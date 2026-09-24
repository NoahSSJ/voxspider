#!/usr/bin/env python3
"""逐片下载超大 HTTP 文件，使用 tosutil 上传独立 chunk 后立即删除本地分片。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

MIB = 1024 * 1024
SOURCE_URL = "https://dl.fbaipublicfiles.com/mls/mls_lm_polish.tar.gz"
EXPECTED_MD5 = "4785245b3b1c2d7eb9abf1175ad58431"
TOS_SOURCE_PREFIX = (
    "tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/"
    "asr/multi/mTEDx/source/"
)
STATE_FILE = Path(".mls-lm-polish-chunks-upload.json")
PART_MANIFEST = Path("mls_lm_polish.tar.gz.parts.json")
MISSING_MARKERS = ("status [404]", "nosuchkey", "not found", "0017-00000003")
STAT_SIZE = re.compile(r"^Size:\s*\n\s*(\d+)\s*$", re.MULTILINE)


class TransferError(RuntimeError):
    pass


def source_info(session: requests.Session, timeout: int) -> dict[str, Any]:
    response = session.head(SOURCE_URL, allow_redirects=True, timeout=(timeout, timeout))
    response.raise_for_status()
    length = response.headers.get("Content-Length")
    if length is None or int(length) <= 0:
        raise TransferError("源站 HEAD 未返回合法 Content-Length")
    return {
        "url": SOURCE_URL,
        "final_url": response.url,
        "size": int(length),
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
    }


def range_headers(info: dict[str, Any], start: int, end: int) -> dict[str, str]:
    headers = {"Range": f"bytes={start}-{end}"}
    validator = info.get("etag") or info.get("last_modified")
    if validator:
        headers["If-Range"] = validator
    return headers


def download_range(
    session: requests.Session,
    info: dict[str, Any],
    start: int,
    end: int,
    output: Any | None,
    whole_hasher: Any,
    part_hasher: Any | None,
    timeout: int,
) -> None:
    response = session.get(
        info["final_url"],
        headers=range_headers(info, start, end),
        stream=True,
        timeout=(timeout, timeout),
    )
    response.raise_for_status()
    expected = f"bytes {start}-{end}/"
    content_range = response.headers.get("Content-Range", "")
    if response.status_code != 206 or not content_range.startswith(expected):
        response.close()
        raise TransferError(
            f"Range 响应错误: status={response.status_code}, "
            f"Content-Range={content_range!r}, expected={expected!r}"
        )
    received = 0
    try:
        for block in response.iter_content(chunk_size=8 * MIB):
            if not block:
                continue
            received += len(block)
            whole_hasher.update(block)
            if part_hasher is not None:
                part_hasher.update(block)
            if output is not None:
                output.write(block)
    finally:
        response.close()
    wanted = end - start + 1
    if received != wanted:
        raise TransferError(f"分片下载不完整: wanted={wanted}, received={received}")


def retry(label: str, attempts: int, operation: Any) -> None:
    for attempt in range(1, attempts + 1):
        try:
            operation()
            return
        except (requests.RequestException, OSError, TransferError) as exc:
            if attempt == attempts:
                raise TransferError(f"{label} 连续失败 {attempts} 次: {exc}") from exc
            delay = min(60, 2 ** (attempt - 1))
            print(f"{label} 失败 ({attempt}/{attempts}): {exc}; {delay}s 后重试", file=sys.stderr)
            time.sleep(delay)


def run_tosutil(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["tosutil", *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and result.returncode:
        raise TransferError(
            f"tosutil {' '.join(arguments[:2])} 失败 ({result.returncode}):\n{result.stdout}"
        )
    return result


def remote_size(uri: str) -> int | None:
    result = run_tosutil("stat", uri, check=False)
    if result.returncode:
        lowered = result.stdout.lower()
        if any(marker in lowered for marker in MISSING_MARKERS):
            return None
        raise TransferError(f"无法判断远端对象状态: {uri}\n{result.stdout}")
    match = STAT_SIZE.search(result.stdout)
    if not match:
        raise TransferError(f"无法解析远端对象大小: {uri}\n{result.stdout}")
    return int(match.group(1))


def save_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def rebuild_md5(
    session: requests.Session,
    info: dict[str, Any],
    completed_bytes: int,
    part_size: int,
    hasher: Any,
    timeout: int,
    attempts: int,
) -> Any:
    """续传时重新读取已上传前缀来恢复完整 MD5，不写入磁盘。"""
    start = 0
    while start < completed_bytes:
        end = min(start + part_size, completed_bytes) - 1
        baseline = hasher.copy()

        def restore() -> None:
            nonlocal hasher
            hasher = baseline.copy()
            download_range(session, info, start, end, None, hasher, None, timeout)

        retry(f"恢复 MD5 {start}-{end}", attempts, restore)
        start = end + 1
        print(f"恢复 MD5: {start:,}/{completed_bytes:,}", file=sys.stderr)
    return hasher


def part_uri(number: int) -> str:
    return f"{TOS_SOURCE_PREFIX}mls_lm_polish.tar.gz.part-{number:05d}"


def run(args: argparse.Namespace) -> None:
    if shutil.which("tosutil") is None:
        raise TransferError("找不到 tosutil")
    if args.part_size_mb < 5:
        raise TransferError("--part-size-mb 至少为 5")
    part_size = args.part_size_mb * MIB
    session = requests.Session()
    info = source_info(session, args.timeout)
    part_count = (info["size"] + part_size - 1) // part_size

    state_path = args.state_file.resolve()
    identity = {
        "version": 1,
        "source": info,
        "part_size": part_size,
        "parts_prefix": TOS_SOURCE_PREFIX,
        "expected_md5": EXPECTED_MD5,
    }
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if any(state.get(key) != value for key, value in identity.items()):
            raise TransferError("状态文件与当前源、分片大小、目标路径或 MD5 不一致")
    else:
        state = {**identity, "parts": []}
        save_json(state_path, state)

    completed = len(state["parts"])
    if [item["number"] for item in state["parts"]] != list(range(1, completed + 1)):
        raise TransferError("状态文件中的分片编号不连续")
    print(f"source:      {SOURCE_URL}")
    print(f"size:        {info['size']:,} bytes")
    print(f"destination: {TOS_SOURCE_PREFIX}")
    print(f"parts:       {part_count} x {args.part_size_mb} MiB (last part may be smaller)")
    print(f"resume:      {completed}/{part_count}")

    whole_md5 = hashlib.md5()
    if completed:
        completed_bytes = sum(item["size"] for item in state["parts"])
        whole_md5 = rebuild_md5(
            session, info, completed_bytes, part_size, whole_md5, args.timeout, args.retries
        )

    for number in range(completed + 1, part_count + 1):
        start = (number - 1) * part_size
        end = min(start + part_size, info["size"]) - 1
        expected_size = end - start + 1
        uri = part_uri(number)
        if remote_size(uri) is not None:
            raise TransferError(f"远端已有未登记分片，禁止覆盖: {uri}")
        baseline = whole_md5.copy()
        part_digest = ""

        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f"mls-part-{number:05d}-",
            dir=args.temp_dir,
        ) as temporary:

            def download() -> None:
                nonlocal whole_md5, part_digest
                temporary.seek(0)
                temporary.truncate(0)
                whole_md5 = baseline.copy()
                part_md5 = hashlib.md5()
                download_range(
                    session,
                    info,
                    start,
                    end,
                    temporary,
                    whole_md5,
                    part_md5,
                    args.timeout,
                )
                temporary.flush()
                part_digest = part_md5.hexdigest()

            retry(f"下载分片 {number}", args.retries, download)
            result = run_tosutil("cp", temporary.name, uri, "-vchecksum")
            if "Upload successfully" not in result.stdout:
                raise TransferError(f"tosutil 未报告上传成功:\n{result.stdout}")
            uploaded_size = remote_size(uri)
            if uploaded_size != expected_size:
                raise TransferError(
                    f"远端分片大小错误: {uri}; "
                    f"expected={expected_size}, actual={uploaded_size}"
                )
        # 离开上下文后，本地临时分片已关闭并删除。
        state["parts"].append(
            {
                "number": number,
                "start": start,
                "end": end,
                "size": expected_size,
                "md5": part_digest,
                "uri": uri,
            }
        )
        save_json(state_path, state)
        print(f"完成 {number}/{part_count}; 本地分片已删除; 累计 {end + 1:,}/{info['size']:,}")

    actual_md5 = whole_md5.hexdigest()
    if actual_md5 != EXPECTED_MD5:
        raise TransferError(
            f"完整 MD5 失败: expected={EXPECTED_MD5}, actual={actual_md5}; "
            "远端 chunks 保留，未执行任何合并"
        )
    manifest = {
        **identity,
        "part_count": part_count,
        "total_size": info["size"],
        "whole_md5": actual_md5,
        "parts": state["parts"],
        "status": "chunks_uploaded_and_source_md5_verified",
        "merged": False,
    }
    save_json(args.manifest.resolve(), manifest)
    print(f"PASS: {part_count} 个 chunks 已上传，完整源 MD5={actual_md5}")
    print(f"本地合并清单: {args.manifest.resolve()}")
    print("注意: 当前没有生成或合并最终 mls_english.tar.gz 对象。")


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part-size-mb", type=int, default=10)
    parser.add_argument("--state-file", type=Path, default=STATE_FILE)
    parser.add_argument("--manifest", type=Path, default=PART_MANIFEST)
    parser.add_argument("--temp-dir", type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=8)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        run(cli())
    except KeyboardInterrupt:
        print("\n已中断；重新执行相同命令可从已登记 chunk 后继续。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
