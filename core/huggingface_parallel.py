"""Bounded end-to-end parallel Hugging Face -> TOS archiver.

Run with ``python -m core.huggingface_parallel``. It reads the same
``config/hgf.yaml`` and manifest format as :mod:`core.huggingface`.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import threading
import time
from typing import Any

import httpx
from huggingface_hub.errors import HfHubHTTPError

if __package__:
    from . import archive_state
    from .huggingface import (
        DatasetScheduler,
        HuggingfaceSpider,
        setup_logger,
    )
else:  # Support ``python core/huggingface_parallel.py`` as well.
    import archive_state
    from huggingface import DatasetScheduler, HuggingfaceSpider, setup_logger

log = setup_logger("huggingface_parallel")


class ParallelHuggingfaceSpider(HuggingfaceSpider):
    """Use a bounded pool for complete per-file download/upload transactions.

    Each worker downloads, validates, uploads and CRC64-checks one distinct
    object. Durable per-file proofs are appended to a small journal before a
    local source is deleted; the large repo manifest is atomically checkpointed
    after a batch and can be reconstructed from the journal after interruption.
    """

    _journal_write_lock = threading.Lock()

    def __init__(self, dataset: dict[str, Any], loader=None):
        loader = loader or {}
        super().__init__(dataset, loader)
        # Preserve the familiar knob as a fallback; callers can tune the
        # end-to-end pool independently with ``parallel_workers``.
        self.parallel_workers = loader.get(
            "parallel_workers", loader.get("download_workers", 8)
        )
        self.download_max_attempts = loader.get("download_max_attempts", 5)
        self.retry_base_seconds = loader.get("retry_base_seconds", 3)
        if type(self.parallel_workers) is not int or self.parallel_workers < 1:
            raise ValueError("parallel_workers must be a positive integer")
        if type(self.download_max_attempts) is not int or self.download_max_attempts < 1:
            raise ValueError("download_max_attempts must be a positive integer")
        if type(self.retry_base_seconds) not in (int, float) or self.retry_base_seconds < 0:
            raise ValueError("retry_base_seconds must be nonnegative")
        self.journal_path = self.save_dir / ".transfer_journal.jsonl"

    def source_path(self, item):
        path = item.get("path", "")
        if path == ".transfer_journal.jsonl":
            raise ValueError("源路径与并行传输管理文件冲突")
        return super().source_path(item)

    def _item_identity(self, item, commit_id):
        return {
            "repo_id": self.repo_id,
            "commit_id": commit_id,
            "path": item["path"],
            "size": item["size"],
            "sha256": item.get("sha256"),
            "blob_id": item.get("blob_id"),
        }

    def _journal_proof_is_valid(self, entry, item, commit_id):
        proof = entry.get("proof")
        if not isinstance(proof, dict):
            return False
        crc = proof.get("local_crc64")
        return (
            entry.get("identity") == self._item_identity(item, commit_id)
            and proof.get("identity") == self._item_identity(item, commit_id)
            and proof.get("uri") == f"{self.tos_url.rstrip('/')}/{item['path']}"
            and proof.get("size") == item.get("size")
            and proof.get("method") == "size_crc64"
            and isinstance(crc, str)
            and re.fullmatch(r"\d{1,20}", crc) is not None
            and int(crc) < 2**64
            and proof.get("remote_crc64") == crc
        )

    def _replay_journal(self, repo_dict):
        """Restore only proofs whose full source identity still matches."""
        if not self.journal_path.exists():
            return False
        by_path = {item["path"]: item for item in repo_dict["file_manifest"]}
        lines = self.journal_path.read_text(encoding="utf-8").splitlines()
        latest = {}
        for index, line in enumerate(lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                if index == len(lines) - 1:
                    log.warning("忽略传输日志中不完整的末尾记录")
                    continue
                raise ValueError(f"并行传输日志第 {index + 1} 行损坏")
            item = by_path.get(entry.get("identity", {}).get("path"))
            if item is not None and self._journal_proof_is_valid(
                entry, item, repo_dict["commit_id"]
            ):
                latest[item["path"]] = entry["proof"]

        changed = False
        for path, proof in latest.items():
            item = by_path[path]
            if item.get("local_verified") is not True:
                item["local_verified"] = True
                changed = True
            if item.get("tos_verified") is not True or item.get("tos_verification") != proof:
                item["tos_verified"] = True
                item["tos_verification"] = proof
                changed = True
            source = self.source_path(item)
            deleted = not source.exists()
            if item.get("local_deleted") is not deleted:
                item["local_deleted"] = deleted
                changed = True
        return changed

    def _append_proof(self, item, commit_id, proof):
        entry = {
            "identity": self._item_identity(item, commit_id),
            "proof": proof,
        }
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._journal_write_lock:
            with self.journal_path.open("a", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())

    def _clear_journal(self):
        try:
            self.journal_path.unlink()
        except FileNotFoundError:
            return
        directory_fd = os.open(self.journal_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @staticmethod
    def _retryable_download_error(error):
        if isinstance(error, (httpx.TransportError, TimeoutError, ConnectionError)):
            return True
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        return isinstance(error, HfHubHTTPError) and (
            status == 429 or (isinstance(status, int) and status >= 500)
        )

    def _download_with_retry(self, item, file_path, commit_id):
        last_error = None
        for attempt in range(1, self.download_max_attempts + 1):
            try:
                from huggingface_hub import hf_hub_download

                hf_hub_download(
                    repo_id=self.repo_id,
                    filename=item["path"],
                    repo_type="dataset",
                    revision=commit_id,
                    local_dir=self.save_dir,
                    endpoint=self.endpoint,
                    token=self.token,
                )
                if not file_path.is_file() or file_path.stat().st_size != item["size"]:
                    raise ValueError(
                        f"下载后文件大小不符: {item['path']}，期望 {item['size']} bytes"
                    )
                return
            except Exception as error:
                last_error = error
                if attempt >= self.download_max_attempts or not self._retryable_download_error(error):
                    raise
                delay = min(self.retry_base_seconds * (2 ** (attempt - 1)), 120)
                delay *= random.uniform(0.8, 1.2)
                log.warning(
                    "下载临时失败，将重试 %s/%s，等待 %.1f 秒: %s (%s)",
                    attempt,
                    self.download_max_attempts,
                    delay,
                    item["path"],
                    error,
                )
                time.sleep(delay)
        raise last_error  # pragma: no cover - loop always returns or raises

    def _verify_local(self, item, file_path):
        size = file_path.stat().st_size
        if size != item["size"]:
            raise ValueError(
                f"文件大小不符: {file_path}，期望 {item['size']}，实际 {size}"
            )
        if item.get("sha256"):
            with file_path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != item["sha256"]:
                raise ValueError(f"SHA256 不符: {file_path}")
        elif item.get("blob_id"):
            digest = hashlib.sha1()
            digest.update(f"blob {size}\0".encode("ascii"))
            with file_path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != item["blob_id"]:
                raise ValueError(f"Git blob SHA1 不符: {file_path}")

    @staticmethod
    def _parse_number(output, pattern, description):
        match = re.search(pattern, output, flags=re.MULTILINE | re.IGNORECASE)
        if match is None:
            raise RuntimeError(f"无法从 tosutil 输出解析 {description}:\n{output}")
        return int(match.group(1))

    @staticmethod
    def _is_missing_object(output):
        no_such_key = re.search(
            r"\bcode\s*(?:[:=]\s*)?\[?[\"']?NoSuchKey\b",
            output,
            flags=re.IGNORECASE,
        ) is not None
        missing_code = (
            re.search(r"\bStatus\s*\[\s*404\s*\]", output, flags=re.IGNORECASE)
            and re.search(
                r"\berror message\s*\[\s*0017-00000003\s*\]",
                output,
                flags=re.IGNORECASE,
            )
        )
        return bool(no_such_key or missing_code)

    def _stat_remote(self, uri):
        result = subprocess.run(
            ["tosutil", "stat", uri, "-sbh"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode == 0:
            return {
                "size": self._parse_number(result.stdout, r"^\s*Size:\s*(\d+)\s*$", "对象大小"),
                "crc64": str(self._parse_number(
                    result.stdout,
                    r"^\s*HashCrc64ecma:\s*(\d+)\s*$",
                    "远端 CRC64",
                )),
            }
        if self._is_missing_object(result.stdout):
            return None
        raise RuntimeError(f"TOS 对象查询失败，不能视为对象不存在:\n{result.stdout}")

    def _crc64_local(self, file_path):
        result = subprocess.run(
            ["tosutil", "hash", str(file_path), "-type=crc64"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"本地 CRC64 计算失败:\n{result.stdout}")
        return str(self._parse_number(
            result.stdout, r"^\s*crc64:\s*(\d+)\s*$", "本地 CRC64"
        ))

    def _ensure_remote(self, item, file_path, commit_id, local_crc64):
        uri = f"{self.tos_url.rstrip('/')}/{item['path']}"
        remote = self._stat_remote(uri)
        if remote is not None:
            if remote["size"] != item["size"] or remote["crc64"] != local_crc64:
                raise ValueError(f"TOS 已有对象大小或 CRC64 不符，停止且不覆盖: {uri}")
        else:
            upload = subprocess.run(
                ["tosutil", "cp", str(file_path), uri, "-vchecksum"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=3600,
            )
            if upload.returncode != 0:
                raise RuntimeError(f"TOS 上传失败:\n{upload.stdout}")
            log.info("并行对象上传完成，开始远端核验: %s", uri)
            remote = self._stat_remote(uri)
            if remote is None:
                raise RuntimeError(f"上传后未找到 TOS 对象: {uri}")
            if remote["size"] != item["size"] or remote["crc64"] != local_crc64:
                raise ValueError(f"TOS 上传后大小或 CRC64 不符: {uri}")
        return {
            "identity": self._item_identity(item, commit_id),
            "uri": uri,
            "size": item["size"],
            "local_crc64": local_crc64,
            "remote_crc64": remote["crc64"],
            "method": "size_crc64",
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }

    def _cleanup_resumed_local(self, item, file_path):
        if not file_path.exists():
            item["local_deleted"] = True
            return
        try:
            self._verify_local(item, file_path)
            crc = self._crc64_local(file_path)
            proof = item.get("tos_verification") or {}
            if crc != proof.get("local_crc64"):
                log.warning("本地残留文件与已验证对象不同，保留不删: %s", file_path)
                return
            stat = file_path.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_size,
                         stat.st_mtime_ns, stat.st_ctime_ns)
            current = file_path.stat()
            if signature != (current.st_dev, current.st_ino, current.st_size,
                             current.st_mtime_ns, current.st_ctime_ns):
                log.warning("本地残留文件在清理前发生变化，保留不删: %s", file_path)
                return
            file_path.unlink()
            directory_fd = os.open(file_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            item["local_deleted"] = True
        except Exception:
            # Remote delivery proof remains valid; a local cleanup problem must
            # not cause a duplicate remote upload or erase the verified state.
            log.exception("清理已验证对象的本地残留失败，保留本地文件: %s", file_path)

    def _process_one(self, item, commit_id):
        file_path = self.source_path(item)
        if self.can_resume_remote(item, commit_id):
            if item.get("local_deleted") is not True:
                self._cleanup_resumed_local(item, file_path)
            return

        if file_path.exists() and file_path.stat().st_size == item["size"]:
            log.info("复用本地文件，进入并行核验: %s", item["path"])
        else:
            self._download_with_retry(item, file_path, commit_id)
        self._verify_local(item, file_path)
        local_crc64 = self._crc64_local(file_path)
        proof = self._ensure_remote(item, file_path, commit_id, local_crc64)

        # fsync the compact proof before unlinking the only local copy. The
        # manifest is checkpointed once per batch, not once per field/file.
        self._append_proof(item, commit_id, proof)
        item["local_verified"] = True
        item["tos_verified"] = True
        item["tos_verification"] = proof

        local_stat = file_path.stat()
        signature = (
            local_stat.st_dev,
            local_stat.st_ino,
            local_stat.st_size,
            local_stat.st_mtime_ns,
            local_stat.st_ctime_ns,
        )
        current = file_path.stat()
        if signature != (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
        ):
            raise RuntimeError(f"本地文件在核验期间发生变化，保留不删: {file_path}")
        file_path.unlink()
        directory_fd = os.open(file_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        item["local_deleted"] = True
        log.info("TOS 核验通过并清理本地文件: %s", item["path"])

    def _reserve_space(self, size):
        with HuggingfaceSpider._space_lock:
            available = (
                shutil.disk_usage(self.save_dir).free
                - HuggingfaceSpider._reserved_bytes
                - self.reserve_bytes
            )
            if size > available:
                return False
            HuggingfaceSpider._reserved_bytes += size
            return True

    @staticmethod
    def _release_space(size):
        if size:
            with HuggingfaceSpider._space_lock:
                HuggingfaceSpider._reserved_bytes -= size

    def process_parallel(self, repo_dict):
        items = repo_dict["file_manifest"]
        seen = set()
        for item in items:
            self.source_path(item)
            if (
                item["path"] in seen
                or type(item.get("size")) is not int
                or item["size"] < 0
            ):
                raise ValueError("清单存在重复路径或无效文件大小")
            seen.add(item["path"])

        pending: dict[Future, tuple[dict, int]] = {}
        cursor = 0
        first_error = None
        log.info("端到端并行文件 worker 数: %s", self.parallel_workers)
        with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
            while cursor < len(items) or pending:
                while cursor < len(items) and len(pending) < self.parallel_workers and first_error is None:
                    item = items[cursor]
                    cursor += 1
                    path = self.source_path(item)
                    if self.can_resume_remote(item, repo_dict["commit_id"]):
                        if item.get("local_deleted") is not True:
                            self._cleanup_resumed_local(item, path)
                        continue
                    local_ready = path.is_file() and path.stat().st_size == item["size"]
                    needed = 0 if local_ready else item["size"]
                    if needed and not self._reserve_space(needed):
                        cursor -= 1
                        if pending:
                            break
                        raise OSError(f"磁盘空间不足，无法安全暂存: {item['path']}")
                    future = executor.submit(self._process_one, item, repo_dict["commit_id"])
                    pending[future] = (item, needed)

                if not pending:
                    if cursor >= len(items) or first_error is not None:
                        break
                    continue

                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    _, reserved = pending.pop(future)
                    self._release_space(reserved)
                    try:
                        future.result()
                    except Exception as error:
                        if first_error is None:
                            first_error = error
                        log.exception("并行文件处理失败: %s", error)

        if first_error is not None:
            raise first_error

    def download_file(self):
        with archive_state.dataset_lock(self.save_dir, self.tos_url):
            repo_dict = self.get_manifest()
            archive_state.initialize(repo_dict)
            if self._replay_journal(repo_dict):
                self.save_manifest(repo_dict)

            complete = archive_state.transfer_complete(repo_dict, self.tos_url)
            if repo_dict.get("status") is True and complete:
                changed = False
                for item in repo_dict["file_manifest"]:
                    path = self.source_path(item)
                    if not item.get("local_deleted") and not path.exists():
                        item["local_deleted"] = True
                        changed = True
                if changed:
                    self.save_manifest(repo_dict)
                self._clear_journal()
                log.info("清单已标记文件传输完成: %s", self.repo_id)
                return

            if not complete:
                repo_dict["status"] = False
                archive_state.invalidate_delivery(repo_dict)
                self.save_manifest(repo_dict)

            try:
                self.process_parallel(repo_dict)
            finally:
                # Journal proofs are durable before local deletion. Merge them
                # into the canonical manifest even when another worker failed.
                self._replay_journal(repo_dict)
                complete = archive_state.transfer_complete(repo_dict, self.tos_url)
                repo_dict["status"] = complete
                self.save_manifest(repo_dict)
                if complete:
                    self._clear_journal()


class ParallelDatasetScheduler(DatasetScheduler):
    """Reuse config watching and scheduling, but launch the parallel spider."""

    def __init__(self):
        super().__init__()
        self.failure_counts = {}

    def collect_finished(self):
        for repo_id, future in list(self.running.items()):
            if not future.done():
                continue
            del self.running[repo_id]
            try:
                future.result()
                self.finished.add(repo_id)
                self.failure_counts.pop(repo_id, None)
                log.info("并行数据集下载完成: %s", repo_id)
            except archive_state.LockBusyError as error:
                self.retry_after[repo_id] = time.monotonic() + 30
                log.warning("归档锁占用，稍后重试 %s: %s", repo_id, error)
            except Exception:
                failures = self.failure_counts.get(repo_id, 0) + 1
                self.failure_counts[repo_id] = failures
                delay = min(30 * (2 ** min(failures - 1, 6)), 1800)
                self.retry_after[repo_id] = time.monotonic() + delay
                log.exception(
                    "并行数据集任务失败，将在 %.0f 秒后自动续跑（第 %s 次）: %s",
                    delay,
                    failures,
                    repo_id,
                )

    def submit_tasks(self, executor):
        max_workers = self.loader["max_workers"]
        free_workers = max_workers - len(self.running)
        if free_workers <= 0:
            return
        for dataset in self.datasets:
            repo_id = dataset["repo_id"]
            if repo_id in self.running or repo_id in self.finished:
                continue
            if time.monotonic() < self.retry_after.get(repo_id, 0):
                continue
            spider = ParallelHuggingfaceSpider(dataset.copy(), self.loader.copy())
            future = executor.submit(spider.download_file)
            self.retry_after.pop(repo_id, None)
            self.running[repo_id] = future
            free_workers -= 1
            log.info("开始并行下载数据集: %s", repo_id)
            if free_workers == 0:
                break


if __name__ == "__main__":
    ParallelDatasetScheduler().run()
