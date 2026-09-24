import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core import archive_state as state
from core import archive_scan as scan

PREFIX = 'tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/tts/zh/example/source/'


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'config').mkdir()
        (self.root/'config/hgf.yaml').write_text('datasets:\n- name: example\n  repo_id: owner/example\n  tos_url: '+PREFIX+'\n')
        self.path = self.root/'datasets/example/repo_manifest.json'
        self.path.parent.mkdir(parents=True)
        identity = dict(repo_id='owner/example', commit_id='fixed', path='a', size=7, sha256='sha', blob_id=None)
        entry = dict(path='a', size=7, sha256='sha', blob_id=None,
                     local_verified=True, tos_verified=True, local_deleted=True,
                     tos_verification=dict(identity=identity, uri=PREFIX+'a', size=7, method='size_crc64',
                                           local_crc64='123', remote_crc64='123'))
        self.manifest = dict(id='owner/example', commit_id='fixed', file_manifest=[entry])
        state.initialize(self.manifest)
        self.manifest['status'] = True
        self.save()
        self.card = self.path.parent/'dataset_card.yaml'
        self.card.write_bytes(b'card')
        self.settings = {'codex': 'codex', 'generation_timeout_seconds': 10}

    def save(self):
        state.atomic_json(self.path, self.manifest)

    def current(self):
        return json.loads(self.path.read_text())

    def mark_generated(self):
        self.manifest['is_datasetcard_gen_ok'] = True
        self.manifest['datasetcard_generation'] = dict(card_sha256=hashlib.sha256(b'card').hexdigest(),
            source_fingerprint=state.source_fingerprint(self.manifest))
        self.save()

    def test_transfer_complete_requires_identity_and_all_files(self):
        self.assertTrue(state.transfer_complete(self.manifest, PREFIX))
        bad = copy.deepcopy(self.manifest)
        bad['file_manifest'][0]['tos_verification']['identity']['commit_id'] = 'other'
        self.assertFalse(state.transfer_complete(bad, PREFIX))
        bad = copy.deepcopy(self.manifest)
        bad['file_manifest'].append(dict(bad['file_manifest'][0], path='b', tos_verified=False))
        self.assertFalse(state.transfer_complete(bad, PREFIX))
        self.assertFalse(state.transfer_complete(dict(self.manifest, file_manifest=[]), PREFIX))

    def test_dataset_and_target_locks_exclude_other_workers(self):
        with state.dataset_lock(self.path.parent, PREFIX):
            with self.assertRaises(BlockingIOError):
                with state.dataset_lock(self.path.parent, PREFIX): pass
            other = self.path.parent.parent/'alias'
            other.mkdir()
            with self.assertRaises(BlockingIOError):
                with state.dataset_lock(other, PREFIX): pass
        with state.dataset_lock(self.path.parent, PREFIX): pass

    def test_skip_unready_paused_and_archived_without_network(self):
        for field, value in [('status', False), ('needs_manual_review', True), ('is_archived', True)]:
            with self.subTest(field=field):
                current = dict(self.manifest, **{field:value})
                state.atomic_json(self.path, current)
                with patch.object(scan, 'reconcile', side_effect=AssertionError('network')):
                    self.assertEqual(scan.process(self.root, self.path, self.settings), 'skipped')

    def test_manual_problem_pauses_and_preserves_reason(self):
        with patch.object(scan, 'reconcile'), patch.object(scan, 'generate_card', side_effect=scan.ManualReview('许可不明')):
            self.assertEqual(scan.process(self.root, self.path, self.settings), 'manual_review')
        self.assertTrue(self.current()['needs_manual_review'])
        self.assertEqual(self.current()['last_error']['message'], '许可不明')
        self.assertFalse(self.current()['is_datasetcard_gen_ok'])

    def test_transient_error_can_retry_next_scan(self):
        with patch.object(scan, 'reconcile', side_effect=scan.RetryLater('timeout')):
            self.assertEqual(scan.process(self.root, self.path, self.settings), 'retry')
        self.assertFalse(self.current()['needs_manual_review'])
        self.assertTrue(scan.eligible(self.current()))

    def test_true_status_with_incomplete_evidence_pauses(self):
        self.manifest['file_manifest'][0]['tos_verified'] = False
        self.save()
        self.assertEqual(scan.process(self.root, self.path, self.settings), 'manual_review')
        self.assertFalse(self.current()['status'])
        self.assertTrue(self.current()['needs_manual_review'])

    def test_generated_card_not_regenerated_after_upload_failure(self):
        self.mark_generated()
        with patch.object(scan, 'reconcile'), patch.object(scan, 'generate_card', side_effect=AssertionError('regenerate')), \
             patch.object(scan, 'validated_card'), patch.object(scan, 'publish', side_effect=scan.RetryLater('timeout')):
            self.assertEqual(scan.process(self.root, self.path, self.settings), 'retry')
        self.assertTrue(self.current()['is_datasetcard_gen_ok'])
        self.assertFalse(self.current()['is_datasetcard_upload_ok'])

    def test_card_edit_requires_review(self):
        self.mark_generated()
        with patch.object(scan.checks, 'validate_card', return_value=(b'edited',PREFIX.removesuffix('source/')+'dataset_card.yaml')):
            with self.assertRaises(scan.ManualReview):
                scan.validated_card(self.path, self.manifest, PREFIX)

    def test_remote_card_conflict_never_overwrites(self):
        self.mark_generated()
        with patch.object(scan, 'validated_card', return_value=(b'card','tos://card')), \
             patch.object(scan.checks, 'run_tos', return_value='crc64: 456\n') as tos, \
             patch.object(scan.subprocess, 'run', return_value=SimpleNamespace(returncode=0,stdout='Size: 4\nHashCrc64ecma: 999\n')):
            with self.assertRaises(scan.ManualReview): scan.publish(self.path,self.manifest,PREFIX)
            self.assertEqual(tos.call_count,1)
        self.assertFalse(self.current()['is_datasetcard_upload_ok'])

    def test_upload_flag_only_after_remote_verification(self):
        self.mark_generated()
        missing = SimpleNamespace(returncode=1,stdout='Status [404], error code [], error message [0017-00000003]',stderr='')
        with patch.object(scan, 'validated_card', return_value=(b'card','tos://card')), \
             patch.object(scan.checks, 'run_tos', side_effect=['crc64: 456\n','uploaded']), \
             patch.object(scan.subprocess, 'run', return_value=missing), \
             patch.object(scan.checks, 'object_info', return_value={'size':4,'crc64':'999'}):
            with self.assertRaises(scan.ManualReview): scan.publish(self.path,self.manifest,PREFIX)
        self.assertFalse(self.current()['is_datasetcard_upload_ok'])

    def test_verified_existing_card_reused_without_upload(self):
        self.mark_generated()
        with patch.object(scan, 'validated_card', return_value=(b'card','tos://card')), \
             patch.object(scan.checks, 'run_tos', return_value='crc64: 456\n') as tos, \
             patch.object(scan.subprocess, 'run', return_value=SimpleNamespace(returncode=0,stdout='Size: 4\nHashCrc64ecma: 456\n')), \
             patch.object(scan.checks, 'object_info', return_value={'size':4,'crc64':'456'}):
            scan.publish(self.path,self.manifest,PREFIX)
            self.assertEqual(tos.call_count,1)
        self.assertTrue(self.current()['is_datasetcard_upload_ok'])

    def test_register_final_snapshot_matches_manifest(self):
        self.mark_generated(); self.manifest['is_datasetcard_upload_ok'] = True; self.save()
        state.atomic_json(self.path.parent/'archive_report.json', {'passed':True})
        with patch.object(scan.subprocess, 'run',return_value=SimpleNamespace(returncode=0,stdout='PASS')):
            scan.register(self.root,self.path,self.manifest,PREFIX)
        self.assertTrue(self.current()['is_archived'])
        self.assertEqual(json.loads((self.root/'archived.json').read_text())['datasets'],[self.current()])

    def test_register_failure_does_not_mark_archived(self):
        state.atomic_json(self.path.parent/'archive_report.json', {'passed':False,'error':'timeout'})
        with patch.object(scan.subprocess, 'run',return_value=SimpleNamespace(returncode=1,stdout='FAIL')):
            with self.assertRaises(scan.RetryLater): scan.register(self.root,self.path,self.manifest,PREFIX)
        self.assertFalse(self.current()['is_archived'])

    def test_remember_error_does_not_overwrite_concurrent_progress(self):
        other = copy.deepcopy(self.manifest)
        other['is_datasetcard_gen_ok'] = True
        state.atomic_json(self.path, other)
        scan.remember_error(self.path, self.manifest, 'changed', True)
        self.assertTrue(self.current()['is_datasetcard_gen_ok'])

    def test_initialize_historical_is_offline_and_defaults_false(self):
        for key in state.DEFAULTS: self.manifest.pop(key)
        self.save()
        with patch.object(scan.checks, 'run_tos', side_effect=AssertionError('network')):
            self.assertEqual(scan.initialize_historical(self.root,self.path),'initialized')
        self.assertTrue(self.current()['status'])
        self.assertFalse(self.current()['is_datasetcard_gen_ok'])
        self.assertFalse(self.current()['is_archived'])


if __name__ == '__main__': unittest.main()
