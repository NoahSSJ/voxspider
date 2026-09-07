#!/usr/bin/env python3
"""Read-only TOS reconciliation and verified local archived.json registration.

Uses the current core/huggingface.py manifest; never requires local source shards.
Only register writes the registry. Neither command uploads or deletes TOS objects.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from urllib.parse import urlsplit


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_tos(*args):
    result = subprocess.run(['tosutil', *args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=600)
    if result.returncode:
        raise RuntimeError(f'tosutil {args[0]} failed ({result.returncode}): {result.stdout}')
    return result.stdout


def number(output, pattern):
    matches = re.findall(pattern, output, re.MULTILINE | re.IGNORECASE)
    if len(matches) != 1:
        raise ValueError(f'无法唯一解析 tosutil 字段: {pattern}')
    return int(matches[0])


def object_info(uri):
    output = run_tos('stat', uri, '-sbh')
    return {
        'size': number(output, r'^\s*Size:\s*(\d+)\s*$'),
        'crc64': str(number(output, r'^\s*HashCrc64ecma:\s*(\d+)\s*$')),
    }


def normalize_prefix(value):
    uri = urlsplit(value)
    parts = uri.path.strip('/').split('/')
    if (uri.scheme != 'tos' or uri.netloc != 'vox-asr-audio-data'
        or uri.query or uri.fragment or len(parts) != 7
        or parts[:3] != ['SPEECH-DATA-TEMP', 'raw', 'dataset_repository']
        or parts[3] not in ('asr', 'tts') or not re.fullmatch(r'[a-z]{2}|multi', parts[4])
        or not parts[5] or parts[5] in ('.', '..') or '_' in parts[5]
        or any(c.isspace() for c in parts[5]) or parts[6] != 'source'
        or '\\' in value):
        raise ValueError('目标必须是手册规定的 raw/.../{asr|tts}/{lang}/{dataset}/source/ 前缀')
    return value.rstrip('/') + '/'


def load_manifest(path, prefix):
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or not data.get('id') or not data.get('commit_id'):
        raise ValueError('清单必须包含 id 和固定 commit_id')
    entries = data.get('file_manifest')
    if not isinstance(entries, list) or not entries:
        raise ValueError('file_manifest 不能为空')
    expected = {}
    for item in entries:
        relative = item.get('path')
        if (not isinstance(relative, str) or not relative or relative in expected
            or PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts
            or str(PurePosixPath(relative)) != relative
            or any(c in relative for c in '\r\n\t\\') or relative != relative.strip()):
            raise ValueError(f'非法或重复路径: {relative!r}')
        if type(item.get('size')) is not int or item['size'] < 0:
            raise ValueError(f'非法文件大小: {relative}')
        if item.get('local_verified') is not True or item.get('tos_verified') is not True:
            raise ValueError(f'文件尚未完成两端核验: {relative}')
        evidence = item.get('tos_verification') or {}
        local_crc, remote_crc = evidence.get('local_crc64'), evidence.get('remote_crc64')
        if (evidence.get('uri') != prefix + relative or evidence.get('size') != item['size']
            or evidence.get('method') != 'size_crc64'
            or not isinstance(local_crc, str) or not re.fullmatch(r'\d+', local_crc)
            or remote_crc != local_crc or not 0 <= int(local_crc) < 2**64):
            raise ValueError(f'缺少匹配当前目标的完整 CRC64 核验记录: {relative}')
        expected[relative] = {'size': item['size'], 'crc64': str(int(local_crc))}
    return data, expected, hashlib.sha256(raw).hexdigest()


def list_all(prefix):
    # 明确分页，不把默认最多 1000 个对象的结果当作完整仓库。
    marker, found = None, set()
    while True:
        args = ['ls', prefix, '-s', '-bf=raw', '-limit=1000']
        if marker is not None:
            args.append('-marker=' + marker)
        output = run_tos(*args)
        rows = [line.strip() for line in output.splitlines() if line.strip().startswith('tos://')]
        count = number(output, r'File number is:\s*(\d+)')
        if count != len(rows) or count > 1000 or len(set(rows)) != count:
            raise ValueError('TOS 列举不完整、重复或输出格式不兼容')
        if any(not row.startswith(prefix) for row in rows) or found.intersection(rows):
            raise ValueError('TOS 分页包含越界或重复对象')
        found.update(rows)
        if count < 1000:
            break
        # marker 是完整对象 key（不含 bucket），按服务端返回顺序取最后一个。
        next_marker = rows[-1].split('/', 3)[3]
        if marker == next_marker:
            raise ValueError('TOS 分页未推进')
        marker = next_marker
    return {uri[len(prefix):] for uri in found}


def reconcile(manifest, prefix):
    data, expected, fingerprint = load_manifest(manifest, prefix)
    actual = list_all(prefix)
    missing, extra = sorted(expected.keys() - actual), sorted(actual - expected.keys())
    mismatches, errors, checked_bytes = [], [], 0
    for relative in sorted(expected.keys() & actual):
        try:
            info = object_info(prefix + relative)
            checked_bytes += info['size']
            if info != expected[relative]:
                mismatches.append({'path': relative, 'expected': expected[relative], 'actual': info})
        except (ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            errors.append({'path': relative, 'error': str(error)})
    output = run_tos('du', prefix, '-bf=raw')
    du_count = number(output, r'Total object number:\s*(\d+)')
    du_bytes = number(output, r'Total object size:\s*(\d+)B')
    expected_bytes = sum(item['size'] for item in expected.values())
    passed = (not missing and not extra and not mismatches and not errors
              and len(expected) == len(actual) == du_count
              and expected_bytes == checked_bytes == du_bytes)
    if hashlib.sha256(Path(manifest).read_bytes()).hexdigest() != fingerprint:
        raise ValueError('对账期间清单发生变化，请停止写入后重新对账')
    report = dict(passed=passed, checked_at=now(), repo_id=data['id'], commit_id=data['commit_id'],
                  manifest_sha256=fingerprint, tos_prefix=prefix,
                  expected_count=len(expected), listed_count=len(actual), du_count=du_count,
                  expected_bytes=expected_bytes, checked_bytes=checked_bytes, du_bytes=du_bytes,
                  missing=missing, extra=extra, mismatches=mismatches, errors=errors)
    return data, report


def validate_card(path, manifest, prefix):
    # 机器检查结构门槛；完整标签枚举与证据审查由 SKILL.md 要求执行。
    import yaml
    raw = Path(path).read_bytes()
    card = yaml.safe_load(raw)
    required = '''schema_version dataset.id dataset.name dataset.version dataset.split status
source.owner_type source.source_platform lineage.upstream lineage.derivation lineage.manifests.samples
data.num_samples data.total_duration_hours data.languages labels.fixed labels.distribution
labels.unknown labels.availability usage.intended_use usage.not_recommended_for homepage
generation.generated_at'''.split()
    for dotted in required:
        value = card
        for key in dotted.split('.'):
            if not isinstance(value, dict) or key not in value:
                raise ValueError(f'卡片缺少字段: {dotted}')
            value = value[key]
    root_uri = prefix.removesuffix('source/')
    dataset_id = root_uri.rstrip('/').rsplit('/', 1)[1]
    if (card['schema_version'] != 'speech_dataset_spec_v1.2' or card['status'] != 'raw'
        or card['dataset']['id'] != dataset_id
        or card['dataset']['split'] not in ('train', 'dev', 'test', 'all')
        or card['source'] != {'owner_type': 'public', 'source_platform': 'dataset_repository'}
        or card['lineage']['upstream'] != [] or card['lineage']['manifests']['samples'] is not None
        or card['homepage'] != 'https://huggingface.co/datasets/' + manifest['id']):
        raise ValueError('卡片身份、raw 溯源或固定字段不符合规范')
    hours = card['data']['total_duration_hours']
    if type(hours) not in (int, float) or not math.isfinite(hours) or hours < 0:
        raise ValueError('卡片需要有证据支持的数值时长')
    timestamp = card['generation']['generated_at']
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
        raise ValueError('generation.generated_at 必须带时区')
    for key in ('transcript', 'timestamps', 'speaker_labels', 'diarization',
                'pronunciation', 'phoneme', 'prosody'):
        if type(card['labels']['availability'].get(key)) is not bool:
            raise ValueError(f'卡片 availability 缺少布尔值: {key}')
    return raw, root_uri + 'dataset_card.yaml'


def update_registry(path, manifest):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        registry = json.loads(path.read_text()) if path.exists() else {'datasets': []}
        entries = registry.get('datasets')
        if not isinstance(entries, list) or any(not isinstance(e, dict) or not e.get('id')
                or not e.get('commit_id') for e in entries):
            raise ValueError('archived.json 必须包含完整清单组成的 datasets 数组')
        identities = [(entry['id'], entry['commit_id']) for entry in entries]
        if len(set(identities)) != len(identities):
            raise ValueError('archived.json 存在重复的仓库和 commit')
        identity = (manifest['id'], manifest['commit_id'])
        if identity in identities:
            entries[identities.index(identity)] = manifest
        else:
            entries.append(manifest)
        atomic_json(path, registry)
        stored = json.loads(path.read_text())['datasets']
        if [e for e in stored if (e['id'], e['commit_id']) == identity] != [manifest]:
            raise RuntimeError('登记后清单内容不一致')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['reconcile', 'register'])
    parser.add_argument('manifest', type=Path)
    parser.add_argument('tos_prefix')
    parser.add_argument('--card', type=Path)
    parser.add_argument('--registry', type=Path, default=Path('archived.json'))
    parser.add_argument('--report', type=Path, required=True,
                        help='本地审计 JSON 路径；成功或失败均替换此报告')
    args = parser.parse_args()
    protected = {args.manifest.resolve(), args.registry.resolve()}
    if args.card:
        protected.add(args.card.resolve())
    if args.report.resolve() in protected or args.registry.resolve() == args.manifest.resolve():
        parser.error('报告、登记簿、清单和卡片必须使用不同路径')
    report = {'passed': False, 'operation': args.operation, 'checked_at': now()}
    try:
        prefix = normalize_prefix(args.tos_prefix)
        manifest, source_report = reconcile(args.manifest, prefix)
        report['source_reconciliation'] = source_report
        if not source_report['passed']:
            raise ValueError('源对象全量对账失败')
        if args.operation == 'register':
            if (args.card is None or args.card.is_symlink()
                or args.card.resolve() != args.manifest.resolve().parent / 'dataset_card.yaml'):
                raise ValueError('--card 必须是清单同目录下的普通 dataset_card.yaml')
            if args.registry.resolve() == args.card.resolve():
                raise ValueError('登记簿不能覆盖卡片')
            card_raw, card_uri = validate_card(args.card, manifest, prefix)
            output = run_tos('hash', str(args.card), '-type=crc64')
            local_crc = str(number(output, r'^\s*crc64:\s*(\d+)\s*$'))
            remote = object_info(card_uri)
            if remote != {'size': len(card_raw), 'crc64': local_crc}:
                raise ValueError('远端数据集卡与本地卡片不一致，不登记')
            if args.card.read_bytes() != card_raw:
                raise ValueError('卡片在核验期间发生变化')
            if hashlib.sha256(args.manifest.read_bytes()).hexdigest() != source_report['manifest_sha256']:
                raise ValueError('清单在核验期间发生变化')
            report['card_verification'] = dict(uri=card_uri, size=len(card_raw), crc64=local_crc,
                sha256=hashlib.sha256(card_raw).hexdigest(), verified_at=now())
            update_registry(args.registry, manifest)
            report['registry'] = str(args.registry)
        report['passed'] = True
        atomic_json(args.report, report)
        print('PASS')
        return 0
    except Exception as error:
        report['error'] = str(error)
        atomic_json(args.report, report)
        print('FAIL:', error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
