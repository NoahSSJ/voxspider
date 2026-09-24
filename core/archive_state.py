"""Durable dataset lifecycle state shared by the downloader and archive scanner."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

DEFAULTS = dict(status=False, is_datasetcard_gen_ok=False,
                is_datasetcard_upload_ok=False, is_archived=False,
                needs_manual_review=False, last_error=None)


def now():
    return datetime.now(timezone.utc).isoformat()


def initialize(manifest):
    changed = False
    for key, value in DEFAULTS.items():
        if key not in manifest:
            manifest[key] = value
            changed = True
    return changed


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class LockBusyError(BlockingIOError):
    """Another worker holds the archive lock; the task may be retried."""


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusyError(exc.errno, "Archive lock is held by another worker", str(path)) from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def dataset_lock(directory, prefix=None):
    directory = Path(directory)
    with file_lock(directory / '.archive.lock'):
        if prefix:
            key = hashlib.sha256(prefix.rstrip('/').encode()).hexdigest()
            with file_lock(directory.parent / '.archive-locks' / (key + '.lock')):
                yield
        else:
            yield


def entry_complete(manifest, item, prefix):
    proof = item.get('tos_verification') or {}
    identity = dict(repo_id=manifest.get('id'), commit_id=manifest.get('commit_id'),
                    **{key: item.get(key) for key in ('path', 'size', 'sha256', 'blob_id')})
    crc = proof.get('local_crc64')
    return (item.get('local_verified') is True and item.get('tos_verified') is True
            and proof.get('identity') == identity
            and proof.get('uri') == prefix.rstrip('/') + '/' + item['path']
            and type(item.get('size')) is int and item['size'] >= 0
            and proof.get('size') == item['size'] and proof.get('method') == 'size_crc64'
            and isinstance(crc, str) and re.fullmatch(r'\d{1,20}', crc) is not None
            and int(crc) < 2**64 and proof.get('remote_crc64') == crc)


def transfer_complete(manifest, prefix):
    items = manifest.get('file_manifest')
    return (bool(manifest.get('id')) and bool(manifest.get('commit_id'))
            and isinstance(items, list) and bool(items)
            and len({x['path'] for x in items}) == len(items)
            and all(entry_complete(manifest, item, prefix) for item in items))


def source_fingerprint(manifest):
    data = {key: manifest.get(key) for key in ('id', 'commit_id', 'file_manifest')}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def invalidate_delivery(manifest):
    for key in ('is_datasetcard_gen_ok', 'is_datasetcard_upload_ok', 'is_archived'):
        manifest[key] = False
    manifest.pop('datasetcard_generation', None)
    manifest.pop('datasetcard_upload', None)
