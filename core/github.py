from pathlib import Path
import subprocess
import re
from urllib.parse import unquote, urlsplit

urls = [
    "https://zenodo.org/record/4590121/files/nptel-train.tar.gz.partaa",
    "https://zenodo.org/record/4590910/files/nptel-train.tar.gz.partab",
    "https://zenodo.org/record/4590927/files/nptel-train.tar.gz.partac",
    "https://zenodo.org/record/4591351/files/nptel-train.tar.gz.partad",
    "https://zenodo.org/record/4591367/files/nptel-train.tar.gz.partae",
    "https://zenodo.org/record/4591412/files/nptel-train.tar.gz.partaf",
    "https://zenodo.org/record/4591424/files/nptel-train.tar.gz.partag",
    "https://zenodo.org/record/4591448/files/nptel-train.tar.gz.partah",
    "https://zenodo.org/record/4591458/files/nptel-train.tar.gz.partai",
    "https://zenodo.org/record/4591466/files/nptel-train.tar.gz.partaj",
    "https://zenodo.org/record/4593165/files/nptel-train.tar.gz.partak",
    "https://zenodo.org/record/4593170/files/nptel-train.tar.gz.partal",
    "https://zenodo.org/record/4593172/files/nptel-train.tar.gz.partam",
    "https://zenodo.org/record/4593180/files/nptel-train.tar.gz.partan",
    "https://zenodo.org/record/4593184/files/nptel-train.tar.gz.partao",
    "https://zenodo.org/record/4593186/files/nptel-train.tar.gz.partap",
    "https://zenodo.org/record/4593188/files/nptel-train.tar.gz.partaq",
    "https://zenodo.org/record/4593192/files/nptel-train.tar.gz.partar",
    "https://zenodo.org/record/4593195/files/nptel-train.tar.gz.partas",
    "https://zenodo.org/record/4593199/files/nptel-train.tar.gz.partat",
]


SAVE_DIR = Path(__file__).resolve().parent
TOS_PREFIX = (
    "tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/asr/en/"
    "NPTEL2020-Indian-English-Speech-Dataset/source"
)


def download_file(url, save_dir=SAVE_DIR):
    """断点续传到明确的本地路径；wget 成功后才进入上传阶段。"""
    filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise ValueError(f"无效的下载文件名: {url}")
    save_dir = Path(save_dir).resolve()
    save_dir.mkdir(parents=True, exist_ok=True)
    file_path = save_dir / filename
    if file_path.is_symlink():
        raise ValueError(f"下载目标不能是符号链接: {file_path}")
    subprocess.run(["wget", "-c", "-O", str(file_path), url], check=True)
    return file_path


def upload_and_cleanup(file_path, tos_prefix=TOS_PREFIX):
    """上传后仅核对远端字节数，一致才删除本地源文件。"""
    file_path = Path(file_path)
    if any(path.is_symlink() for path in (file_path, *file_path.parents)):
        raise ValueError(f"源文件路径不能包含符号链接: {file_path}")
    before = file_path.stat()
    remote_path = f"{tos_prefix.rstrip('/')}/{file_path.name}"
    upload_result = subprocess.run(
        ["tosutil", "cp", str(file_path), remote_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if upload_result.returncode != 0:
        raise RuntimeError(f"TOS 上传失败，保留本地文件: {file_path}\n{upload_result.stdout}")

    stat_result = subprocess.run(
        ["tosutil", "stat", remote_path, "-sbh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=120,
    )
    if stat_result.returncode != 0:
        raise RuntimeError(f"TOS 查询失败，保留本地文件: {file_path}\n{stat_result.stdout}")
    match = re.search(r"^\s*Size:\s*(\d+)\s*$", stat_result.stdout, re.MULTILINE | re.IGNORECASE)
    if match is None:
        raise RuntimeError(f"无法解析 TOS 字节数，保留本地文件: {file_path}\n{stat_result.stdout}")
    remote_size = int(match.group(1))
    if remote_size != before.st_size:
        raise ValueError(
            f"TOS 文件大小不符，保留本地文件: {file_path} "
            f"本地={before.st_size}, 远端={remote_size}"
        )

    current = file_path.lstat()
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
    if any(getattr(before, field) != getattr(current, field) for field in fields):
        raise RuntimeError(f"本地文件在上传期间发生变化，保留不删: {file_path}")
    file_path.unlink()
    print(f"TOS 大小校验通过 ({remote_size} bytes)，已删除本地文件: {file_path}", flush=True)


def run(download_urls=None, save_dir=SAVE_DIR, tos_prefix=TOS_PREFIX):
    for url in urls if download_urls is None else download_urls:
        print(f"开始下载: {url}", flush=True)
        file_path = download_file(url, save_dir)
        print(f"下载完成，开始上传: {file_path}", flush=True)
        upload_and_cleanup(file_path, tos_prefix)


if __name__ == "__main__":
    run()
