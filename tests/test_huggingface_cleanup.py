"""Execute the actual downloader class with mocked network I/O and temporary files."""
import ast
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
        self.ns=dict(Any=object, Path=Path, PurePosixPath=PurePosixPath, json=json, os=os,
                     re=re, hashlib=hashlib, datetime=datetime, timezone=timezone, log=Mock())
        cls=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='HuggingfaceSpider')
        exec(compile(ast.Module(body=[cls],type_ignores=[]),str(SOURCE),'exec'),self.ns)
        self.worker=self.ns['HuggingfaceSpider'].__new__(self.ns['HuggingfaceSpider'])
        self.worker.save_dir=self.root; self.worker.repo_id='owner/repo'; self.worker.endpoint='unused'
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

    def test_success_and_restart_without_redownload(self):
        self.worker.download_file()
        self.assertFalse((self.root/self.item['path']).exists())
        self.assertTrue(all(self.saved()[k] for k in ['local_verified','tos_verified','local_deleted']))
        self.worker.download_file()
        self.assertEqual(self.downloads,1)

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

    def test_missing_remote_after_cleanup_never_claims_success(self):
        self.worker.download_file(); self.remote.clear()
        with self.assertRaises(RuntimeError): self.worker.download_file()
        self.assertFalse(self.saved()['tos_verified'])
        self.worker.download_file()
        self.assertEqual(self.downloads,2)
        self.assertTrue(self.saved()['local_deleted'])

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


if __name__=='__main__': unittest.main()
