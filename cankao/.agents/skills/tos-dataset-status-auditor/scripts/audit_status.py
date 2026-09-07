#!/usr/bin/env python3
"""Read-only audit of raw dataset state on TOS."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG = ROOT / "config.yaml"
DEFAULT_DATASETS = ROOT / "datasets"
TOS_BASE = "tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository"
LS_COUNT = re.compile(r"File number is:\s*(\d+)")
LS_BYTES = re.compile(r"is:\s*(\d+)B\s*$", re.MULTILINE)
STAT_SIZE = re.compile(r"^Size:\s*\n\s*(\d+)\s*$", re.MULTILINE)


def run_tosutil(*args: str, allow_missing: bool = False) -> tuple[int, str]:
    result = subprocess.run(
        ["tosutil", *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode and not allow_missing:
        raise RuntimeError(
            f"tosutil {' '.join(args[:2])} failed ({result.returncode}):\n{result.stdout}"
        )
    return result.returncode, result.stdout


def load_config_datasets(path: Path) -> dict[str, str | None]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result: dict[str, str | None] = {}
    for item in data.get("datasets") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        repo_id = item.get("repo_id")
        if isinstance(name, str):
            result[name] = repo_id if isinstance(repo_id, str) else None
    return result


def discover(config: Path, datasets_dir: Path, selected: list[str]) -> list[dict[str, Any]]:
    configured = load_config_datasets(config)
    names = set(configured)
    names.update(path.parent.name for path in datasets_dir.glob("*/repo_manifest.json"))
    if selected:
        wanted_names = {value.rstrip("/").rsplit("/", 1)[-1] for value in selected}
        names &= wanted_names
        missing = wanted_names - names
        names.update(missing)

    entries = []
    for name in sorted(names):
        manifest_path = datasets_dir / name / "repo_manifest.json"
        entries.append(
            {
                "dataset": name,
                "configured_repo_id": configured.get(name),
                "manifest_path": manifest_path,
            }
        )
    return entries


def load_manifest(path: Path) -> tuple[dict[str, Any], dict[str, int]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    expected: dict[str, int] = {}
    for item in data.get("file_manifest") or []:
        relative = item.get("path")
        size = item.get("size")
        if not isinstance(relative, str) or not isinstance(size, int):
            raise ValueError("file_manifest items require string path and integer size")
        parsed = PurePosixPath(relative)
        if parsed.is_absolute() or ".." in parsed.parts or relative in expected:
            raise ValueError(f"unsafe or duplicate manifest path: {relative!r}")
        expected[relative] = size
    if data.get("file_count") != len(expected):
        raise ValueError("file_count does not match file_manifest length")
    if data.get("total_size") != sum(expected.values()):
        raise ValueError("total_size does not match file_manifest byte sum")
    return data, expected


def task_candidates(name: str, manifest: dict[str, Any]) -> tuple[list[str], list[str]]:
    categories = set(manifest.get("task_categories") or [])
    structured = []
    if "automatic-speech-recognition" in categories:
        structured.append("asr")
    if "text-to-speech" in categories:
        structured.append("tts")
    tokens = {token for token in re.split(r"[^a-z0-9]+", name.lower()) if token}
    named = []
    if tokens & {"stt", "asr"}:
        named.append("asr")
    if "tts" in tokens:
        named.append("tts")
    issues = []
    if len(structured) == 1 and (not named or named == structured):
        return structured, issues
    if not structured and len(named) == 1:
        issues.append("task inferred from dataset name because task_categories is not decisive")
        return named, issues
    if structured and named and structured != named:
        issues.append("task_categories conflicts with dataset name")
    return ["asr", "tts"], issues


def language_path(manifest: dict[str, Any]) -> tuple[str | None, str | None]:
    languages = manifest.get("language")
    if not isinstance(languages, list) or not languages:
        return None, "manifest language is missing"
    if any(not isinstance(value, str) or not value for value in languages):
        return None, "manifest language contains an invalid value"
    if len(languages) > 1:
        return "multi", None
    language = languages[0].lower()
    if not re.fullmatch(r"[a-z]{2}", language):
        return None, f"language is not ISO 639-1: {languages[0]!r}"
    return language, None


def list_prefix(prefix: str) -> tuple[dict[str, str], int, int]:
    _, output = run_tosutil("ls", prefix, "-s", "-bf=raw")
    urls = {
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith("tos://") and line.strip().startswith(prefix)
    }
    paths = {url[len(prefix) :]: url for url in urls}
    count_match = LS_COUNT.search(output)
    bytes_match = LS_BYTES.search(output)
    if not count_match or not bytes_match:
        raise RuntimeError(f"cannot parse tosutil ls totals for {prefix}")
    return paths, int(count_match.group(1)), int(bytes_match.group(1))


def object_size(url: str) -> int | None:
    code, output = run_tosutil("stat", url, allow_missing=True)
    if code:
        return None
    match = STAT_SIZE.search(output)
    if not match:
        raise RuntimeError(f"cannot parse object Size for {url}")
    return int(match.group(1))


def candidate_exists(root: str) -> bool:
    source = root + "/source/"
    _, count, _ = list_prefix(source)
    if count:
        return True
    return object_size(root + "/dataset_card.yaml") is not None


def audit_resolved(
    name: str,
    manifest: dict[str, Any],
    expected: dict[str, int],
    root: str,
    workers: int,
) -> dict[str, Any]:
    source_prefix = root + "/source/"
    actual, listed_count, listed_bytes = list_prefix(source_prefix)
    expected_paths = set(expected)
    actual_paths = set(actual)
    missing = sorted(expected_paths - actual_paths)
    extra = sorted(actual_paths - expected_paths)

    remote_sizes: dict[str, int] = {}
    stat_errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(object_size, actual[path]): path
            for path in sorted(expected_paths & actual_paths)
        }
        for future in as_completed(futures):
            path = futures[future]
            try:
                size = future.result()
                if size is None:
                    stat_errors.append(path)
                else:
                    remote_sizes[path] = size
            except Exception:
                stat_errors.append(path)

    mismatches = sorted(
        path for path, size in remote_sizes.items() if size != expected[path]
    )
    expected_count = len(expected)
    expected_bytes = sum(expected.values())
    upload_complete = not missing and len(remote_sizes) == expected_count
    reconciliation_complete = (
        upload_complete
        and not extra
        and not mismatches
        and not stat_errors
        and listed_count == expected_count == len(actual)
        and listed_bytes == expected_bytes == sum(remote_sizes.values())
    )
    card_size = object_size(root + "/dataset_card.yaml")
    return {
        "dataset": name,
        "repo_id": manifest.get("repo_id"),
        "local_download_status": manifest.get("download_status"),
        "tos_root": root + "/",
        "upload_complete": upload_complete,
        "reconciliation_complete": reconciliation_complete,
        "datacard_uploaded": card_size is not None and card_size > 0,
        "datacard_size": card_size,
        "expected_count": expected_count,
        "actual_count": listed_count,
        "expected_bytes": expected_bytes,
        "actual_bytes": listed_bytes,
        "missing_count": len(missing),
        "extra_count": len(extra),
        "size_mismatch_count": len(mismatches),
        "stat_error_count": len(stat_errors),
        "missing": missing,
        "extra": extra,
        "size_mismatches": mismatches,
        "stat_errors": sorted(stat_errors),
        "needs_review": False,
        "issues": [],
    }


def audit_entry(entry: dict[str, Any], workers: int) -> dict[str, Any]:
    name = entry["dataset"]
    path: Path = entry["manifest_path"]
    base = {
        "dataset": name,
        "manifest_path": str(path),
        "upload_complete": False,
        "reconciliation_complete": False,
        "datacard_uploaded": False,
        "needs_review": True,
        "issues": [],
    }
    if not path.is_file():
        base["issues"] = ["repo_manifest.json is missing"]
        return base
    try:
        manifest, expected = load_manifest(path)
        base.update(
            {
                "repo_id": manifest.get("repo_id"),
                "local_download_status": manifest.get("download_status"),
            }
        )
        if "_" in name or " " in name:
            base["issues"] = ["dataset_id requires review; underscores/spaces are not allowed"]
            return base
        language, language_issue = language_path(manifest)
        if language_issue:
            base["issues"] = [language_issue]
            return base
        tasks, task_issues = task_candidates(name, manifest)
        roots = [f"{TOS_BASE}/{task}/{language}/{name}" for task in tasks]
        if len(roots) > 1:
            existing = [root for root in roots if candidate_exists(root)]
            if len(existing) != 1:
                base["issues"] = task_issues + [
                    "cannot resolve ASR/TTS uniquely from metadata, name, or existing TOS paths"
                ]
                base["candidate_roots"] = roots
                return base
            root = existing[0]
            task_issues.append("task resolved from the only existing TOS candidate path")
        else:
            root = roots[0]
        result = audit_resolved(name, manifest, expected, root, workers)
        result["issues"] = task_issues
        return result
    except Exception as error:  # noqa: BLE001
        base["issues"] = [str(error)]
        return base


def print_table(results: list[dict[str, Any]]) -> None:
    print("dataset\tlocal\tupload\treconciled\tdatacard\tneeds_review\ttos_root")
    for item in results:
        print(
            "\t".join(
                [
                    item["dataset"],
                    str(item.get("local_download_status", "unknown")),
                    str(item["upload_complete"]).lower(),
                    str(item["reconciliation_complete"]).lower(),
                    str(item["datacard_uploaded"]).lower(),
                    str(item["needs_review"]).lower(),
                    str(item.get("tos_root", "-")),
                ]
            )
        )
        for issue in item.get("issues") or []:
            print(f"  ISSUE\t{item['dataset']}\t{issue}")
        if not item.get("needs_review") and not item.get("reconciliation_complete"):
            print(
                f"  DETAIL\t{item['dataset']}\tmissing={item['missing_count']} "
                f"extra={item['extra_count']} size_mismatch={item['size_mismatch_count']} "
                f"stat_error={item['stat_error_count']}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit raw dataset status on TOS read-only.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--datasets-dir", type=Path, default=DEFAULT_DATASETS)
    parser.add_argument("--dataset", action="append", default=[])
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")

    try:
        entries = discover(args.config, args.datasets_dir, args.dataset)
        results = [audit_entry(entry, args.workers) for entry in entries]
        print_table(results)
        report = {
            "generated_at": datetime.now().astimezone().isoformat(),
            "config": str(args.config),
            "datasets_dir": str(args.datasets_dir),
            "results": results,
        }
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"report: {args.json_output}")
        return 0
    except Exception as error:  # noqa: BLE001
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
