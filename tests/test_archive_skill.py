"""Offline tests for the skill's remote-only completion gates."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / '.agents/skills/hf-filewise-tos-archive/scripts/archive_checks.py'
SPEC = importlib.util.spec_from_file_location('archive_checks', PATH)
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)
PREFIX = 'tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/tts/zh/example/source/'


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest_path = self.root / 'repo_manifest.json'
        self.manifest = {'id': 'owner/example', 'commit_id': 'fixed', 'card_data': {'retained': True},
                         'file_manifest': [self.entry('a.parquet')]}
        self.save()
        self.objects = {'a.parquet': {'size': 7, 'crc64': '123'}}
        self.card_present = True
        self.calls = []

    def entry(self, relative):
        return {'path': relative, 'size': 7, 'sha256': 'upstream', 'local_verified': True,
                'tos_verified': True, 'local_deleted': False,
                'tos_verification': {'uri': PREFIX + relative, 'size': 7, 'method': 'size_crc64',
                                     'local_crc64': '123', 'remote_crc64': '123'}}

    def save(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def fake_tos(self, *args):
        self.calls.append(args)
        if args[0] == 'ls':
            keys = sorted(self.objects)
            marker = next((a[len('-marker='):] for a in args if a.startswith('-marker=')), None)
            if marker:
                keys = [k for k in keys if PREFIX.split('/', 3)[3] + k > marker]
            page = keys[:1000]
            return '\n'.join(PREFIX + k for k in page) + f'\nFile number is: {len(page)}\n'
        if args[0] == 'du':
            return f'Total object number: {len(self.objects)}\nTotal object size: {sum(o["size"] for o in self.objects.values())}B'
        if args[0] == 'hash':
            return 'crc64: 456\n'
        if args[0] == 'stat':
            if args[1].endswith('/dataset_card.yaml'):
                if not self.card_present:
                    raise RuntimeError('card does not exist')
                return f'Size:\n{(self.root / "dataset_card.yaml").stat().st_size}\nHashCrc64ecma:\n456\n'
            obj = self.objects[args[1][len(PREFIX):]]
            return f'Size: {obj["size"]}\nHashCrc64ecma: {obj["crc64"]}\n'
        raise AssertionError(args)

    def card(self):
        import yaml
        card = dict(schema_version='speech_dataset_spec_v1.2',
            dataset=dict(id='example', name='Example', version='unversioned', split='all'), status='raw',
            source=dict(owner_type='public', source_platform='dataset_repository'),
            lineage=dict(upstream=[], derivation=[], manifests=dict(samples=None)),
            data=dict(num_samples=1, total_duration_hours=1.0, languages=[]),
            labels=dict(fixed={}, distribution={}, unknown=[], availability={k: False for k in
                ['transcript', 'timestamps', 'speaker_labels', 'diarization', 'pronunciation', 'phoneme', 'prosody']}),
            usage=dict(intended_use=[], not_recommended_for=[]),
            homepage='https://huggingface.co/datasets/owner/example',
            generation=dict(generated_at='2026-09-07T00:00:00+00:00'))
        path = self.root / 'dataset_card.yaml'
        path.write_text(yaml.safe_dump(card))
        return path

    def register(self):
        args = ['checks', 'register', str(self.manifest_path), PREFIX,
                '--card', str(self.root / 'dataset_card.yaml'),
                '--registry', str(self.root / 'archived.json'), '--report', str(self.root / 'report.json')]
        with patch.object(sys, 'argv', args), patch.object(checks, 'run_tos', side_effect=self.fake_tos):
            return checks.main()

    def test_reconcile_without_local_shards(self):
        with patch.object(checks, 'run_tos', side_effect=self.fake_tos):
            _, report = checks.reconcile(self.manifest_path, PREFIX)
        self.assertTrue(report['passed'])
        self.assertFalse((self.root / 'a.parquet').exists())

    def test_missing_extra_and_same_size_corruption(self):
        for objects, field in [({}, 'missing'),
                              ({'a.parquet': {'size': 7, 'crc64': '999'}}, 'mismatches'),
                              ({**self.objects, 'extra': {'size': 7, 'crc64': '123'}}, 'extra')]:
            with self.subTest(field=field), patch.object(checks, 'run_tos', side_effect=self.fake_tos):
                self.objects = objects
                _, report = checks.reconcile(self.manifest_path, PREFIX)
                self.assertFalse(report['passed'])
                self.assertTrue(report[field])

    def test_paginates_past_1000(self):
        self.objects = {f'{i:04d}': {'size': 7, 'crc64': '123'} for i in range(1001)}
        self.manifest['file_manifest'] = [self.entry(k) for k in self.objects]
        self.save()
        with patch.object(checks, 'run_tos', side_effect=self.fake_tos):
            _, report = checks.reconcile(self.manifest_path, PREFIX)
        self.assertTrue(report['passed'])
        self.assertEqual(len([c for c in self.calls if c[0] == 'ls']), 2)

    def test_wrong_destination_and_unverified_rejected(self):
        with self.assertRaises(ValueError):
            checks.load_manifest(self.manifest_path, PREFIX.replace('/example/', '/other/'))
        self.manifest['file_manifest'][0]['tos_verified'] = False
        self.save()
        with self.assertRaises(ValueError):
            checks.load_manifest(self.manifest_path, PREFIX)

    def test_register_preserves_complete_manifest_and_versions(self):
        self.card()
        self.assertEqual(self.register(), 0)
        self.assertEqual(self.register(), 0)
        registry = self.root / 'archived.json'
        self.assertEqual(json.loads(registry.read_text())['datasets'], [self.manifest])
        self.manifest['commit_id'] = 'second'
        self.save()
        self.assertEqual(self.register(), 0)
        self.assertEqual(len(json.loads(registry.read_text())['datasets']), 2)

    def test_missing_card_does_not_register(self):
        self.card()
        self.card_present = False
        self.assertEqual(self.register(), 1)
        self.assertFalse((self.root / 'archived.json').exists())

    def test_failed_reconciliation_does_not_touch_registry(self):
        self.card()
        registry = self.root / 'archived.json'
        registry.write_text('{"datasets": []}')
        before = registry.read_bytes()
        self.objects['a.parquet']['crc64'] = '999'
        self.assertEqual(self.register(), 1)
        self.assertEqual(registry.read_bytes(), before)

    def test_size_only_stat_rejected(self):
        with patch.object(checks, 'run_tos', return_value='Size: 7\n'):
            with self.assertRaises(ValueError):
                checks.object_info(PREFIX + 'a.parquet')


if __name__ == '__main__':
    unittest.main()
