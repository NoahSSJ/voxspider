import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import github


class GithubTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'part.tar.gz'
        self.path.write_bytes(b'payload')

    def result(self, code=0, output=''):
        return SimpleNamespace(returncode=code, stdout=output)

    def test_success_verifies_exact_object_then_deletes(self):
        with patch.object(github.subprocess, 'run', side_effect=[
            self.result(), self.result(output='Size: 7\n'),
        ]) as run:
            github.upload_and_cleanup(self.path, 'tos://bucket/source/')
        self.assertFalse(self.path.exists())
        self.assertEqual(run.call_args_list[0].args[0],
                         ['tosutil', 'cp', str(self.path), 'tos://bucket/source/part.tar.gz'])
        self.assertEqual(run.call_args_list[1].args[0],
                         ['tosutil', 'stat', 'tos://bucket/source/part.tar.gz', '-sbh'])

    def test_failures_preserve_local_file(self):
        cases = [
            [self.result(1, 'upload failed')],
            [self.result(), self.result(1, 'AccessDenied')],
            [self.result(), self.result(output='Size: 8\n')],
            [self.result(), self.result(output='Size: 7GB\n')],
            [self.result(), subprocess.TimeoutExpired('tosutil', 120)],
        ]
        for results in cases:
            with self.subTest(results=results):
                with patch.object(github.subprocess, 'run', side_effect=results):
                    with self.assertRaises((RuntimeError, ValueError, subprocess.TimeoutExpired)):
                        github.upload_and_cleanup(self.path)
                self.assertEqual(self.path.read_bytes(), b'payload')

    def test_local_change_prevents_cleanup(self):
        def execute(cmd, **kwargs):
            if cmd[1] == 'stat':
                before = self.path.stat()
                self.path.write_bytes(b'changed')
                os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
            return self.result(output='Size: 7\n')
        with patch.object(github.subprocess, 'run', side_effect=execute):
            with self.assertRaises(RuntimeError):
                github.upload_and_cleanup(self.path)
        self.assertTrue(self.path.exists())

    def test_each_file_is_removed_before_next_download(self):
        events = []
        def execute(cmd, **kwargs):
            events.append(cmd[0:2])
            if cmd[0] == 'wget':
                self.assertFalse((self.root / 'first').exists())
                Path(cmd[3]).write_bytes(b'payload')
            return self.result(output='Size: 7\n')
        with patch.object(github.subprocess, 'run', side_effect=execute):
            github.run(['https://example.com/first', 'https://example.com/second'], self.root)
        self.assertEqual(events, [['wget', '-c'], ['tosutil', 'cp'], ['tosutil', 'stat']] * 2)
        self.assertFalse((self.root / 'second').exists())

    def test_download_failure_stops_before_upload_and_next_file(self):
        with patch.object(github.subprocess, 'run',
                          side_effect=subprocess.CalledProcessError(1, 'wget')) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                github.run(['https://example.com/first', 'https://example.com/second'], self.root)
        self.assertEqual(run.call_count, 1)

    def test_symlink_is_rejected(self):
        link = self.root / 'link'
        link.symlink_to(self.path)
        with patch.object(github.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                github.upload_and_cleanup(link)
        run.assert_not_called()
        self.assertTrue(self.path.exists())


if __name__ == '__main__':
    unittest.main()
