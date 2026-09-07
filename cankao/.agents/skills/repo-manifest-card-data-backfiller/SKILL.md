---
name: repo-manifest-card-data-backfiller
description: Backfill the complete Hugging Face Dataset Card front matter into existing verified datasets/*/repo_manifest.json files without redownloading datasets or changing their archived file inventories. Use for auditing or updating old manifests that lack card_data.
---

# Repo Manifest Card Data Backfiller

Add only the missing top-level `card_data` field to old verified `repo_manifest.json` files. The bundled script queries Hugging Face at each manifest's saved `repo_id` and immutable `commit_id`; it never calls the downloader and never changes `source/`.

## Safety invariants

- Use `scripts/backfill_card_data.py`; do not implement the update ad hoc.
- Never query the repository's current HEAD for an old manifest. Always use its stored `commit_id`.
- Require `download_status: verified` and `verification.passed: true`.
- Before writing, require the remote fixed-revision inventory to exactly match the stored `file_manifest`: relative path, byte size, Blob ID, and LFS SHA-256, plus file count and total bytes.
- Add only `card_data`. Preserve repository identity, commit, top-level summary fields, file inventory, download state, and verification evidence.
- Skip a manifest that already contains `card_data`; do not refresh it unless the user explicitly requests `--overwrite-card-data`.
- Write atomically in the manifest's directory. A failed dataset must not prevent unrelated manifests from being processed.
- Never download dataset files, modify `source/`, generate a card, or access TOS.

## Commands

Preview all old manifests:

```bash
python .agents/skills/repo-manifest-card-data-backfiller/scripts/backfill_card_data.py \
  --dry-run
```

Backfill all eligible old manifests:

```bash
python .agents/skills/repo-manifest-card-data-backfiller/scripts/backfill_card_data.py
```

Process one or more datasets:

```bash
python .agents/skills/repo-manifest-card-data-backfiller/scripts/backfill_card_data.py \
  --dataset stt-tedx-test-fr \
  --dataset owner/another-dataset
```

Use `--overwrite-card-data` only when the user explicitly asks to replace existing snapshots. Report totals for updated, would-update, already-present, ineligible, and failed manifests, followed by exact failure reasons.
