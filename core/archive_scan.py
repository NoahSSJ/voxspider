"""Scan ready manifests, generate evidence-backed cards, publish, and register.

Run: python -m core.archive_scan [--dry-run | --initialize | --resume NAME]
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import traceback

import yaml
from . import archive_state as state

ROOT = Path(__file__).resolve().parents[1]
SKILL_CHECKS = ROOT / '.agents/skills/hf-filewise-tos-archive/scripts/archive_checks.py'
spec = importlib.util.spec_from_file_location('archive_checks', SKILL_CHECKS)
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class ManualReview(Exception):
    pass


class RetryLater(Exception):
    pass


def read(path):
    if path.is_symlink():
        raise ManualReview(f'不处理符号链接: {path}')
    return json.loads(path.read_text())


def log(message):
    print(f'{state.now()} {message}', flush=True)


def prefix_for(root, path, manifest):
    config = yaml.safe_load((root / 'config/hgf.yaml').read_text())
    entries = [x for x in config['datasets']
               if x.get('name') == path.parent.name and x.get('repo_id') == manifest.get('id')]
    if len(entries) != 1:
        raise ManualReview('清单与 config/hgf.yaml 的 name/repo_id 无法唯一匹配')
    return checks.normalize_prefix(entries[0]['tos_url'])


def eligible(manifest):
    return (manifest.get('status') is True and manifest.get('is_archived') is not True
            and manifest.get('needs_manual_review') is not True)


def remember_error(path, manifest, error, manual):
    # 出错时重新读取，不能把旧快照覆盖到其他写入者保存的新清单上。
    manifest = read(path)
    state.initialize(manifest)
    manifest['needs_manual_review'] = manual
    manifest['last_error'] = {'at': state.now(), 'kind': 'manual_review' if manual else 'retry',
                              'message': str(error)}
    state.atomic_json(path, manifest)


def reconcile(path, prefix):
    try:
        _, report = checks.reconcile(path, prefix)
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
        raise RetryLater(str(error)) from error
    state.atomic_json(path.parent / 'source_reconciliation.json',
                      {'passed': report['passed'], 'operation': 'reconcile',
                       'checked_at': state.now(), 'source_reconciliation': report})
    if report['missing'] or report['extra'] or report['mismatches']:
        raise ManualReview('源全量对账有缺失、多余或大小/CRC64 差异，见 source_reconciliation.json')
    if not report['passed']:
        raise RetryLater('源全量对账查询失败，见 source_reconciliation.json')
    return report


def run_child(command, prompt, output_log, timeout):
    # 独立进程组确保超时后连同其工具进程退出，锁在退出前不会释放。
    with output_log.open('w') as stream:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stream,
                                 stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            child.communicate(prompt, timeout=timeout)
        except BaseException:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            raise
    return child.returncode


def generate_card(root, path, manifest, prefix, settings):
    decision_path = path.parent / 'card_generation_decision.json'
    if decision_path.exists():
        decision_path.unlink()
    prompt = (root / 'docs/archive-card-task.md').read_text() + '\n本次输入（JSON 数据）：\n' + json.dumps(
        {'manifest_path': str(path), 'repo_id': manifest['id'], 'commit_id': manifest['commit_id'],
         'tos_prefix': prefix, 'card_path': str(path.parent / 'dataset_card.yaml')}, ensure_ascii=False)
    run_log = root / 'logs/archive_scan' / f'{path.parent.name}-generation.log'
    command = [settings['codex'], 'exec', '--approve-for-me', '-C', str(root),
               '--output-schema', str(root / 'config/archive_card_result.schema.json'),
               '--output-last-message', str(decision_path), '--color', 'never', '-']
    before = path.read_bytes()
    try:
        code = run_child(command, prompt, run_log, settings['generation_timeout_seconds'])
    except subprocess.TimeoutExpired as error:
        raise RetryLater('数据卡任务执行超时，下轮重试') from error
    if path.read_bytes() != before:
        raise ManualReview('生成任务期间清单发生变化，停止交付；检查是否有未使用锁的旧下载进程')
    if code != 0:
        raise RetryLater(f'Codex 退出码 {code}，见 {run_log}')
    if not decision_path.is_file():
        raise RetryLater(f'Codex 未返回结构化结果，见 {run_log}')
    result = read(decision_path)
    if result.get('outcome') == 'manual_review':
        raise ManualReview(result.get('reason') or '生成器请求人工检查')
    if result.get('outcome') == 'retry':
        raise RetryLater(result.get('reason') or '生成器请求重试')
    if result.get('outcome') != 'ready':
        raise ManualReview('生成器返回了无效的处理结论')
    if not (path.parent / 'card_generation_report.json').is_file():
        raise ManualReview('缺少卡片字段证据审查报告 card_generation_report.json')
    raw, _ = checks.validate_card(path.parent / 'dataset_card.yaml', manifest, prefix)
    manifest['is_datasetcard_gen_ok'] = True
    manifest['datasetcard_generation'] = {
        'card_sha256': hashlib.sha256(raw).hexdigest(), 'source_fingerprint': state.source_fingerprint(manifest),
        'reviewed_at': state.now(), 'reason': result['reason']}
    state.atomic_json(path, manifest)


def validated_card(path, manifest, prefix):
    card_path = path.parent / 'dataset_card.yaml'
    if card_path.is_symlink():
        raise ManualReview('数据卡不能是符号链接')
    raw, uri = checks.validate_card(card_path, manifest, prefix)
    proof = manifest.get('datasetcard_generation') or {}
    if (proof.get('card_sha256') != hashlib.sha256(raw).hexdigest()
            or proof.get('source_fingerprint') != state.source_fingerprint(manifest)):
        raise ManualReview('卡片内容或源清单与生成审查记录不一致，需重新审查')
    return raw, uri


def publish(path, manifest, prefix):
    raw, uri = validated_card(path, manifest, prefix)
    card = path.parent / 'dataset_card.yaml'
    crc = str(checks.number(checks.run_tos('hash', str(card), '-type=crc64'), r'^\s*crc64:\s*(\d+)\s*$'))
    expected = {'size': len(raw), 'crc64': crc}
    result = subprocess.run(['tosutil', 'stat', uri, '-sbh'], capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        actual = {'size': checks.number(result.stdout, r'^\s*Size:\s*(\d+)\s*$'),
                  'crc64': str(checks.number(result.stdout, r'^\s*HashCrc64ecma:\s*(\d+)\s*$'))}
        if actual != expected:
            raise ManualReview('远端已存在不同内容的数据卡，停止且不覆盖')
    else:
        output = result.stdout + result.stderr
        missing = ('NoSuchKey' in output or
                   'Status [404], error code [], error message [0017-00000003]' in output)
        if not missing:
            raise RetryLater('远端卡片查询失败: ' + output)
        if card.read_bytes() != raw:
            raise ManualReview('卡片在发布前发生变化')
        checks.run_tos('cp', str(card), uri, '-vchecksum')
    if checks.object_info(uri) != expected or card.read_bytes() != raw:
        raise ManualReview('发布后卡片内容校验不一致')
    manifest['is_datasetcard_upload_ok'] = True
    manifest['datasetcard_upload'] = dict(uri=uri, **expected,
        sha256=hashlib.sha256(raw).hexdigest(), verified_at=state.now())
    state.atomic_json(path, manifest)
    state.atomic_json(path.parent / 'card_publication.json',
                      {'passed': True, **manifest['datasetcard_upload']})


def register(root, path, manifest, prefix):
    # 调用 skill 的 register：重新核验源对象和卡片，先完成登记门槛。
    command = [sys.executable, str(SKILL_CHECKS), 'register', str(path), prefix,
               '--card', str(path.parent / 'dataset_card.yaml'), '--registry', str(root / 'archived.json'),
               '--report', str(path.parent / 'archive_report.json')]
    result = subprocess.run(command, capture_output=True, text=True)
    report = read(path.parent / 'archive_report.json')
    if result.returncode or report.get('passed') is not True:
        source = report.get('source_reconciliation') or {}
        if source.get('missing') or source.get('extra') or source.get('mismatches'):
            raise ManualReview('登记前源对象对账出现差异，见 archive_report.json')
        raise RetryLater('登记未成功: ' + str(report.get('error', result.stdout)))
    final = copy.deepcopy(manifest)
    final['is_archived'] = True
    final['needs_manual_review'] = False
    final['last_error'] = None
    # 先持久登记最终快照，再标记本地完成；中断时本地仍为 false，可重试。
    checks.update_registry(root / 'archived.json', final)
    state.atomic_json(path, final)
    manifest.clear()
    manifest.update(final)
    report['registered_manifest_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    state.atomic_json(path.parent / 'archive_report.json', report)


def process(root, path, settings):
    initial = read(path)
    if not eligible(initial):
        return 'skipped'
    try:
        prefix = prefix_for(root, path, initial)
    except (ManualReview, ValueError, KeyError) as error:
        with state.dataset_lock(path.parent):
            remember_error(path, initial, error, True)
        return 'manual_review'
    with state.dataset_lock(path.parent, prefix):
        manifest = read(path)
        if not eligible(manifest):
            return 'skipped'
        state.initialize(manifest)
        try:
            if not state.transfer_complete(manifest, prefix):
                manifest['status'] = False
                state.atomic_json(path, manifest)
                raise ManualReview('status=true 但文件两端完整核验记录不匹配')
            log(f'{path.parent.name}: 开始全量对账')
            reconcile(path, prefix)
            if manifest['is_datasetcard_gen_ok'] is not True:
                log(f'{path.parent.name}: 按 skill 生成并审查数据卡')
                generate_card(root, path, manifest, prefix, settings)
            validated_card(path, manifest, prefix)
            # 生成可能耗时较长，发布前重新对账；不会重跑已生成的卡片。
            reconcile(path, prefix)
            log(f'{path.parent.name}: 发布/复核数据卡并登记')
            publish(path, manifest, prefix)
            register(root, path, manifest, prefix)
            return 'archived'
        except ManualReview as error:
            remember_error(path, manifest, error, True)
            return 'manual_review'
        except (RetryLater, OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            remember_error(path, manifest, error, False)
            return 'retry'
        except (ValueError, KeyError, TypeError) as error:
            remember_error(path, manifest, error, True)
            return 'manual_review'


def initialize_historical(root, path):
    manifest = read(path)
    prefix = prefix_for(root, path, manifest)
    with state.dataset_lock(path.parent, prefix):
        before = path.read_bytes()
        manifest = json.loads(before)
        changed = state.initialize(manifest)
        if not changed:
            return 'already_initialized'
        manifest['status'] = state.transfer_complete(manifest, prefix)
        report_path = path.parent / 'archive_report.json'
        if manifest['status'] and report_path.exists():
            report = read(report_path)
            proof = report.get('card_verification') or {}
            source = report.get('source_reconciliation') or {}
            if (report.get('passed') is True and source.get('manifest_sha256') == hashlib.sha256(before).hexdigest()
                    and source.get('passed') is True):
                raw, uri = checks.validate_card(path.parent / 'dataset_card.yaml', manifest, prefix)
                if proof.get('sha256') == hashlib.sha256(raw).hexdigest() and proof.get('uri') == uri:
                    manifest['is_datasetcard_gen_ok'] = True
                    manifest['is_datasetcard_upload_ok'] = True
                    manifest['datasetcard_generation'] = dict(card_sha256=proof['sha256'],
                        source_fingerprint=state.source_fingerprint(manifest), reviewed_at=proof['verified_at'],
                        reason='复用历史完整归档报告及已核验的本地卡片')
                    manifest['datasetcard_upload'] = proof
        state.atomic_json(path, manifest)
        return 'initialized'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--dry-run', action='store_true')
    group.add_argument('--initialize', metavar='DATASET', help='为指定历史清单补默认字段；必须先停止其旧下载进程')
    group.add_argument('--resume', metavar='DATASET', help='解除指定数据集的人工暂停，下次扫描重试')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    root = ROOT
    log_dir = root / 'logs/archive_scan'
    log_dir.mkdir(parents=True, exist_ok=True)
    settings = json.loads((root / 'config/archive_schedule.json').read_text())
    try:
        with state.file_lock(log_dir / 'scan.lock'):
            results = []
            for path in sorted((root / 'datasets').glob('*/repo_manifest.json')):
                if path.parent.is_symlink():
                    continue
                if (args.resume or args.initialize) and path.parent.name != (args.resume or args.initialize):
                    continue
                try:
                    manifest = read(path)
                    if args.dry_run:
                        outcome = 'ready' if eligible(manifest) else 'skipped'
                    elif args.initialize:
                        outcome = initialize_historical(root, path)
                    elif args.resume:
                        prefix = prefix_for(root, path, manifest)
                        with state.dataset_lock(path.parent, prefix):
                            manifest = read(path)
                            manifest['needs_manual_review'] = False
                            manifest['last_error'] = None
                            state.atomic_json(path, manifest)
                        outcome = 'resumed'
                    else:
                        outcome = process(root, path, settings)
                    results.append({'dataset': path.parent.name, 'outcome': outcome})
                except BlockingIOError:
                    results.append({'dataset': path.parent.name, 'outcome': 'busy'})
                except Exception as error:
                    # 损坏清单/配置无法安全写回，在扫描报告保留原因并继续其他数据集。
                    results.append({'dataset': path.parent.name, 'outcome': 'error', 'error': str(error)})
                    log(traceback.format_exc())
                log(str(results[-1]))
            if (args.resume or args.initialize) and not results:
                parser.error('未找到指定数据集')
            state.atomic_json(log_dir / ('dry_run.json' if args.dry_run else 'last_scan.json'),
                              {'scanned_at': state.now(), 'results': results})
    except BlockingIOError:
        log('上一轮仍在运行，本轮跳过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
