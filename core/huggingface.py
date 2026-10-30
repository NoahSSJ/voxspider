from datetime import datetime, timezone
import threading
from typing import Any
import subprocess
import os

# huggingface_hub 在导入时读取超时配置，单位为秒。
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "300"
os.environ["HF_HUB_ETAG_TIMEOUT"] = "60"

from huggingface_hub import HfApi, hf_hub_download, snapshot_download, RepoFile
import sys
import json
import shutil
from pprint import pprint
from dotenv import load_dotenv
import yaml
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path, PurePosixPath
import shutil
import hashlib
import time
import random
import re
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
from contextlib import closing

if __package__:
    from . import archive_state
else:
    import archive_state

def get_root_dir():
    current_dir = Path(__file__).parent.resolve()
    for dir in (current_dir, *current_dir.parents):
        if dir.joinpath("pyproject.toml").exists():
            return dir
    raise RuntimeError("错误")

root_dir = get_root_dir()
# 显式加载项目配置；已导出的环境变量优先。
load_dotenv(root_dir / ".env", override=False)
yaml_path = (root_dir / "config").joinpath('hgf.yaml')
# print(root_dir)
def setup_logger(dataset_name: str = "test"):
    log_dir = root_dir / "logs" / dataset_name
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir.joinpath("run.log")
    logger = logging.getLogger(dataset_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d | %(funcName)s | %(message)s")
    file_handler = RotatingFileHandler(
        filename=log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    console_handler = logging.StreamHandler()
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

log = setup_logger()    


class HuggingfaceSpider():
    _space_lock = threading.Lock()
    _reserved_bytes = 0

    def __init__(self, dataset: dict[str, Any], loader=None):
        loader = loader or {}
        self.max_workers = 3
        self.endpoint = loader.get("endpoint", "https://huggingface.co")
        self.download_workers = loader.get("download_workers", 8)
        self.reserve_bytes = loader.get("reserve_bytes", 1024**3)
        if type(self.download_workers) is not int or self.download_workers < 1:
            raise ValueError("download_workers must be a positive integer")
        if type(self.reserve_bytes) is not int or self.reserve_bytes < 0:
            raise ValueError("reserve_bytes must be a nonnegative integer")
        self.retry_base_time = 10
        self.repo_id = dataset['repo_id']
        self.repo_type = "dataset"
        self.save_name = dataset['name']
        self.save_dir: Path = root_dir / "datasets" / self.save_name
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.token = os.getenv("HF_TOKEN")
        self.api = HfApi(endpoint=self.endpoint, token=self.token)
        self.tos_url = dataset['tos_url']
    
    
    
    def save_manifest(self, repo_dict):
        manifest_path = self.save_dir / "repo_manifest.json"
        temp_path = self.save_dir / "repo_manifest.json.tmp"

        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(repo_dict, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, manifest_path)
        # 删除源文件前，确保清单的原子替换也已持久化。
        directory_fd = os.open(self.save_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    
    def get_manifest(self):
        manifest_path = self.save_dir / "repo_manifest.json"
        if manifest_path.exists():
            repo_dict = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )

            if repo_dict.get("id") != self.repo_id:
                raise ValueError("清单仓库 ID 与当前配置不一致")
            if not repo_dict.get("commit_id"):
                raise ValueError("清单缺少 commit_id")

            archive_state.initialize(repo_dict)
            for item in repo_dict["file_manifest"]:
                item.setdefault("local_verified", False)
                item.setdefault("tos_verified", False)
                item.setdefault("local_deleted", False)

            self.save_manifest(repo_dict)
            return repo_dict  # 这里不能漏
        
        repo_dict = dict(archive_state.DEFAULTS)
        info = self.api.dataset_info(self.repo_id)
        pprint(info)
        id = info.id
        author = info.author
        card_data = info.card_data.to_dict() if info.card_data is not None else {}
        language = card_data.get('language')
        license = card_data.get('license')
        task_categories = card_data.get('task_categories')
        citation = info.citation
        created_at = (
            info.created_at.isoformat()
            if info.created_at is not None
            else None
        )
        description = info.description
        commit_id = info.sha
        tags = info.tags
        used_storage = info.used_storage
        main_size = info.main_size
        repo_dict.update({
            "id": id,
            "author": author,
            "card_data": card_data,
            "language": language,
            "license": license,
            "task_categories": task_categories,
            "citation": citation,
            "created_at": created_at,
            "description": description,
            "commit_id": commit_id,
            "tags": tags,
            "used_storage": used_storage,
            "main_size": main_size,
        })
        items = self.api.list_repo_tree(
            repo_id=self.repo_id,
            repo_type=self.repo_type,
            revision=commit_id,
            recursive=True
        )
        file_manifest = []
        for index, item in enumerate(items, start=1):
            log.info(f"index:%s, item:%s", index, item)
            if isinstance(item, RepoFile):
                path = item.path
                size = item.size
                blob_id = item.blob_id
                lf_sha256 = item.lfs.sha256 if item.lfs is not None else None
                print(path, size, blob_id, lf_sha256)
                file_dict = {
                    "path": path,
                    "size": size,
                    "blob_id": blob_id,
                    "sha256": lf_sha256,
                    "local_verified": False,
                    "tos_verified": False,
                    "local_deleted": False,
                }
                file_manifest.append(file_dict)
        repo_dict['file_manifest'] = file_manifest
        # pprint(repo_dict)
        repo_dict_path = self.save_dir / "repo_manifest.json"
        # with open(repo_dict_path, "w", encoding="utf-8") as f:
            # json.dump(repo_dict, f, ensure_ascii=False, indent=2)
        # Path(repo_dict_path).write_text(data=json.dumps(repo_dict, ensure_ascii=False, indent=2), encoding='utf-8')
        # return repo_dict
        self.save_manifest(repo_dict)
        return repo_dict

    def source_path(self, item):
        path = item['path']
        relative = PurePosixPath(path)
        if (not path or relative.is_absolute() or '..' in relative.parts
                or str(relative) != path or '\\' in path
                or relative.parts[0] in {'.cache', 'repo_manifest.json',
                    'repo_manifest.json.tmp', 'dataset_card.yaml',
                    'source_reconciliation.json', 'archive_report.json',
                    'card_statistics.json', 'upstream_evidence.json',
                    '.archive.lock', 'card_generation_decision.json',
                    'card_publication.json', 'card_generation_report.json'}):
            raise ValueError(f"不能下载或清理管理文件/非法路径: {path!r}")
        file_path = self.save_dir / path
        # 不跟随符号链接，也不允许通过父目录链接删除工作目录外的文件。
        if any(part.is_symlink() for part in (file_path, *file_path.parents)):
            raise ValueError(f"不能清理符号链接路径: {file_path}")
        if file_path.exists() and not file_path.is_file():
            raise ValueError(f"本地路径不是普通文件: {file_path}")
        return file_path

    def can_resume_remote(self, item, commit_id):
        # 续跑复用清单中的完整核验记录；远端全量复核留给最终交付。
        self.source_path(item)
        size = item['size']
        remote_path = f"{self.tos_url.rstrip('/')}/{item['path']}"
        identity = dict(repo_id=self.repo_id, commit_id=commit_id,
                        **{key: item[key] for key in ('path', 'size', 'sha256', 'blob_id')})
        previous = item.get("tos_verification") or {}
        return (
            item.get("local_verified") is True
            and item.get("tos_verified") is True
            and previous.get("identity") == identity
            and previous.get("uri") == remote_path
            and previous.get("size") == size
            and previous.get("method") == "size_crc64"
            and isinstance(previous.get("local_crc64"), str)
            and re.fullmatch(r"\d+", previous["local_crc64"]) is not None
            and previous.get("remote_crc64") == previous["local_crc64"]
        )

    def prefetch(self, repo_dict):
        items = repo_dict['file_manifest']
        seen = set()
        for item in items:
            self.source_path(item)
            if item['path'] in seen or type(item['size']) is not int or item['size'] < 0:
                raise ValueError("Duplicate source path or invalid file size")
            seen.add(item['path'])
        pending = deque()
        cursor = 0
        reserved = 0
        log.info("Download prefetch workers: %s", self.download_workers)
        try:
            with ThreadPoolExecutor(max_workers=self.download_workers) as executor:
                try:
                    while cursor < len(items) or pending:
                        while cursor < len(items) and len(pending) < self.download_workers:
                            item = items[cursor]
                            resume = self.can_resume_remote(item, repo_dict['commit_id'])
                            local_path = self.source_path(item)
                            local_ready = local_path.exists() and local_path.stat().st_size == item['size']
                            # Existing files are verified by the consumer before upload.
                            download_needed = not resume and not local_ready
                            # Reserve full sizes across datasets, including partial caches.
                            needed = item['size'] if download_needed else 0
                            with HuggingfaceSpider._space_lock:
                                available = (shutil.disk_usage(self.save_dir).free
                                             - HuggingfaceSpider._reserved_bytes - self.reserve_bytes)
                                if needed and needed > available:
                                    if pending:
                                        break
                                    raise OSError(f"Insufficient download space: {item['path']}")
                                HuggingfaceSpider._reserved_bytes += needed
                                reserved += needed
                            future = None if not download_needed else executor.submit(
                                hf_hub_download, repo_id=self.repo_id, filename=item['path'],
                                repo_type='dataset', revision=repo_dict['commit_id'],
                                local_dir=self.save_dir, endpoint=self.endpoint, token=self.token)
                            pending.append((item, future, needed))
                            cursor += 1
                        item, future, needed = pending.popleft()
                        if future is not None:
                            future.result()
                        yield item
                        with HuggingfaceSpider._space_lock:
                            HuggingfaceSpider._reserved_bytes -= needed
                            reserved -= needed
                finally:
                    for _, future, _ in pending:
                        if future is not None:
                            future.cancel()
        finally:
            # Active downloads finish before their space reservations are released.
            with HuggingfaceSpider._space_lock:
                HuggingfaceSpider._reserved_bytes -= reserved

    def download_file(self):
        # 下载器与交付任务共用锁；锁定后才读取清单，避免覆盖其他阶段的新状态。
        with archive_state.dataset_lock(self.save_dir, self.tos_url):
            repo_dict = self.get_manifest()
            changed = archive_state.initialize(repo_dict)
            complete = archive_state.transfer_complete(repo_dict, self.tos_url)
            if repo_dict['status'] is True and complete:
                for item in repo_dict['file_manifest']:
                    local_path = self.source_path(item)
                    if not item.get('local_deleted') and not local_path.exists():
                        item['local_deleted'] = True
                        changed = True
                if changed:
                    self.save_manifest(repo_dict)
                log.info("清单已标记文件传输完成: %s", self.repo_id)
                return
            if not complete:
                repo_dict['status'] = False
                archive_state.invalidate_delivery(repo_dict)
                self.save_manifest(repo_dict)
            try:
                with closing(self.prefetch(repo_dict)) as items:
                    self.process_downloads(repo_dict, items)
            finally:
                # 本地清理失败不抹去已完成的两端校验事实。
                complete = archive_state.transfer_complete(repo_dict, self.tos_url)
                if repo_dict.get('status') is not complete:
                    repo_dict['status'] = complete
                    self.save_manifest(repo_dict)

    def process_downloads(self, repo_dict, items):
        commit_id = repo_dict['commit_id']
        for index, item in enumerate(items, start=1):
            if self.can_resume_remote(item, commit_id):
                # 处理删除成功但删除状态尚未保存就退出的情况。
                if not item.get("local_deleted") and not self.source_path(item).exists():
                    item["local_deleted"] = True
                    self.save_manifest(repo_dict)
                log.info("复用清单核验记录，跳过已完成文件: %s", item['path'])
                continue
            print(f"index:{index}, item:{item}")
            path = item['path']
            file_path = self.source_path(item)
            size = item['size']
            blob_id = item['blob_id']
            sha256 = item['sha256']
            remote_path = f"{self.tos_url.rstrip('/')}/{path}"
            identity = {
                "repo_id": self.repo_id, "commit_id": commit_id,
                "path": path, "size": size, "sha256": sha256, "blob_id": blob_id,
            }
            item['local_verified'] = False
            item['local_deleted'] = False
            self.save_manifest(repo_dict)
            log.info("Download completed - local_path:%s", self.save_dir)
            file_path = self.save_dir / path
            actual_size = file_path.stat().st_size
            if actual_size != size:
                raise ValueError(f"File size mismatch for {file_path}: expected {size}, got {actual_size}")
            else:
                log.info(f"File size verified for {file_path}: {actual_size} bytes")
            if sha256:
                # file_bytes = Path(file_path).read_bytes()
                # file_hash = hashlib.sha256(file_bytes).hexdigest()
                with Path(file_path).open('rb') as f:
                    file_hash = hashlib.file_digest(f, "sha256").hexdigest()
                if file_hash != sha256:
                    raise ValueError(f"SHA256 mismatch for {file_path}: expected {sha256}, got {file_hash}")
                else:
                    log.info(f"SHA256 verified for {file_path}: {file_hash}")
            elif blob_id:
                git_hash = hashlib.sha1()
                git_hash.update(f"blob {actual_size}\0".encode("ascii"))
                with file_path.open("rb") as f:
                    while chunk := f.read(1024 * 1024):
                        git_hash.update(chunk)
                actual_blob_id = git_hash.hexdigest()
                if actual_blob_id != blob_id:
                    raise ValueError(
                        f"Blob ID mismatch: expected {blob_id}, got {actual_blob_id}"
                    )
                else:
                    log.info(f"blob_id verified for {file_path}: {actual_blob_id}")
            item["local_verified"] = True
            self.save_manifest(repo_dict)
            log.info(f"Successfully downloaded and verified file: {file_path}")
            local_stat = file_path.stat()
            local_signature = (local_stat.st_dev, local_stat.st_ino, local_stat.st_size,
                               local_stat.st_mtime_ns, local_stat.st_ctime_ns)
            remote_path = f"{self.tos_url.rstrip('/')}/{path}"
            if item.get("local_verified") is not True:
                raise RuntimeError(f"文件尚未通过本地核验: {file_path}")

            item["tos_verified"] = False
            item["tos_verification"] = None
            self.save_manifest(repo_dict)
                
            try:
                # 1. 计算已经通过本地核验的文件的 CRC64
                hash_result = subprocess.run(
                    ["tosutil", "hash", str(file_path), "-type=crc64"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )

                if hash_result.returncode != 0:
                    raise RuntimeError(
                        f"计算本地 CRC64 失败:\n{hash_result.stdout}"
                    )

                crc_match = re.search(
                    r"^\s*crc64:\s*(\d+)\s*$",
                    hash_result.stdout,
                    flags=re.MULTILINE | re.IGNORECASE,
                )
                if crc_match is None:
                    raise RuntimeError(
                        f"无法解析本地 CRC64:\n{hash_result.stdout}"
                    )

                local_crc64 = int(crc_match.group(1))

                # 2. 最多查询两次：
                # 第一次检查目标是否存在；不存在则上传，再查询一次。
                for check_index in range(2):
                    stat_result = subprocess.run(
                        ["tosutil", "stat", remote_path, "-sbh"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        check=False,
                        timeout=120,
                    )

                    if stat_result.returncode == 0:
                        # 3. 目标存在，解析大小和服务端 CRC64
                        size_match = re.search(
                            r"^\s*Size:\s*(\d+)\s*$",
                            stat_result.stdout,
                            flags=re.MULTILINE | re.IGNORECASE,
                        )
                        remote_crc_match = re.search(
                            r"^\s*HashCrc64ecma:\s*(\d+)\s*$",
                            stat_result.stdout,
                            flags=re.MULTILINE | re.IGNORECASE,
                        )

                        if size_match is None or remote_crc_match is None:
                            raise RuntimeError(
                                "远端缺少大小或 CRC64，或输出格式无法解析；"
                                f"不能标记核验通过:\n{stat_result.stdout}"
                            )

                        remote_size = int(size_match.group(1))
                        remote_crc64 = int(remote_crc_match.group(1))

                        if remote_size != size:
                            raise ValueError(
                                f"TOS 文件大小不符，停止处理且不覆盖: {remote_path}\n"
                                f"期望={size}, 实际={remote_size}"
                            )

                        if remote_crc64 != local_crc64:
                            raise ValueError(
                                f"TOS CRC64 不符，停止处理且不覆盖: {remote_path}\n"
                                f"本地={local_crc64}, 远端={remote_crc64}"
                            )

                        # 已有对象一致也会走到这里，直接复用，不重复上传。
                        break

                    # 只将明确的对象不存在响应视为可上传。
                    # 网络、权限、桶不存在等错误均不能触发上传。
                    output = stat_result.stdout
                    has_no_such_key = re.search(
                        r"\bcode\s*(?:[:=]\s*)?\[?[\"']?NoSuchKey\b",
                        output,
                        flags=re.IGNORECASE,
                    ) is not None
                    has_object_missing_code = (
                        re.search(
                            r"\bStatus\s*\[\s*404\s*\]",
                            output,
                            flags=re.IGNORECASE,
                        ) is not None
                        and re.search(
                            r"\berror message\s*\[\s*0017-00000003\s*\]",
                            output,
                            flags=re.IGNORECASE,
                        ) is not None
                    )
                    missing_object = has_no_such_key or has_object_missing_code

                    if not missing_object:
                        raise RuntimeError(
                            f"TOS 对象查询失败:\n{output}"
                        )

                    if check_index == 1:
                        raise RuntimeError(
                            f"上传完成后仍查询不到目标对象: {remote_path}"
                        )

                    # 4. 目标明确不存在，上传并开启传输一致性校验
                    upload_result = subprocess.run(
                        [
                            "tosutil", "cp",
                            str(file_path),
                            remote_path,
                            "-vchecksum",
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        check=False,
                    )

                    if upload_result.returncode != 0:
                        raise RuntimeError(
                            f"TOS 上传失败:\n{upload_result.stdout}"
                        )

                    log.info("上传成功，开始远端核验: %s", remote_path)

                # 5. 只有远端大小、CRC64 都匹配才保存 True
                item["tos_verification"] = {
                    "identity": identity,
                    "uri": remote_path,
                    "size": remote_size,
                    "local_crc64": str(local_crc64),
                    "remote_crc64": str(remote_crc64),
                    "method": "size_crc64",
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                }
                item["tos_verified"] = True
                self.save_manifest(repo_dict)

                log.info("TOS 单文件核验通过: %s", remote_path)

                # 6. 远端核验结果持久保存后，才删除确切的本地源文件。
                if any(part.is_symlink() for part in (file_path, *file_path.parents)):
                    raise ValueError(f"清理前发现符号链接: {file_path}")
                current = file_path.stat()
                signature = (current.st_dev, current.st_ino, current.st_size,
                             current.st_mtime_ns, current.st_ctime_ns)
                if signature != local_signature:
                    item["local_verified"] = False
                    self.save_manifest(repo_dict)
                    raise RuntimeError(f"本地文件在核验后发生变化，保留不删: {file_path}")
                file_path.unlink()
                directory_fd = os.open(file_path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                item["local_deleted"] = True
                self.save_manifest(repo_dict)
                log.info("本地文件已清理，继续下一个文件: %s", file_path)

            except Exception:
                log.exception("TOS 上传、核验或本地清理失败: %s", remote_path)
                raise
        return  
    
    def tos_upload(self, file_path: Path, bucket_name: str, object_name: str):
        # Implement the logic to upload the file to TOS (Tencent Object Storage)
        # This is a placeholder for the actual upload code
        log.info(f"Uploading {file_path} to TOS bucket {bucket_name} as {object_name}")
        # Example: Use boto3 or any other library to perform the upload
        # s3_client.upload_file(str(file_path), bucket_name, object_name)
        log.info(f"Upload completed for {file_path} to TOS bucket {bucket_name}")
        
    @classmethod
    def run(cls, datasets: list[dict[str, Any]]):
        with ThreadPoolExecutor(max_workers=3) as executor:

            futures = [executor.submit(HuggingfaceSpider(dataset=ds).download_file) for ds in datasets[:3]]
        for future in as_completed(futures):
            try:
                result = future.result()
                log.info("处理完成:%s", result)
            except Exception as e:
                log.info("处理失败:%s", e)
        




# total, used, free = shutil.disk_usage("/")
# log.info(f"Disk usage - Total: {total}, Used: {used}, Free: {free}")

# size = (Path(r'logs\test\run.log').stat().st_size) / (1024 ** 2)
# log.info(f"File size: {size}Mib")

# HuggingfaceSpider.run(loader=loader)

class SpiderConfig():
    @staticmethod
    def get_loader():
        loader = yaml.safe_load(yaml_path.read_text(encoding='utf-8'))['loader']
        log.info("配置已经加载:%s", loader)
        return loader

    @staticmethod
    def get_datasets():
        datasets = yaml.safe_load(yaml_path.read_text(encoding='utf-8'))['datasets']
        datasets = [ds for ds in datasets if ds['enabled'] == True and ds['status'] == False]
        log.info("目前的数据集下载任务为:%s", datasets)
        return datasets

class ConfigHandler(FileSystemEventHandler):
    def __init__(self, reload_event):
        self.reload_event = reload_event
        self._timer = None
        self._lock = threading.Lock()

    def on_modified(self, event):
        if event.is_directory:
            return

        if Path(event.src_path).resolve() != yaml_path.resolve():
            return

        # 防止一次保存触发多次事件
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()

            self._timer = threading.Timer(
                0.3,
                self.reload_event.set,
            )
            self._timer.daemon = True
            self._timer.start()

class DatasetScheduler:
    def __init__(self):
        self.reload_event = threading.Event()

        self.loader = SpiderConfig.get_loader()
        self.datasets = SpiderConfig.get_datasets()

        # 已经开始下载的任务
        self.running = {}

        # 已经执行结束的任务
        self.finished = set()
        self.retry_after = {}

    def reload_config(self):
        self.datasets = SpiderConfig.get_datasets()
        log.info("已重新加载 YAML")

    def collect_finished(self):
        for repo_id, future in list(self.running.items()):
            if not future.done():
                continue

            del self.running[repo_id]
            try:
                future.result()
                self.finished.add(repo_id)
                log.info("数据集下载完成: %s", repo_id)
            except archive_state.LockBusyError as exc:
                self.retry_after[repo_id] = time.monotonic() + 30
                log.warning("数据集锁被其他任务占用，30 秒后重试: %s (%s)", repo_id, exc.filename)
            except Exception:
                self.finished.add(repo_id)
                log.exception("数据集下载失败: %s", repo_id)

    def submit_tasks(self, executor):
        max_workers = self.loader["max_workers"]
        free_workers = max_workers - len(self.running)

        if free_workers <= 0:
            return

        for dataset in self.datasets:
            repo_id = dataset["repo_id"]

            if repo_id in self.running:
                continue

            if repo_id in self.finished:
                continue

            if time.monotonic() < self.retry_after.get(repo_id, 0):
                continue

            # 使用提交时的配置副本。
            # 任务提交后再修改 YAML，不会影响正在下载的任务。
            future = executor.submit(
                HuggingfaceSpider(
                    dataset=dataset.copy(), loader=self.loader.copy()
                ).download_file
            )

            self.retry_after.pop(repo_id, None)
            self.running[repo_id] = future
            free_workers -= 1

            log.info("开始下载数据集: %s", repo_id)

            # 只提交当前能够立即执行的任务，
            # 不提前往线程池队列里面塞任务。
            if free_workers == 0:
                break

    def run(self):
        max_workers = self.loader["max_workers"]

        handler = ConfigHandler(self.reload_event)

        observer = Observer()
        observer.schedule(
            handler,
            str(yaml_path.parent),
            recursive=False,
        )
        observer.start()

        log.info("正在监听 YAML: %s", yaml_path)

        try:
            with ThreadPoolExecutor(
                max_workers=max_workers
            ) as executor:

                while True:
                    changed = self.reload_event.wait(timeout=0.5)

                    if changed:
                        self.reload_event.clear()

                        try:
                            self.reload_config()
                        except Exception:
                            log.exception("重新读取 YAML 失败")

                    # 检查旧任务是否完成
                    self.collect_finished()

                    # 按照最新 YAML 提交新任务
                    self.submit_tasks(executor)

        except KeyboardInterrupt:
            log.info("程序退出")

        finally:
            observer.stop()
            observer.join()

if __name__ == "__main__":
    DatasetScheduler().run()


    






