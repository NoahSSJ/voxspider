"""逐文件归档 Hugging Face 仓库；兼容已有 repo_manifest.json。

安装: pip install huggingface_hub tos
运行: python core/huggingface_transfer.py --repo-id TCDATA/ruozhiba \
    --name 弱智吧语录 --tos-uri tos://BUCKET/PREFIX/source/ \
    --tos-endpoint https://tos-cn-beijing.volces.com --tos-region cn-beijing
环境变量: HF_TOKEN（可选）、TOS_ACCESS_KEY、TOS_SECRET_KEY、TOS_SECURITY_TOKEN（可选）。
严格串行：下载 → 本地核验 → 上传 → 远端核验 → 原子保存 → 删除。
只处理 raw 文件；全量对账通过后仍需人工准备并最后发布 dataset_card.yaml。
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_save(path, data):
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def safe_path(root, relative):
    parts = PurePosixPath(relative)
    if (not relative or parts.is_absolute() or '..' in parts.parts
            or '\\' in relative or str(parts) != relative
            or parts.parts[0] == '.cache'):
        raise ValueError(f'不安全或保留的上游路径: {relative!r}')
    target = root / relative
    for parent in [target, *target.parents]:
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise ValueError(f'不允许符号链接: {parent}')
    return target


def digest(stream, size):
    from tos.utils import Crc64
    sha = hashlib.sha256()
    blob = hashlib.sha1(f'blob {size}\0'.encode('ascii'))
    crc = Crc64()
    count = 0
    while chunk := stream.read(4 * 1024 * 1024):
        count += len(chunk)
        sha.update(chunk)
        blob.update(chunk)
        crc.update(chunk)
    return dict(size=count, sha256=sha.hexdigest(), blob_id=blob.hexdigest(),
                crc64=str(crc.crc))


class HuggingfaceTransfer:
    def __init__(self, repo_id, save_dir, client, bucket, prefix,
                 endpoint='https://huggingface.co', no_progress_limit=8,
                 reserve_bytes=1024**3):
        self.repo_id, self.save_dir = repo_id, Path(save_dir)
        self.client, self.bucket, self.prefix = client, bucket, prefix.rstrip('/') + '/'
        self.endpoint = endpoint
        self.no_progress_limit, self.reserve_bytes = no_progress_limit, reserve_bytes
        self.source = self.save_dir / 'source'
        self.manifest_path = self.save_dir / 'repo_manifest.json'
        self.manifest = None

    def save(self):
        atomic_save(self.manifest_path, self.manifest)

    def load_manifest(self):
        from huggingface_hub import HfApi, RepoFile
        if self.manifest_path.exists():
            data = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        else:
            api = HfApi(endpoint=self.endpoint, token=os.getenv('HF_TOKEN'))
            info = api.dataset_info(self.repo_id)
            data = dict(id=self.repo_id, commit_id=info.sha,
                        card_data=info.card_data.to_dict() if info.card_data else {},
                        file_manifest=[])
            for item in api.list_repo_tree(self.repo_id, repo_type='dataset',
                                           revision=info.sha, recursive=True):
                if isinstance(item, RepoFile):
                    data['file_manifest'].append(dict(path=item.path, size=item.size,
                        blob_id=item.blob_id,
                        sha256=item.lfs.sha256 if item.lfs else None))
        if data['id'] != self.repo_id or not data.get('commit_id'):
            raise ValueError('清单仓库身份不符或缺少固定 commit_id')
        target = dict(bucket=self.bucket, prefix=self.prefix)
        if data.get('tos_target', target) != target:
            raise ValueError('TOS 目标与已有清单不符，请使用独立工作目录')
        seen = set()
        for item in data['file_manifest']:
            safe_path(self.source, item['path'])
            if item['path'] in seen or type(item['size']) is not int or item['size'] < 0:
                raise ValueError('清单包含重复路径或非法大小')
            seen.add(item['path'])
            for key in ('local_verified', 'tos_verified', 'local_deleted'):
                item.setdefault(key, False)
            for key in ('local_verification', 'tos_verification', 'last_error'):
                item.setdefault(key, None)
        if not seen:
            raise ValueError('文件清单为空，不能宣告完成')
        data.update(schema_version=2, tos_target=target, transfer_complete=False)
        self.manifest = data
        self.save()

    def identity(self, item):
        return dict(repo_id=self.repo_id, commit_id=self.manifest['commit_id'],
                    **{k: item.get(k) for k in ('path', 'size', 'sha256', 'blob_id')})

    def head(self, key):
        from tos.exceptions import TosServerError
        try:
            return self.client.head_object(self.bucket, key)
        except TosServerError as error:
            if error.status_code == 404 and error.code in ('NoSuchKey', 'NotFound'):
                return None
            raise

    def download(self, item, path):
        from huggingface_hub import hf_hub_download
        # HF 在 local_dir 下维护 .incomplete，失败重试时保留以便续传。
        def progress():
            return sum(p.stat().st_size for p in self.source.rglob('*.incomplete')
                       if p.is_file())
        stalled, previous = 0, progress()
        while True:
            try:
                hf_hub_download(self.repo_id, filename=item['path'], repo_type='dataset',
                    revision=self.manifest['commit_id'], local_dir=self.source,
                    endpoint=self.endpoint, token=os.getenv('HF_TOKEN'))
                return
            except Exception as error:
                status = getattr(getattr(error, 'response', None), 'status_code', None)
                if status in (400, 401, 403, 404):
                    raise
                current = progress()
                stalled = 0 if current > previous else stalled + 1
                previous = current
                if stalled >= self.no_progress_limit:
                    raise
                LOG.warning('下载中断，连续无进展次数 %s，稍后续传', stalled)
                time.sleep(min(30, 2 ** min(stalled, 5)))

    def verify_local(self, item, path):
        with path.open('rb') as stream:
            result = digest(stream, item['size'])
        if result['size'] != item['size']:
            raise ValueError(f"本地大小不符: {item['path']}")
        method = 'size_only'
        for algorithm in ('sha256', 'blob_id'):
            if item.get(algorithm):
                if result[algorithm] != item[algorithm]:
                    raise ValueError(f"本地 {algorithm} 不符: {item['path']}")
                method = algorithm
                break
        result.update(method=method, verified_at=now(), identity=self.identity(item))
        return result

    def verify_remote(self, item, expected, head):
        key = self.prefix + item['path']
        if head is None or head.content_length != item['size']:
            raise ValueError(f'TOS 对象缺失或大小不符: {key}')
        crc = getattr(head, 'hash_crc64_ecma', None)
        method = 'size_crc64'
        if crc is not None:
            if str(crc) != expected['crc64']:
                raise ValueError(f'TOS CRC64 不符，禁止覆盖: {key}')
        else:
            # 完整流式读回，仅占用固定内存，不再生成本地副本。
            remote = self.client.get_object(self.bucket, key, if_match=head.etag)
            result = digest(remote, item['size'])
            if result['size'] != expected['size'] or result['sha256'] != expected['sha256']:
                raise ValueError(f'TOS SHA256 不符，禁止覆盖: {key}')
            method = 'size_sha256_readback'
        return dict(bucket=self.bucket, key=key, size=head.content_length,
                    crc64=str(crc) if crc is not None else None, etag=head.etag,
                    version_id=getattr(head, 'version_id', None), method=method,
                    verified_at=now())

    def process(self, item):
        from tos.models2 import GenericInput
        path = safe_path(self.source, item['path'])
        key = self.prefix + item['path']
        stage = 'local_verify'
        try:
            expected = item.get('local_verification')
            reusable = (item['local_verified'] and expected
                        and expected.get('identity') == self.identity(item)
                        and all(k in expected for k in ('sha256', 'size', 'crc64')))
            item['tos_verified'] = False
            item['tos_verification'] = None
            if not reusable:
                item['local_verified'] = False
                item['local_verification'] = None
            self.save()
            head = self.head(key)
            if not reusable or (not path.exists() and head is None):
                item['local_verified'] = False
                item['local_deleted'] = False
                self.save()
                if not path.exists():
                    stage = 'download'
                    if shutil.disk_usage(self.source).free < item['size'] + self.reserve_bytes:
                        raise OSError('磁盘空间不足以容纳当前文件及预留空间')
                    self.download(item, path)
                stage = 'local_verify'
                expected = self.verify_local(item, path)
                item.update(local_verified=True, local_verification=expected)
                self.save()
            elif path.exists():
                item.update(local_verified=False, local_deleted=False)
                self.save()
                expected = self.verify_local(item, path)
                item.update(local_verified=True, local_verification=expected)
                self.save()
            if head is None:
                stage = 'upload'
                self.client.upload_file(self.bucket, key, str(path), task_num=1,
                    enable_checkpoint=True,
                    generic_input=GenericInput(request_headers={'x-tos-forbid-overwrite': 'true'}))
                head = self.head(key)
            stage = 'tos_verify'
            verification = self.verify_remote(item, expected, head)
            item.update(tos_verified=True, tos_verification=verification, last_error=None)
            self.save()  # 保存失败绝不删除本地文件。
            stage = 'cleanup'
            safe_path(self.source, item['path']).unlink(missing_ok=True)
            item['local_deleted'] = True
            self.save()
            LOG.info('已核验并清理: %s', item['path'])
        except Exception as error:
            item['last_error'] = dict(stage=stage, type=type(error).__name__,
                                      message=str(error), occurred_at=now())
            self.save()
            raise

    def reconcile(self):
        expected = {self.prefix + i['path']: i['size'] for i in self.manifest['file_manifest']}
        actual, token = {}, None
        while True:
            page = self.client.list_objects_type2(self.bucket, prefix=self.prefix,
                continuation_token=token, max_keys=1000, list_only_once=True)
            actual.update((obj.key, obj.size) for obj in page.contents)
            if not page.is_truncated:
                break
            next_token = page.next_continuation_token
            if not next_token or next_token == token:
                raise RuntimeError('TOS 列举分页未推进')
            token = next_token
        report = dict(missing=sorted(expected.keys() - actual.keys()),
                      extra=sorted(actual.keys() - expected.keys()),
                      size_mismatch=sorted(k for k in expected.keys() & actual.keys()
                                           if expected[k] != actual[k]),
                      expected_bytes=sum(expected.values()), actual_bytes=sum(actual.values()),
                      expected_files=len(expected), actual_files=len(actual), checked_at=now())
        self.manifest['reconciliation'] = report
        self.manifest['transfer_complete'] = not any(report[k] for k in ('missing', 'extra', 'size_mismatch'))
        self.save()
        if not self.manifest['transfer_complete']:
            raise ValueError(f'全量对账失败: {report}')
        LOG.info('全量对账通过: %s 个文件，%s 字节', len(expected), sum(expected.values()))

    def run(self):
        self.save_dir.mkdir(parents=True, exist_ok=True)
        if self.save_dir.is_symlink() or self.source.is_symlink():
            raise ValueError('工作目录不能是符号链接')
        self.source.mkdir(exist_ok=True)
        with (self.save_dir / '.transfer.lock').open('a') as lock:
            # 同一工作目录仅一个进程写清单；退出/崩溃自动释放。
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.load_manifest()
            for item in self.manifest['file_manifest']:
                self.process(item)
            self.reconcile()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repo-id', required=True)
    parser.add_argument('--name', required=True, help='datasets 下的本地目录名')
    parser.add_argument('--tos-uri', required=True, help='明确指定 tos://bucket/.../source/')
    parser.add_argument('--tos-endpoint', required=True)
    parser.add_argument('--tos-region', required=True)
    parser.add_argument('--hf-endpoint', default='https://huggingface.co')
    parser.add_argument('--no-progress-limit', type=int, default=8)
    args = parser.parse_args()
    target = urlsplit(args.tos_uri)
    prefix = target.path.lstrip('/').rstrip('/')
    if (target.scheme != 'tos' or not target.netloc or not prefix.endswith('/source')
            or target.query or target.fragment or '..' in PurePosixPath(prefix).parts):
        parser.error('--tos-uri 必须是 tos://bucket/.../source/')
    if args.name in ('.', '..') or '/' in args.name or '\\' in args.name or args.no_progress_limit < 1:
        parser.error('非法 name 或 no-progress-limit')
    import tos
    client = tos.TosClientV2(os.environ['TOS_ACCESS_KEY'], os.environ['TOS_SECRET_KEY'],
        args.tos_endpoint, args.tos_region, security_token=os.getenv('TOS_SECURITY_TOKEN'), enable_crc=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        HuggingfaceTransfer(args.repo_id, ROOT / 'datasets' / args.name,
            client, target.netloc, prefix, args.hf_endpoint, args.no_progress_limit).run()
    finally:
        client.close()


if __name__ == '__main__':
    main()
