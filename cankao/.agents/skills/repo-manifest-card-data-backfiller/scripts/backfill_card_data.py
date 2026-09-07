#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from huggingface_hub import HfApi


def find_root(path: Path) -> Path:
    resolved = path.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    raise FileNotFoundError("未找到包含 .git 的项目根目录")


ROOT_DIR = find_root(Path(__file__))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill complete Hugging Face card_data into verified "
            "repo_manifest.json files without downloading datasets."
        )
    )
    parser.add_argument(
        "--datasets-dir",
        type=Path,
        default=ROOT_DIR / "datasets",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT_DIR / "config.yaml",
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        help="Dataset directory name or owner/repo. Repeatable.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and validate, but do not modify manifests.",
    )
    parser.add_argument(
        "--overwrite-card-data",
        action="store_true",
        help="Replace an existing card_data snapshot.",
    )
    return parser.parse_args()


def load_endpoint(config_path: Path) -> str:
    if not config_path.is_file():
        return "https://huggingface.co"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    global_config = config.get("global") or {}
    return global_config.get("endpoint") or "https://huggingface.co"


def discover_manifests(
    datasets_dir: Path, selected: list[str]
) -> list[Path]:
    paths = sorted(datasets_dir.glob("*/repo_manifest.json"))
    if not selected:
        return paths
    wanted = {item.rstrip("/") for item in selected}
    matched = []
    for path in paths:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        if path.parent.name in wanted or manifest.get("repo_id") in wanted:
            matched.append(path)
    return matched


def card_data_to_json(info: Any) -> dict[str, Any]:
    raw = info.card_data
    if raw is None:
        card_data: dict[str, Any] = {}
    elif hasattr(raw, "to_dict"):
        card_data = raw.to_dict()
    else:
        card_data = dict(raw)
    # Round-trip validates JSON safety and detaches custom mapping values.
    return json.loads(json.dumps(card_data, ensure_ascii=False))


def inventory_from_manifest(
    manifest: dict[str, Any],
) -> dict[str, tuple[Any, Any, Any]]:
    inventory = {}
    for item in manifest.get("file_manifest") or []:
        path = item.get("path")
        if not isinstance(path, str) or not path or path in inventory:
            raise ValueError(f"本地 file_manifest 路径非法或重复: {path!r}")
        inventory[path] = (
            item.get("size"),
            item.get("blob_id"),
            item.get("lfs_sha256"),
        )
    return inventory


def inventory_from_info(info: Any) -> dict[str, tuple[Any, Any, Any]]:
    inventory = {}
    for sibling in info.siblings or []:
        path = sibling.rfilename
        if not path or path in inventory:
            raise ValueError(f"远端文件路径非法或重复: {path!r}")
        inventory[path] = (
            sibling.size,
            sibling.blob_id,
            sibling.lfs.sha256 if sibling.lfs is not None else None,
        )
    return inventory


def validate_fixed_revision(
    manifest: dict[str, Any], info: Any
) -> None:
    repo_id = manifest.get("repo_id")
    commit_id = manifest.get("commit_id")
    if info.id != repo_id:
        raise ValueError(f"repo_id 不一致: {repo_id!r} != {info.id!r}")
    if info.sha != commit_id:
        raise ValueError(f"commit_id 不一致: {commit_id!r} != {info.sha!r}")

    local_inventory = inventory_from_manifest(manifest)
    remote_inventory = inventory_from_info(info)
    if local_inventory != remote_inventory:
        missing = sorted(set(local_inventory) - set(remote_inventory))
        extra = sorted(set(remote_inventory) - set(local_inventory))
        mismatched = sorted(
            path
            for path in set(local_inventory) & set(remote_inventory)
            if local_inventory[path] != remote_inventory[path]
        )
        raise ValueError(
            "固定 revision 文件清单不一致: "
            f"missing={missing[:10]} extra={extra[:10]} "
            f"metadata_mismatches={mismatched[:10]}"
        )

    expected_count = manifest.get("file_count")
    expected_bytes = manifest.get("total_size")
    actual_count = len(remote_inventory)
    actual_bytes = sum(item[0] for item in remote_inventory.values())
    if expected_count != actual_count or expected_bytes != actual_bytes:
        raise ValueError(
            "固定 revision 汇总不一致: "
            f"count={expected_count}/{actual_count} "
            f"bytes={expected_bytes}/{actual_bytes}"
        )


def write_manifest_atomic(path: Path, manifest: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".card-data.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def process_manifest(
    api: HfApi,
    path: Path,
    dry_run: bool,
    overwrite: bool,
) -> tuple[str, str]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    verification = manifest.get("verification") or {}
    if (
        manifest.get("download_status") != "verified"
        or verification.get("passed") is not True
    ):
        return "ineligible", "下载状态或核验结果不是 verified/passed"
    if "card_data" in manifest and not overwrite:
        return "already_present", "card_data 已存在"

    repo_id = manifest.get("repo_id")
    commit_id = manifest.get("commit_id")
    if not isinstance(repo_id, str) or not repo_id:
        return "failed", "缺少有效 repo_id"
    if not isinstance(commit_id, str) or not commit_id:
        return "failed", "缺少有效 commit_id"

    info = api.dataset_info(
        repo_id=repo_id,
        revision=commit_id,
        files_metadata=True,
    )
    validate_fixed_revision(manifest, info)
    card_data = card_data_to_json(info)
    if dry_run:
        return "would_update", f"card_data keys={len(card_data)}"

    updated = dict(manifest)
    updated["card_data"] = card_data
    write_manifest_atomic(path, updated)
    return "updated", f"card_data keys={len(card_data)}"


def main() -> int:
    args = parse_args()
    load_dotenv(ROOT_DIR / ".env")
    token = os.getenv("HF_TOKEN")
    endpoint = load_endpoint(args.config)
    api = HfApi(endpoint=endpoint, token=token)
    paths = discover_manifests(args.datasets_dir, args.dataset)
    if not paths:
        print("未找到匹配的 repo_manifest.json", file=sys.stderr)
        return 2

    counts = {
        "updated": 0,
        "would_update": 0,
        "already_present": 0,
        "ineligible": 0,
        "failed": 0,
    }
    for index, path in enumerate(paths, start=1):
        try:
            status, detail = process_manifest(
                api=api,
                path=path,
                dry_run=args.dry_run,
                overwrite=args.overwrite_card_data,
            )
        except Exception as error:
            status, detail = "failed", str(error)
        counts[status] += 1
        print(f"[{index}/{len(paths)}] {path.parent.name}: {status} - {detail}")

    print("汇总: " + " ".join(f"{key}={value}" for key, value in counts.items()))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
