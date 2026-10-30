"""Focused tests for the bounded parallel transfer primitives."""
import ast
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from core import archive_state

SOURCE = Path(__file__).resolve().parents[1] / "core" / "huggingface_parallel.py"


class SpiderBase:
    _space_lock = threading.Lock()
    _reserved_bytes = 0

    def source_path(self, item):
        return self.save_dir / item["path"]

    def can_resume_remote(self, item, commit_id):
        return False


class ParallelTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        parallel_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ParallelHuggingfaceSpider"
        )
        namespace = {
            "Any": object,
            "Future": Future,
            "ThreadPoolExecutor": ThreadPoolExecutor,
            "FIRST_COMPLETED": FIRST_COMPLETED,
            "wait": wait,
            "hashlib": hashlib,
            "json": json,
            "os": os,
            "Path": Path,
            "random": random,
            "re": re,
            "shutil": shutil,
            "subprocess": subprocess,
            "threading": threading,
            "time": time,
            "archive_state": archive_state,
            "HuggingfaceSpider": SpiderBase,
            "log": Mock(),
        }
        module = ast.Module(body=[parallel_class], type_ignores=[])
        exec(compile(module, str(SOURCE), "exec"), namespace)
        cls.spider_class = namespace["ParallelHuggingfaceSpider"]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.spider = self.spider_class.__new__(self.spider_class)
        self.spider.save_dir = self.root
        self.spider.repo_id = "owner/dataset"
        self.spider.tos_url = "tos://bucket/prefix/source"
        self.spider.parallel_workers = 3
        self.spider.journal_path = self.root / ".transfer_journal.jsonl"

    def test_missing_key_requires_specific_not_found_signature(self):
        self.assertTrue(self.spider._is_missing_object(
            "Status [404], error code [], error message [0017-00000003]"
        ))
        self.assertFalse(self.spider._is_missing_object("Status [404] access denied"))
        self.assertFalse(self.spider._is_missing_object("connection timed out"))

    def test_journal_replays_only_matching_proof_and_infers_deletion(self):
        item = {
            "path": "part/a.wav", "size": 4, "sha256": "abcd", "blob_id": None,
            "local_verified": False, "tos_verified": False, "local_deleted": False,
        }
        identity = self.spider._item_identity(item, "fixed-commit")
        proof = {
            "identity": identity,
            "uri": "tos://bucket/prefix/source/part/a.wav",
            "size": 4,
            "local_crc64": "12345",
            "remote_crc64": "12345",
            "method": "size_crc64",
            "verified_at": "2026-01-01T00:00:00+00:00",
        }
        self.spider.journal_path.write_text(
            json.dumps({"identity": identity, "proof": proof}) + "\n",
            encoding="utf-8",
        )
        manifest = {"commit_id": "fixed-commit", "file_manifest": [item]}
        self.assertTrue(self.spider._replay_journal(manifest))
        self.assertTrue(item["local_verified"])
        self.assertTrue(item["tos_verified"])
        self.assertTrue(item["local_deleted"])
        self.assertEqual(item["tos_verification"], proof)

    def test_local_source_is_removed_only_after_durable_remote_proof(self):
        payload = b"test"
        item = {
            "path": "audio.wav", "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(), "blob_id": None,
            "local_verified": False, "tos_verified": False, "local_deleted": False,
        }
        source = self.root / item["path"]
        source.write_bytes(payload)
        self.spider.can_resume_remote = lambda item, commit: False
        self.spider._verify_local = lambda item, path: None
        self.spider._crc64_local = lambda path: "123"
        self.spider._ensure_remote = lambda item, path, commit, crc: {
            "identity": self.spider._item_identity(item, commit),
            "uri": f"{self.spider.tos_url}/{item['path']}",
            "size": item["size"], "local_crc64": crc,
            "remote_crc64": crc, "method": "size_crc64",
            "verified_at": "2026-01-01T00:00:00+00:00",
        }
        def append_proof(*args):
            self.assertTrue(source.exists(), "proof must be durable before source deletion")
        self.spider._append_proof = append_proof
        self.spider._process_one(item, "fixed-commit")
        self.assertFalse(source.exists())
        self.assertTrue(item["local_verified"])
        self.assertTrue(item["tos_verified"])
        self.assertTrue(item["local_deleted"])

    def test_remote_failure_keeps_local_source(self):
        source = self.root / "audio.wav"
        source.write_bytes(b"test")
        item = {"path": "audio.wav", "size": 4, "sha256": None, "blob_id": None}
        self.spider.can_resume_remote = lambda item, commit: False
        self.spider._verify_local = lambda item, path: None
        self.spider._crc64_local = lambda path: "123"
        self.spider._ensure_remote = lambda *args: (_ for _ in ()).throw(
            RuntimeError("remote verification failed")
        )
        with self.assertRaisesRegex(RuntimeError, "remote verification failed"):
            self.spider._process_one(item, "fixed-commit")
        self.assertTrue(source.exists())

    def test_parallel_worker_count_is_bounded(self):
        items = []
        for index in range(9):
            item = {"path": f"f{index}.bin", "size": 1}
            (self.root / item["path"]).write_bytes(b"x")
            items.append(item)
        self.spider.can_resume_remote = lambda item, commit: False
        self.spider._process_one = self.track_concurrency
        self.active = 0
        self.peak = 0
        self.counter_lock = threading.Lock()
        self.spider.process_parallel({"commit_id": "fixed", "file_manifest": items})
        self.assertEqual(self.peak, 3)

    def track_concurrency(self, item, commit_id):
        with self.counter_lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.02)
        with self.counter_lock:
            self.active -= 1


if __name__ == "__main__":
    unittest.main()
