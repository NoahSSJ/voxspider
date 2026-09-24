"""Execute the actual downloader class with mocked network I/O and temporary files."""
import ast
from core import archive_state
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import shutil
import threading
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SOURCE=Path(__file__).resolve().parents[1]/'core/huggingface.py'


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.ns=dict(archive_state=archive_state, Any=object, Path=Path, PurePosixPath=PurePosixPath, json=json, os=os,
                     re=re, hashlib=hashlib, datetime=datetime, timezone=timezone, log=Mock(),
                     threading=threading, shutil=shutil, deque=deque, closing=closing,
                     ThreadPoolExecutor=ThreadPoolExecutor)
        cls=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='HuggingfaceSpider')
        exec(compile(ast.Module(body=[cls],type_ignores=[]),str(SOURCE),'exec'),self.ns)
        self.worker=self.ns['HuggingfaceSpider'].__new__(self.ns['HuggingfaceSpider'])
        self.worker.save_dir=self.root; self.worker.repo_id='owner/repo'; self.worker.endpoint='unused'
        self.worker.download_workers=3; self.worker.reserve_bytes=1024**3
        self.worker.token="test-token"
        self.worker.tos_url='tos://bucket/source'
        self.item=dict(path='data/a.bin',size=7,blob_id=None,sha256=hashlib.sha256(b'payload').hexdigest(),
                       local_verified=False,tos_verified=False,local_deleted=False)
        self.manifest=dict(id='owner/repo',commit_id='fixed',file_manifest=[self.item])
        self.worker.get_manifest=lambda:self.manifest
        self.remote={}; self.downloads=0; self.crc='123'
        self.ns['hf_hub_download']=self.download
        self.ns['subprocess']=SimpleNamespace(run=self.run_command,PIPE=subprocess.PIPE,STDOUT=subprocess.STDOUT)

    def download(self,**kwargs):
        self.downloads+=1
        path=self.root/kwargs['filename']; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'payload')
        return str(path)

    def run_command(self,args,**kwargs):
        command=args[1]
        if command=='hash': return SimpleNamespace(returncode=0,stdout='crc64: 123\n')
        if command=='cp':
            self.remote[args[3]]=b'payload'
            return SimpleNamespace(returncode=0,stdout='uploaded')
        if args[2] not in self.remote:
            return SimpleNamespace(returncode=1,stdout='Status [404], error message [0017-00000003]')
        return SimpleNamespace(returncode=0,stdout=f'Size: 7\nHashCrc64ecma: {self.crc}\n')

    def saved(self):
        return json.loads((self.root/'repo_manifest.json').read_text())['file_manifest'][0]

    def test_new_manifest_with_and_without_card_metadata(self):
        self.ns['pprint'] = Mock()
        self.ns['RepoFile'] = SimpleNamespace
        self.worker.repo_type = 'dataset'
        for metadata in (None, {'language': ['zh'], 'license': 'apache-2.0'}):
            with self.subTest(metadata=metadata):
                (self.root / 'repo_manifest.json').unlink(missing_ok=True)
                card = None if metadata is None else SimpleNamespace(to_dict=lambda: metadata)
                info = SimpleNamespace(id='owner/repo', author='owner', card_data=card,
                    citation=None, created_at=None, description=None, sha='fixed',
                    tags=[], used_storage=None, main_size=None)
                self.worker.api = Mock()
                self.worker.api.dataset_info.return_value = info
                self.worker.api.list_repo_tree.return_value = [SimpleNamespace(
                    path='data/a.bin', size=7, blob_id='blob', lfs=None)]
                manifest = type(self.worker).get_manifest(self.worker)
                self.assertEqual(manifest['card_data'], metadata or {})
                self.assertEqual(manifest['language'], (metadata or {}).get('language'))
                self.assertEqual(manifest['commit_id'], 'fixed')
                self.assertEqual(manifest['file_manifest'][0]['path'], 'data/a.bin')
                self.assertEqual(json.loads((self.root / 'repo_manifest.json').read_text()), manifest)
                self.worker.api.list_repo_tree.assert_called_once_with(
                    repo_id='owner/repo', repo_type='dataset', revision='fixed', recursive=True)

    def test_prefetch_passes_authentication_token(self):
        download = Mock(side_effect=self.download)
        self.ns['hf_hub_download'] = download
        with closing(self.worker.prefetch(self.manifest)) as pending:
            self.assertEqual(list(pending), [self.item])
        self.assertEqual(download.call_args.kwargs['token'], self.worker.token)

    def test_project_dotenv_loaded_without_overriding_environment(self):
        tree = ast.parse(SOURCE.read_text())
        calls = [node for node in tree.body
                 if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                 and isinstance(node.value.func, ast.Name)
                 and node.value.func.id == 'load_dotenv']
        self.assertEqual(len(calls), 1)
        loader = Mock()
        exec(compile(ast.Module(body=calls, type_ignores=[]), str(SOURCE), 'exec'),
             {'load_dotenv': loader, 'root_dir': self.root})
        loader.assert_called_once_with(self.root / '.env', override=False)

    def test_success_and_restart_without_redownload(self):
        self.worker.download_file()
        self.assertFalse((self.root/self.item['path']).exists())
        self.assertTrue(all(self.saved()[k] for k in ['local_verified','tos_verified','local_deleted']))
        before = (self.root/'repo_manifest.json').read_bytes()
        with patch.object(self.ns['subprocess'], 'run', side_effect=AssertionError('unexpected TOS request')):
            with patch.object(self.worker, 'save_manifest', side_effect=AssertionError('unexpected manifest rewrite')):
                self.worker.download_file()
        self.assertEqual(self.downloads,1)
        self.assertEqual((self.root/'repo_manifest.json').read_bytes(), before)

    def test_manifest_status_and_delivery_defaults(self):
        self.worker.download_file()
        saved = json.loads((self.root/'repo_manifest.json').read_text())
        self.assertTrue(saved['status'])
        for key in ('is_datasetcard_gen_ok', 'is_datasetcard_upload_ok',
                    'is_archived', 'needs_manual_review'):
            self.assertIs(saved[key], False)
        self.assertIsNone(saved['last_error'])

    def test_failed_download_keeps_status_false(self):
        self.ns['hf_hub_download'] = Mock(side_effect=OSError('network failed'))
        with self.assertRaises(OSError):
            self.worker.download_file()
        saved = json.loads((self.root/'repo_manifest.json').read_text())
        self.assertFalse(saved['status'])

    def test_downloader_does_not_write_while_archive_holds_lock(self):
        with archive_state.dataset_lock(self.root, self.worker.tos_url):
            with self.assertRaises(BlockingIOError):
                self.worker.download_file()
        self.assertEqual(self.downloads, 0)
        self.assertFalse((self.root/'repo_manifest.json').exists())

    def test_crc_conflict_preserves_local(self):
        self.crc='999'
        with self.assertRaises(ValueError): self.worker.download_file()
        self.assertTrue((self.root/self.item['path']).exists())
        self.assertFalse(self.saved()['tos_verified'])
        self.assertFalse(self.saved()['local_deleted'])

    def test_verification_save_failure_preserves_local(self):
        original=self.worker.save_manifest
        def save(data):
            if self.item['tos_verified']: raise OSError('disk full')
            original(data)
        with patch.object(self.worker,'save_manifest',side_effect=save):
            with self.assertRaises(OSError): self.worker.download_file()
        self.assertTrue((self.root/self.item['path']).exists())
        self.assertFalse(self.saved()['tos_verified'])

    def test_unlink_failure_keeps_verified_but_not_deleted(self):
        original=Path.unlink
        def unlink(path,*args,**kwargs):
            if path==self.root/self.item['path']: raise PermissionError('denied')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink):
            with self.assertRaises(PermissionError): self.worker.download_file()
        self.assertTrue(self.saved()['tos_verified'])
        self.assertFalse(self.saved()['local_deleted'])
        self.assertTrue((self.root/self.item['path']).exists())

    def test_crash_after_unlink_recovers_without_redownload(self):
        original=self.worker.save_manifest
        def save(data):
            if self.item['local_deleted']: raise OSError('crash after delete')
            original(data)
        with patch.object(self.worker,'save_manifest',side_effect=save):
            with self.assertRaises(OSError): self.worker.download_file()
        self.item.clear(); self.item.update(self.saved())
        self.assertFalse(self.item['local_deleted'])
        self.worker.download_file()
        self.assertEqual(self.downloads,1)
        self.assertTrue(self.saved()['local_deleted'])

    def test_resume_trusts_saved_evidence_without_remote_query(self):
        self.worker.download_file(); self.remote.clear()
        before = self.saved()
        with patch.object(self.ns['subprocess'], 'run', side_effect=AssertionError('unexpected TOS request')):
            self.worker.download_file()
        self.assertEqual(self.downloads, 1)
        self.assertEqual(self.saved(), before)

    def test_complete_record_with_leftover_local_file_skips_transfer(self):
        self.worker.download_file()
        target = self.root/self.item['path']
        target.write_bytes(b'changed')
        self.item['local_deleted'] = False
        with patch.object(self.ns['subprocess'], 'run', side_effect=AssertionError('unexpected TOS request')):
            self.worker.download_file()
        self.assertEqual(self.downloads, 1)
        self.assertEqual(target.read_bytes(), b'changed')
        self.assertFalse(self.item['local_deleted'])

    def test_incomplete_or_mismatched_evidence_does_not_skip(self):
        self.worker.download_file()
        original = json.loads(json.dumps(self.item))
        for field, value in [('local_verified', False), ('tos_verified', False)]:
            with self.subTest(field=field):
                self.item.clear(); self.item.update(json.loads(json.dumps(original)))
                self.item[field] = value
                self.assertFalse(self.worker.can_resume_remote(self.item, 'fixed'))
        for field, value in [('identity', {}), ('uri', 'tos://other/source/data/a.bin'),
                             ('size', 8), ('method', 'size_only'), ('remote_crc64', '999')]:
            with self.subTest(field=field):
                self.item.clear(); self.item.update(json.loads(json.dumps(original)))
                self.item['tos_verification'][field] = value
                self.assertFalse(self.worker.can_resume_remote(self.item, 'fixed'))
        self.item.clear(); self.item.update(original)
        self.assertFalse(self.worker.can_resume_remote(self.item, 'different-commit'))

    def test_resume_processes_unfinished_file_after_completed_files(self):
        self.worker.download_file()
        unfinished = dict(self.item, path='data/next.bin', local_verified=False,
                          tos_verified=False, local_deleted=False, tos_verification=None)
        self.manifest['file_manifest'].append(unfinished)
        run = self.run_command
        def command(args, **kwargs):
            self.assertNotIn(self.worker.tos_url + '/' + self.item['path'], args)
            return run(args, **kwargs)
        self.ns['subprocess'].run = command
        self.worker.download_file()
        self.assertEqual(self.downloads, 2)
        self.assertTrue(unfinished['tos_verified'])
        self.assertTrue(unfinished['local_deleted'])

    def test_file_changed_during_transfer_is_kept(self):
        run=self.run_command
        def command(args,**kwargs):
            output=run(args,**kwargs)
            if args[1]=='cp': (self.root/self.item['path']).write_bytes(b'changed')
            return output
        self.ns['subprocess'].run=command
        with self.assertRaises(RuntimeError): self.worker.download_file()
        self.assertEqual((self.root/self.item['path']).read_bytes(),b'changed')
        self.assertFalse(self.saved()['local_deleted'])
        self.assertFalse(self.saved()['local_verified'])

    def test_management_path_or_symlink_rejected(self):
        for name in ['repo_manifest.json','dataset_card.yaml','../escape']:
            self.item['path']=name
            with self.assertRaises(ValueError): self.worker.download_file()
        outside=self.root/'evidence'; outside.write_bytes(b'keep')
        (self.root/'link').symlink_to(outside)
        self.item['path']='link'
        with self.assertRaises(ValueError): self.worker.download_file()
        self.assertEqual(outside.read_bytes(),b'keep')
        self.assertEqual(self.downloads,0)

    def add_files(self, count):
        self.manifest['file_manifest'] = [
            dict(self.item, path=f'data/{index}.bin') for index in range(count)
        ]

    def test_parallel_downloads_serial_manifest_and_resume(self):
        self.add_files(6)
        barrier = threading.Barrier(3)
        download = self.download
        threads = set()
        def concurrent_download(**kwargs):
            threads.add(threading.get_ident())
            if int(Path(kwargs['filename']).stem) < 3:
                barrier.wait(timeout=5)
            return download(**kwargs)
        self.ns['hf_hub_download'] = concurrent_download
        save = self.worker.save_manifest
        owner = threading.get_ident()
        def serial_save(data):
            self.assertEqual(threading.get_ident(), owner)
            save(data)
        self.worker.save_manifest = serial_save
        self.worker.download_file()
        self.assertEqual(len(threads), 3)
        self.assertEqual(self.downloads, 6)
        saved = json.loads((self.root/'repo_manifest.json').read_text())
        self.assertTrue(all(item['local_deleted'] for item in saved['file_manifest']))
        self.worker.download_file()
        self.assertEqual(self.downloads, 6)
        self.assertEqual(type(self.worker)._reserved_bytes, 0)

    def test_prefetch_window_is_bounded(self):
        self.add_files(8)
        with closing(self.worker.prefetch(self.manifest)) as pending:
            next(pending)
        self.assertLessEqual(self.downloads, 3)
        self.assertEqual(type(self.worker)._reserved_bytes, 0)

    def test_low_space_reduces_prefetch_window(self):
        self.add_files(3)
        free = self.worker.reserve_bytes + self.item['size']
        with patch.object(shutil, 'disk_usage', return_value=SimpleNamespace(free=free)):
            self.worker.download_file()
        self.assertEqual(self.downloads, 3)
        self.assertEqual(type(self.worker)._reserved_bytes, 0)

    def test_existing_file_is_processed_even_when_disk_is_full(self):
        target = self.root/self.item['path']
        target.parent.mkdir(parents=True)
        target.write_bytes(b'payload')
        with patch.object(shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            self.worker.download_file()
        self.assertEqual(self.downloads, 0)
        self.assertTrue(self.saved()['local_deleted'])
        self.assertFalse(target.exists())

    def test_insufficient_space_never_starts_download(self):
        with patch.object(shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaises(OSError):
                self.worker.download_file()
        self.assertEqual(self.downloads, 0)
        self.assertEqual(type(self.worker)._reserved_bytes, 0)

    def test_download_failure_releases_reservations_and_keeps_files(self):
        self.add_files(3)
        download = self.download
        def fail(**kwargs):
            download(**kwargs)
            raise OSError('interrupted')
        self.ns['hf_hub_download'] = fail
        with self.assertRaises(OSError):
            self.worker.download_file()
        self.assertEqual(type(self.worker)._reserved_bytes, 0)
        self.assertTrue((self.root/'data/0.bin').exists())
        self.assertFalse(self.remote)

    def test_duplicate_paths_rejected_before_download(self):
        self.manifest['file_manifest'].append(dict(self.item))
        with self.assertRaises(ValueError):
            self.worker.download_file()
        self.assertEqual(self.downloads, 0)


if __name__=='__main__': unittest.main()
