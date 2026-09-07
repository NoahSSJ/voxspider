---
name: tos-dataset-status-auditor
description: Read config.yaml and datasets manifests, query TOS without mutation, and report each raw dataset's source upload, full reconciliation, and dataset_card.yaml upload status. Use for inventory audits and status checks; never upload, overwrite, or delete anything.
---

# TOS Dataset Status Auditor

Audit raw dataset delivery state without changing local files or TOS objects.

## Scope and authority

- Use the union of datasets declared in `config.yaml` and directories containing `datasets/*/repo_manifest.json`, unless the user selects specific datasets.
- Treat each `repo_manifest.json.file_manifest` as the expected raw source inventory. Check its internal `file_count` and `total_size` before trusting it.
- Query only beneath `tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/`.
- Never upload, copy, overwrite, delete, create directories, or upload a card.
- This skill reports remote card presence even though `raw-dataset-tos-uploader` intentionally never uploads cards.

## Resolve the raw TOS path

Resolve `{asr|tts}`, `{lang}`, and `{dataset_id}` with the same rules as `raw-dataset-tos-uploader`:

- `automatic-speech-recognition` maps to `asr`; `text-to-speech` maps to `tts`.
- Distinct dataset-name tokens `stt`/`asr` support `asr`; `tts` supports `tts`.
- One manifest language uses its ISO 639-1 code; multiple languages use `multi`.
- The dataset directory name is the candidate `dataset_id`; an underscore, space, or ambiguous version is `needs_review` rather than silently normalized.
- If task classification is ambiguous, probe both ASR and TTS candidate prefixes read-only. Adopt a candidate only when exactly one contains source objects or a dataset card. Otherwise report `needs_review`.

Never guess a destination path.

## Status definitions

For each resolved dataset, report these independent booleans:

- `upload_complete`: every path in `file_manifest` exists beneath the remote `source/` prefix. Sizes are not part of this status, so a completed transfer with corrupt sizes is visible as uploaded but not reconciled.
- `reconciliation_complete`: the remote source path set exactly equals `file_manifest`, every object's exact byte size matches, object counts match, and exact total bytes match. Missing, extra, size mismatch, stat error, or aggregate mismatch makes this false.
- `datacard_uploaded`: `{dataset_root}/dataset_card.yaml` exists on TOS, is a regular object, and has size greater than zero. Its schema validity is outside this presence status.

Also report local download status, resolved TOS root, expected/actual counts and bytes, and detailed mismatch counts. Never infer that reconciliation happened historically from upload logs; recompute it from current TOS state.

## Required execution

Run from the repository root:

```bash
python .agents/skills/tos-dataset-status-auditor/scripts/audit_status.py
```

Useful filters:

```bash
python .agents/skills/tos-dataset-status-auditor/scripts/audit_status.py \
  --dataset stt-mls-test \
  --dataset owner/another-dataset
```

Save a machine-readable report when performing an audit for the user:

```bash
python .agents/skills/tos-dataset-status-auditor/scripts/audit_status.py \
  --json-output logs/tos_dataset_status_YYYYMMDD-HHMMSS.json
```

Require exit status 0 for a successful query run. Dataset states may still be false or `needs_review`; command failure means the audit itself is incomplete.

## Reporting

Return a compact table with one row per dataset and columns for dataset, local status, resolved raw path, upload complete, reconciliation complete, and datacard uploaded. Follow it with exact reasons for `needs_review` and mismatches. Do not describe a false status as complete.
