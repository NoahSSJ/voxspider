#!/usr/bin/env python3
"""Reconcile every TOS object under a raw/source prefix with repo_manifest.json."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

LS_COUNT = re.compile(r"File number is:\s*(\d+)")
LS_BYTES = re.compile(r"is:\s*(\d+)B\s*$", re.MULTILINE)
DU_COUNT = re.compile(r"Total object number:\s*(\d+)")
DU_BYTES = re.compile(r"Total object size:\s*(\d+)B")
STAT_SIZE = re.compile(r"^Size:\s*\n\s*(\d+)\s*$", re.MULTILINE)


def run_tosutil(*args: str) -> str:
    result = subprocess.run(
        ["tosutil", *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(
            f"tosutil {' '.join(args[:2])} failed ({result.returncode}):\n{result.stdout}"
        )
    return result.stdout


def load_manifest(path: Path) -> tuple[dict[str, int], int, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("download_status") != "verified" or not data.get(
        "verification", {}
    ).get("passed"):
        raise ValueError(
            "manifest requires download_status=verified and verification.passed=true"
        )

    expected: dict[str, int] = {}
    for item in data.get("file_manifest", []):
        relative = item.get("path")
        size = item.get("size")
        if not isinstance(relative, str) or not isinstance(size, int):
            raise ValueError("every file_manifest item requires string path and integer size")
        parsed = PurePosixPath(relative)
        if parsed.is_absolute() or ".." in parsed.parts or relative in expected:
            raise ValueError(f"unsafe or duplicate manifest path: {relative!r}")
        expected[relative] = size

    expected_count = len(expected)
    expected_bytes = sum(expected.values())
    if data.get("file_count") != expected_count:
        raise ValueError(
            f"file_count={data.get('file_count')} but file_manifest has {expected_count} items"
        )
    if data.get("total_size") != expected_bytes:
        raise ValueError(
            f"total_size={data.get('total_size')} but file_manifest sums to {expected_bytes}"
        )
    return expected, expected_count, expected_bytes


def stat_size(object_url: str) -> int:
    output = run_tosutil("stat", object_url)
    match = STAT_SIZE.search(output)
    if not match:
        raise RuntimeError(f"cannot parse Size for {object_url}")
    return int(match.group(1))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a TOS source prefix object-by-object with repo_manifest.json."
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("tos_prefix")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    if not args.tos_prefix.startswith("tos://"):
        parser.error("tos_prefix must start with tos://")
    if args.workers < 1:
        parser.error("--workers must be positive")
    prefix = args.tos_prefix.rstrip("/") + "/"

    try:
        expected, expected_count, expected_bytes = load_manifest(args.manifest)

        ls_output = run_tosutil("ls", prefix, "-s", "-bf=raw")
        listed_urls = {
            line.strip()
            for line in ls_output.splitlines()
            if line.strip().startswith("tos://")
        }
        foreign_urls = sorted(url for url in listed_urls if not url.startswith(prefix))
        listed_paths = {
            url[len(prefix) :]: url
            for url in listed_urls
            if url.startswith(prefix)
        }
        ls_count_match = LS_COUNT.search(ls_output)
        ls_bytes_match = LS_BYTES.search(ls_output)
        if not ls_count_match or not ls_bytes_match:
            raise RuntimeError("cannot parse total count/bytes from tosutil ls")
        ls_count = int(ls_count_match.group(1))
        ls_bytes = int(ls_bytes_match.group(1))

        du_output = run_tosutil("du", prefix, "-bf=raw")
        du_count_match = DU_COUNT.search(du_output)
        du_bytes_match = DU_BYTES.search(du_output)
        if not du_count_match or not du_bytes_match:
            raise RuntimeError("cannot parse total count/bytes from tosutil du")
        du_count = int(du_count_match.group(1))
        du_bytes = int(du_bytes_match.group(1))

        expected_paths = set(expected)
        actual_paths = set(listed_paths)
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)

        remote_sizes: dict[str, int] = {}
        stat_errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(stat_size, listed_paths[path]): path
                for path in sorted(actual_paths & expected_paths)
            }
            for future in as_completed(futures):
                path = futures[future]
                try:
                    remote_sizes[path] = future.result()
                except Exception as error:  # noqa: BLE001
                    stat_errors[path] = str(error)

        size_mismatches = [
            (path, expected[path], remote_sizes[path])
            for path in sorted(remote_sizes)
            if expected[path] != remote_sizes[path]
        ]
        stat_bytes = sum(remote_sizes.values())
        listing_complete = len(listed_urls) == ls_count == du_count
        aggregates_match = (
            expected_count == ls_count == du_count
            and expected_bytes == ls_bytes == du_bytes == stat_bytes
        )
        passed = not (
            missing
            or extra
            or size_mismatches
            or stat_errors
            or foreign_urls
            or not listing_complete
            or not aggregates_match
        )

        print(f"manifest: {args.manifest}")
        print(f"prefix:   {prefix}")
        print(f"expected: {expected_count} objects, {expected_bytes} bytes")
        print(f"tos ls:   {ls_count} objects, {ls_bytes} bytes")
        print(f"tos du:   {du_count} objects, {du_bytes} bytes")
        print(f"tos stat: {len(remote_sizes)} objects, {stat_bytes} bytes")
        print(
            f"missing={len(missing)} extra={len(extra)} "
            f"size_mismatches={len(size_mismatches)} stat_errors={len(stat_errors)}"
        )
        for path in missing:
            print(f"MISSING\t{path}")
        for path in extra:
            print(f"EXTRA\t{path}")
        for path, wanted, actual in size_mismatches:
            print(f"SIZE_MISMATCH\t{path}\texpected={wanted}\tactual={actual}")
        for path, error in sorted(stat_errors.items()):
            print(f"STAT_ERROR\t{path}\t{error}")
        for url in foreign_urls:
            print(f"FOREIGN_URL\t{url}")
        print("PASS" if passed else "FAIL")
        return 0 if passed else 1
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
