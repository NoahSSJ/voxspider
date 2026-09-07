#!/usr/bin/env python3
"""Register a complete repo_manifest.json after a verified card upload."""

import argparse
import json
import os
import tempfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--registry", type=Path, default=Path("processed_datasets.json"))
    return parser.parse_args()


def load_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a top-level JSON object")
    return value


def main() -> None:
    args = parse_args()
    manifest = load_object(args.manifest)
    repo_id = manifest.get("repo_id")
    if not isinstance(repo_id, str) or not repo_id:
        raise ValueError(f"{args.manifest} has no non-empty string repo_id")

    if args.registry.exists():
        registry = load_object(args.registry)
    else:
        registry = {"datasets": []}
    datasets = registry.get("datasets")
    if not isinstance(datasets, list) or not all(isinstance(item, dict) for item in datasets):
        raise ValueError(f"{args.registry} must have a datasets array of objects")

    matches = [index for index, item in enumerate(datasets) if item.get("repo_id") == repo_id]
    if len(matches) > 1:
        raise ValueError(f"{args.registry} contains duplicate repo_id: {repo_id}")
    if matches:
        datasets[matches[0]] = manifest
        action = "updated"
    else:
        datasets.append(manifest)
        action = "added"

    args.registry.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{args.registry.name}.", suffix=".tmp", dir=args.registry.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(registry, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, args.registry)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise

    stored = load_object(args.registry)
    entries = [item for item in stored["datasets"] if item.get("repo_id") == repo_id]
    if entries != [manifest]:
        raise RuntimeError(f"failed to verify exact registry entry for {repo_id}")
    print(f"{action}: {repo_id} -> {args.registry}")


if __name__ == "__main__":
    main()
