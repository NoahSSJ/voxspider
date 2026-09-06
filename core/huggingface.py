import threading
from typing import Any
import subprocess
from huggingface_hub import HfApi, hf_hub_download, snapshot_download, RepoFile
import sys
import os
import json
import shutil
from pprint import pprint
from dotenv import load_dotenv
import yaml
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import shutil
import hashlib
import time
import random
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_root_dir():
    current_dir = Path(__file__).parent.resolve()
    for dir in (current_dir, *current_dir.parents):
        if dir.joinpath("pyproject.toml").exists():
            return dir
    raise RuntimeError("错误")

root_dir = get_root_dir()
yaml_path = root_dir.joinpath('config\\hgf.yaml')
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
    def __init__(self, dataset: dict[str, Any]):
        self.max_workers = 3
        self.endpoint = "https://huggingface.co"
        self.download_workers = 8
        self.retry_base_time = 10
        self.repo_id = dataset['repo_id']
        self.repo_type = "dataset"
        self.save_name = dataset['name']
        self.save_dir: Path = root_dir / "datasets" / self.save_name
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.api = HfApi(endpoint=self.endpoint, token=os.getenv("HF_TOKEN"))
    
    def get_manifest(self):
        repo_dict = {}
        info = self.api.dataset_info(self.repo_id)
        pprint(info)
        id = info.id
        author = info.author
        card_data = info.card_data.to_dict()
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
                    "sha256": lf_sha256
                }
                file_manifest.append(file_dict)
        repo_dict['file_manifest'] = file_manifest
        # pprint(repo_dict)
        repo_dict_path = self.save_dir / "repo_manifest.json"
        # with open(repo_dict_path, "w", encoding="utf-8") as f:
            # json.dump(repo_dict, f, ensure_ascii=False, indent=2)
        Path(repo_dict_path).write_text(data=json.dumps(repo_dict, ensure_ascii=False, indent=2), encoding='utf-8')
        return repo_dict

    def download_file(self):
        repo_dict = self.get_manifest()
        commit_id = repo_dict['commit_id']
        items = repo_dict['file_manifest']
        for index, item in enumerate(items, start=1):
            print(f"index:{index}, item:{item}")
            path = item['path']
            size = item['size']
            blob_id = item['blob_id']
            sha256 = item['sha256']
            log.info(f"index:{index}, path:{path}, size:{size}, blob_id:{blob_id}, sha256:{sha256}")
            # download the file
            flag = hf_hub_download(
                repo_id=self.repo_id, 
                filename=path, 
                repo_type="dataset",
                revision=commit_id,
                local_dir=self.save_dir,
                endpoint=self.endpoint,
            )
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
            log.info(f"Successfully downloaded and verified file: {file_path}")
            # 还有一个git的 blob_id可以验证文件的完整性，后面补上验证
            time.sleep(random.uniform(3, 5))  # Sleep for a random time between 0.5 and 1.5 seconds
            remote_path = (
                "tos://vox-asr-audio-data/"
                "SPEECH-DATA-TEMP/raw/dataset_repository/"
                f"tts/zh/example/source/{path}"
            )
            command = [
                "tosutil",
                "cp",
                str(file_path),
                remote_path,
                "-vchecksum",
            ]
            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        f"TOS 上传失败，exit_code={result.returncode}\n"
                        f"{result.stdout}"
                    )
                log.info(f"TOS 上传成功，exit_code={result.returncode}\n{result.stdout}")
                log.info(result.stdout)
                log.info(f"Successfully uploaded {file_path} to TOS at {remote_path}")
            except Exception as e:
                log.error("未安装tosutil")
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
        




total, used, free = shutil.disk_usage("/")
log.info(f"Disk usage - Total: {total}, Used: {used}, Free: {free}")

size = (Path(r'logs\test\run.log').stat().st_size) / (1024 ** 2)
log.info(f"File size: {size}Mib")

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

    def reload_config(self):
        self.datasets = SpiderConfig.get_datasets()
        log.info("已重新加载 YAML")

    def collect_finished(self):
        for repo_id, future in list(self.running.items()):
            if not future.done():
                continue

            del self.running[repo_id]
            self.finished.add(repo_id)

            try:
                future.result()
                log.info("数据集下载完成: %s", repo_id)
            except Exception:
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

            # 使用提交时的配置副本。
            # 任务提交后再修改 YAML，不会影响正在下载的任务。
            future = executor.submit(
                HuggingfaceSpider(
                    dataset=dataset.copy()
                ).download_file
            )

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


    






