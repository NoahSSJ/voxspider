import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / '.agents/skills/tos-dataset-status-auditor/scripts/audit_status.py'
SPEC = importlib.util.spec_from_file_location('audit_status_skill', PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
PREFIX = 'tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/tts/zh/example/source/'


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.data = dict(id='owner/example', commit_id='fixed', file_manifest=[dict(
            path='a', size=7, local_verified=True, tos_verified=True,
            tos_verification=dict(uri=PREFIX+'a', size=7, method='size_crc64',
                                  local_crc64='123', remote_crc64='123'))])
        self.job = dict(dataset='本地别名', repo_id='owner/example', manifest=self.data,
                        config={'repo_id':'owner/example', 'tos_url':PREFIX},
                        manifest_path=None, local_dir=None, error=None)

    def run_audit(self, remote=None, card=None, listing=None, du=None):
        def stat(uri):
            return card if uri.endswith('dataset_card.yaml') else remote
        with patch.object(audit.helpers, 'list_all', return_value={'a'} if listing is None else listing), \
             patch.object(audit.helpers, 'run_tos', return_value=du or 'Total object number: 1\nTotal object size: 7B'), \
             patch.object(audit, 'stat_object', side_effect=stat):
            return audit.audit(self.job, [self.data])

    def test_full_source_without_local_shards_or_card(self):
        row=self.run_audit({'size':7,'crc64':'123'})
        self.assertTrue(row['upload_complete'])
        self.assertTrue(row['reconciliation_complete'])
        self.assertTrue(row['checksum_complete'])
        self.assertFalse(row['datacard_uploaded'])
        self.assertTrue(row['archived_registered'])

    def test_same_size_corruption_distinguished(self):
        row=self.run_audit({'size':7,'crc64':'999'})
        self.assertTrue(row['reconciliation_complete'])
        self.assertFalse(row['checksum_complete'])
        self.assertEqual(row['crc_mismatches'], ['a'])

    def test_incomplete_local_flags_do_not_prevent_remote_query(self):
        self.data['file_manifest'][0]['local_verified']=False
        row=self.run_audit({'size':7,'crc64':'123'})
        self.assertTrue(row['upload_complete'])
        self.assertTrue(row['reconciliation_complete'])
        self.assertIsNone(row['checksum_complete'])

    def test_missing_source_with_early_card(self):
        row=self.run_audit(listing=set(), card={'size':10,'crc64':'456'},
                           du='Total object number: 0\nTotal object size: 0B')
        self.assertFalse(row['upload_complete'])
        self.assertTrue(row['datacard_uploaded'])
        self.assertTrue(row['archived_registered'])

    def test_permission_error_is_not_absence(self):
        with patch.object(audit.subprocess, 'run', return_value=SimpleNamespace(
                returncode=1, stdout='Error: Status [403], error code [AccessDenied]')):
            with self.assertRaises(RuntimeError):
                audit.stat_object(PREFIX+'a')
        self.assertTrue(audit.missing_object('Status [404], error code [], error message [0017-00000003]'))
        self.assertFalse(audit.missing_object('Status [404], error code [NoSuchBucket]'))

    def test_source_error_still_queries_card(self):
        with patch.object(audit.helpers, 'list_all', side_effect=RuntimeError('denied')), \
             patch.object(audit, 'stat_object', return_value={'size':10,'crc64':None}):
            row=audit.audit(self.job, [])
        self.assertIsNone(row['upload_complete'])
        self.assertTrue(row['datacard_uploaded'])
        self.assertTrue(row['needs_review'])

    def test_config_and_history_conflict(self):
        self.job['config']['tos_url']=PREFIX.replace('/example/', '/different/')
        row=audit.audit(self.job, [])
        self.assertTrue(row['needs_review'])
        self.assertIsNone(row['upload_complete'])

    def test_manifest_changed_before_query_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'repo_manifest.json'
            path.write_text(json.dumps({**self.data, 'commit_id':'changed'}))
            self.job['manifest_path']=str(path)
            with patch.object(audit.helpers, 'list_all') as query:
                row=audit.audit(self.job, [])
                query.assert_not_called()
            self.assertIsNone(row['upload_complete'])
            self.assertTrue(row['needs_review'])

    def test_discovery_exact_id_and_archived_only_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); datasets=root/'datasets'; datasets.mkdir()
            local=datasets/'本地别名'; local.mkdir()
            (local/'repo_manifest.json').write_text(json.dumps(self.data))
            cfg=root/'config.yaml'
            cfg.write_text('datasets:\n  - name: 本地别名\n    repo_id: owner/example\n    enabled: false\n')
            older={**self.data, 'commit_id':'older'}
            registry=root/'archived.json'; registry.write_text(json.dumps({'datasets':[older]}))
            before=registry.read_bytes()
            jobs,_=audit.discover(cfg, datasets, registry, ['owner/example'])
            self.assertEqual(len(jobs),2)
            self.assertEqual(registry.read_bytes(), before)
            with self.assertRaises(ValueError):
                audit.discover(cfg, datasets, registry, ['example'])


if __name__ == '__main__':
    unittest.main()
