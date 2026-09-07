import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('transfer', Path(__file__).resolve().parents[1] / 'core/huggingface_transfer.py')
transfer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transfer)


class FakeTos:
    def __init__(self):
        self.objects = {}
        self.uploads = 0
        self.with_crc = True

    def head_object(self, bucket, key):
        if key not in self.objects:
            from tos.exceptions import TosServerError
            error = TosServerError.__new__(TosServerError)
            error.status_code, error.code = 404, 'NoSuchKey'
            raise error
        data = self.objects[key]
        result = transfer.digest(io.BytesIO(data), len(data))
        return SimpleNamespace(content_length=len(data), etag='etag', version_id=None,
            hash_crc64_ecma=int(result['crc64']) if self.with_crc else None)

    def upload_file(self, bucket, key, path, **kwargs):
        assert kwargs['generic_input'].request_headers['x-tos-forbid-overwrite'] == 'true'
        assert key not in self.objects
        self.objects[key] = Path(path).read_bytes()
        self.uploads += 1

    def get_object(self, bucket, key, **kwargs):
        return io.BytesIO(self.objects[key])

    def list_objects_type2(self, bucket, **kwargs):
        keys = sorted(self.objects)
        offset = int(kwargs['continuation_token'] or 0)
        page = keys[offset:offset + 1]
        return SimpleNamespace(contents=[SimpleNamespace(key=k, size=len(self.objects[k])) for k in page],
            is_truncated=offset + 1 < len(keys), next_continuation_token=str(offset + 1))


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = FakeTos()
        self.worker = transfer.HuggingfaceTransfer('owner/repo', self.root, self.client, 'bucket', 'raw/source')
        self.worker.source.mkdir()
        self.path = self.worker.source / 'part.bin'
        self.path.write_bytes(b'payload')
        self.item = dict(path='part.bin', size=7, blob_id=None, sha256=hashlib.sha256(b'payload').hexdigest())
        self.worker.manifest_path.write_text(json.dumps(dict(id='owner/repo', commit_id='fixed', file_manifest=[self.item])))
        self.worker.load_manifest()
        self.item = self.worker.manifest['file_manifest'][0]

    def test_success_and_resume_without_download_or_upload(self):
        self.worker.process(self.item)
        self.assertFalse(self.path.exists())
        self.assertTrue(all(self.item[k] for k in ('local_verified', 'tos_verified', 'local_deleted')))
        with patch.object(self.worker, 'download', side_effect=AssertionError('不应下载')):
            self.worker.process(self.item)
        self.assertEqual(self.client.uploads, 1)
        self.worker.reconcile()
        self.assertTrue(self.worker.manifest['transfer_complete'])

    def test_conflicting_remote_keeps_local(self):
        self.client.objects['raw/source/part.bin'] = b'corrupt'
        with self.assertRaises(ValueError):
            self.worker.process(self.item)
        self.assertTrue(self.path.exists())
        self.assertFalse(self.item['tos_verified'])
        self.assertEqual(self.client.uploads, 0)

    def test_invalid_local_never_uploads(self):
        self.path.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.worker.process(self.item)
        self.assertFalse(self.item['local_verified'])
        self.assertEqual(self.client.uploads, 0)
        self.assertTrue(self.path.exists())

    def test_manifest_save_failure_never_deletes(self):
        original = self.worker.save
        def fail_after_verification():
            if self.item['tos_verified']:
                raise OSError('disk full')
            original()
        with patch.object(self.worker, 'save', side_effect=fail_after_verification):
            with self.assertRaises(OSError):
                self.worker.process(self.item)
        self.assertTrue(self.path.exists())
        self.assertFalse(json.loads(self.worker.manifest_path.read_text())['file_manifest'][0]['tos_verified'])

    def test_missing_crc_reads_remote(self):
        self.client.with_crc = False
        self.worker.process(self.item)
        self.assertEqual(self.item['tos_verification']['method'], 'size_sha256_readback')

    def test_pagination_detects_extra_object(self):
        self.worker.process(self.item)
        self.client.objects['raw/source/unexpected'] = b'extra'
        with self.assertRaises(ValueError):
            self.worker.reconcile()
        self.assertEqual(self.worker.manifest['reconciliation']['extra'], ['raw/source/unexpected'])
        self.assertFalse(self.worker.manifest['transfer_complete'])

    def test_changed_target_rejected(self):
        self.worker.prefix = 'different/source/'
        with self.assertRaises(ValueError):
            self.worker.load_manifest()

    def test_unsafe_paths_rejected(self):
        for path in ('../escape', '/absolute', '.cache/file', 'a/../b'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                transfer.safe_path(self.worker.source, path)


if __name__ == '__main__':
    unittest.main()
