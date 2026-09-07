#!/usr/bin/env python3
"""Read-only TOS audit using current config, manifests and archived.json.

Prints JSON; optionally creates a NEW report. Never uploads, deletes, registers,
or changes manifests. Full local source shards and verified flags are not required.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[4]
# Reuse pagination and prefix validation from the sibling project skill.
HELPER = Path(__file__).resolve().parents[2] / 'hf-filewise-tos-archive/scripts/archive_checks.py'
SPEC = importlib.util.spec_from_file_location('archive_read_helpers', HELPER)
helpers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helpers)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def missing_object(output):
    return bool(re.search(r'\bcode\s*(?:[:=]\s*)?\[?["\']?NoSuchKey\b', output, re.I)
        or (re.search(r'\bStatus\s*\[\s*404\s*\]', output, re.I)
            and re.search(r'\berror message\s*\[\s*0017-00000003\s*\]', output, re.I)))


def stat_object(uri):
    result = subprocess.run(['tosutil', 'stat', uri, '-sbh'], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=120)
    if result.returncode:
        if missing_object(result.stdout):
            return None
        raise RuntimeError(f'TOS 查询失败: {uri}: {result.stdout}')
    size = helpers.number(result.stdout, r'^\s*Size:\s*(\d+)\s*$')
    crc = re.findall(r'^\s*HashCrc64ecma:\s*(\d+)\s*$', result.stdout, re.M | re.I)
    if len(crc) > 1:
        raise ValueError('CRC64 字段重复')
    return {'size': size, 'crc64': str(int(crc[0])) if crc else None}


def inventory(manifest):
    if not isinstance(manifest, dict) or not manifest.get('id') or not manifest.get('commit_id'):
        raise ValueError('清单缺少 id 或 commit_id')
    items = manifest.get('file_manifest')
    if not isinstance(items, list) or not items:
        raise ValueError('file_manifest 为空或无效')
    result = {}
    for item in items:
        relative = item.get('path')
        if (not isinstance(relative, str) or not relative or relative in result
            or PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts
            or str(PurePosixPath(relative)) != relative or relative != relative.strip()
            or any(c in relative for c in '\r\n\t\\')
            or type(item.get('size')) is not int or item['size'] < 0):
            raise ValueError(f'非法清单条目: {relative!r}')
        result[relative] = item
    return result


def evidence_prefix(manifest):
    prefixes = set()
    for item in manifest.get('file_manifest', []):
        evidence = item.get('tos_verification') or {}
        uri, relative = evidence.get('uri'), item.get('path')
        if isinstance(uri, str) and isinstance(relative, str) and relative and uri.endswith('/' + relative):
            prefixes.add(helpers.normalize_prefix(uri[:-len(relative)]))
    if len(prefixes) > 1:
        raise ValueError('清单记录了多个不同 TOS 前缀')
    return next(iter(prefixes), None)


def discover(config_path, datasets_dir, registry_path, selected):
    config = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    configured = (config or {}).get('datasets') or []
    by_name = {}
    for item in configured:
        name = item['name']
        if name in by_name or not name or name in ('.', '..') or '/' in name or '\\' in name:
            raise ValueError(f'配置存在重复或非法 name: {name!r}')
        by_name[name] = item
    registry = read_json(registry_path) if registry_path.exists() else {'datasets': []}
    archived = registry.get('datasets')
    if not isinstance(archived, list):
        raise ValueError('archived.json 缺少 datasets 数组')
    identities = []
    for entry in archived:
        if not isinstance(entry, dict) or not entry.get('id') or not entry.get('commit_id'):
            raise ValueError('archived.json 含无效清单')
        identities.append((entry['id'], entry['commit_id']))
    if len(set(identities)) != len(identities):
        raise ValueError('archived.json 包含重复 id + commit_id')
    paths = {p.parent.name: p for p in datasets_dir.glob('*/repo_manifest.json')}
    jobs, seen = [], set()
    for name in sorted(set(paths) | set(by_name)):
        cfg = by_name.get(name, {})
        path = paths.get(name, datasets_dir / name / 'repo_manifest.json')
        data, error = None, None
        if path.exists():
            try:
                data = read_json(path)
                if not isinstance(data, dict):
                    raise ValueError('清单不是 JSON 对象')
            except Exception as exc:
                error = str(exc)
        else:
            error = '本地 repo_manifest.json 不存在'
        repo_id = data.get('id') if data else cfg.get('repo_id')
        if data:
            seen.add((repo_id, data.get('commit_id')))
        jobs.append(dict(dataset=name, repo_id=repo_id, manifest=data, config=cfg,
                         manifest_path=str(path), local_dir=str(path.parent), error=error))
    for data in archived:
        if (data['id'], data['commit_id']) not in seen:
            jobs.append(dict(dataset=data['id'], repo_id=data['id'], manifest=data,
                             config={}, manifest_path=None, local_dir=None, error=None))
    if selected:
        matches = []
        for selection in selected:
            found = [job for job in jobs if selection in (job['dataset'], job['repo_id'])]
            if not found:
                raise ValueError(f'没有精确匹配的数据集: {selection}')
            for job in found:
                if job not in matches:
                    matches.append(job)
        jobs = matches
    return jobs, archived


def audit(job, archived):
    data = job['manifest']
    result = dict(dataset=job['dataset'], repo_id=job['repo_id'],
        commit_id=data.get('commit_id') if data else None, manifest_path=job['manifest_path'],
        upload_complete=None, reconciliation_complete=None, checksum_complete=None,
        datacard_uploaded=None, datacard_matches_local=None, archived_registered=False,
        archived_manifest_matches=None, needs_review=False, issues=[])
    if data:
        matches = [entry for entry in archived if (entry['id'], entry['commit_id']) ==
                   (data.get('id'), data.get('commit_id'))]
        result['archived_registered'] = bool(matches)
        result['archived_manifest_matches'] = matches[0] == data if matches else None
    try:
        if job['error']:
            raise ValueError(job['error'])
        expected = inventory(data)
        cfg = job['config']
        if cfg.get('repo_id') and cfg['repo_id'] != data['id']:
            raise ValueError('配置 repo_id 与清单 id 不一致')
        configured = helpers.normalize_prefix(cfg['tos_url']) if cfg.get('tos_url') else None
        evidence = evidence_prefix(data)
        if configured and evidence and configured != evidence:
            raise ValueError('配置 TOS 前缀与清单历史核验目标不一致')
        prefix = configured or evidence
        if not prefix:
            raise ValueError('配置和核验记录均无明确 TOS 目标，不能猜测')
        result.update(tos_prefix=prefix, expected_count=len(expected),
            expected_bytes=sum(i['size'] for i in expected.values()),
            local_verified_count=sum(i.get('local_verified') is True for i in expected.values()),
            recorded_tos_verified_count=sum(i.get('tos_verified') is True for i in expected.values()))
        original = Path(job['manifest_path']).read_bytes() if job['manifest_path'] else None
        if original is not None and json.loads(original) != data:
            raise ValueError('清单在发现数据集后发生变化，请重新查询')
        try:
            actual = helpers.list_all(prefix)
            missing, extra = sorted(expected.keys() - actual), sorted(actual - expected.keys())
            size_mismatches, crc_mismatches, unavailable, errors = [], [], [], []
            checked_bytes = 0
            for relative in sorted(expected.keys() & actual):
                item = expected[relative]
                try:
                    remote = stat_object(prefix + relative)
                    if remote is None:
                        missing.append(relative)
                        continue
                    checked_bytes += remote['size']
                    if remote['size'] != item['size']:
                        size_mismatches.append(relative)
                    proof = item.get('tos_verification') or {}
                    crc = proof.get('local_crc64')
                    valid_proof = (item.get('local_verified') is True and item.get('tos_verified') is True
                        and proof.get('uri') == prefix + relative and proof.get('size') == item['size']
                        and proof.get('method') == 'size_crc64' and isinstance(crc, str)
                        and re.fullmatch(r'\d+', crc) and 0 <= int(crc) < 2**64
                        and proof.get('remote_crc64') == crc)
                    if not valid_proof or remote['crc64'] is None:
                        unavailable.append(relative)
                    elif str(int(crc)) != remote['crc64']:
                        crc_mismatches.append(relative)
                except Exception as exc:
                    errors.append({'path': relative, 'error': str(exc)})
            du = helpers.run_tos('du', prefix, '-bf=raw')
            count = helpers.number(du, r'Total object number:\s*(\d+)')
            total = helpers.number(du, r'Total object size:\s*(\d+)B')
            result.update(actual_count=len(actual), actual_bytes=total, du_count=count,
                missing=sorted(set(missing)), extra=extra, size_mismatches=size_mismatches,
                crc_mismatches=crc_mismatches, checksum_unavailable=unavailable, stat_errors=errors)
            result['upload_complete'] = False if missing else (None if errors else True)
            known_size_failure = bool(missing or extra or size_mismatches or count != len(expected)
                                      or total != result['expected_bytes'])
            result['reconciliation_complete'] = (False if known_size_failure else None if errors else
                len(actual) == count and checked_bytes == total)
            result['checksum_complete'] = (False if missing or crc_mismatches else
                                           None if errors or unavailable else True)
            if errors or unavailable:
                result['needs_review'] = True
            if count != len(actual):
                result['issues'].append('列举与 du 数量不一致，远端可能正在变化')
                result['needs_review'] = True
        except Exception as exc:
            result['issues'].append('源对象查询失败: ' + str(exc))
            result['needs_review'] = True
        # 卡片状态独立查询，源文件未完成也要能发现提前发布的卡片。
        try:
            card_uri = prefix.removesuffix('source/') + 'dataset_card.yaml'
            remote_card = stat_object(card_uri)
            result['datacard_uploaded'] = remote_card is not None and remote_card['size'] > 0
            result['datacard_uri'] = card_uri
            if remote_card is not None:
                result['datacard_size'] = remote_card['size']
                local = Path(job['local_dir']) / 'dataset_card.yaml' if job['local_dir'] else None
                if local and local.is_file() and not local.is_symlink():
                    before = local.read_bytes()
                    crc_output = helpers.run_tos('hash', str(local), '-type=crc64')
                    crc = str(helpers.number(crc_output, r'^\s*crc64:\s*(\d+)\s*$'))
                    if before != local.read_bytes():
                        raise ValueError('查询期间本地卡片变化')
                    if remote_card['crc64'] is not None:
                        result['datacard_matches_local'] = remote_card == {'size': len(before), 'crc64': crc}
                    result['local_card_sha256'] = hashlib.sha256(before).hexdigest()
        except Exception as exc:
            result['issues'].append('卡片查询失败: ' + str(exc))
            result['needs_review'] = True
        if original is not None and Path(job['manifest_path']).read_bytes() != original:
            result.update(upload_complete=None, reconciliation_complete=None, checksum_complete=None,
                          needs_review=True)
            result['issues'].append('查询期间本地清单变化，重新查询以获取一致状态')
    except Exception as exc:
        result['issues'].append(str(exc))
        result['needs_review'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/hgf.yaml')
    parser.add_argument('--datasets-dir', type=Path, default=ROOT / 'datasets')
    parser.add_argument('--registry', type=Path, default=ROOT / 'archived.json')
    parser.add_argument('--dataset', action='append', default=[], help='精确本地目录名或完整 repo ID，可重复')
    parser.add_argument('--json-output', type=Path, help='可选：新建报告文件，拒绝覆盖已有文件')
    args = parser.parse_args()
    try:
        jobs, archived = discover(args.config, args.datasets_dir, args.registry, args.dataset)
        results = [audit(job, archived) for job in jobs]
        report = dict(generated_at=helpers.now(), results=results)
        serialized = json.dumps(report, ensure_ascii=False, indent=2)
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            with args.json_output.open('x', encoding='utf-8') as output:
                output.write(serialized + '\n')
        print(serialized)
        return 2 if any(row['needs_review'] for row in results) else 0
    except Exception as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
