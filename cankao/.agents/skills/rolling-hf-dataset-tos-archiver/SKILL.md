---
name: rolling-hf-dataset-tos-archiver
description: Archive a Hugging Face dataset larger than local disk to the raw TOS archive by downloading, verifying, uploading, remotely verifying, and deleting one repository file at a time. Use only when every individual repository file fits local disk; do not use for an already complete local snapshot.
---

# Rolling Hugging Face Dataset TOS Archiver

Use `core/rolling_archive.py` for datasets whose complete snapshot cannot fit locally. The script freezes one Hugging Face commit, processes files sequentially, and is safely rerunnable from `transfer_state.json`.

## Authority and destination

- Read sections 0, 2, 3.4, 4, 8 (raw checklist), 9, and 10 of `docs/open-dataset-ingestion-handbook.md`, plus the rolling section of `docs/raw-dataset-workflow.md`, before acting.
- Use only the raw destination beneath `tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/{asr|tts}/{lang}/{dataset_id}/source/`.
- Resolve ASR/TTS, language, and dataset ID with the same evidence rules as `raw-dataset-tos-uploader`. Stop on ambiguity or conflict.
- Preserve every Hugging Face repository path and byte unchanged. Never unpack, transcode, rename, overwrite, or delete a TOS object.
- This mode requires enough free space for the largest single repository file plus the configured reserve. If any one file cannot fit, stop; this skill does not implement byte-range-to-multipart streaming.

## Prepare without remote writes

Run from the repository root:

```bash
python core/rolling_archive.py prepare \
  --repo-id owner/dataset \
  --work-root rolling_jobs
```

This freezes the exact commit and creates `repo_manifest.json`, `transfer_state.json`, and `source/`. Review and show the user the repo ID, commit, license, file count, total bytes, largest file and size, exact job directory, and exact resolved TOS prefix. Confirm the largest file plus reserve fits disk. Preparation performs no TOS write.

Review redistribution rights. Immediately before the first remote write, require explicit authorization for this exact commit and destination unless the current request already unambiguously grants it.

## Run sequentially

After authorization:

```bash
python core/rolling_archive.py run \
  --job-dir rolling_jobs/dataset \
  --tos-prefix 'tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/tts/en/dataset/source/' \
  --license-reviewed \
  --delete-local-after-verify
```

For each file the script must retain this order:

1. Download the file at the frozen commit, with resumable Hugging Face temporary data isolated inside the job.
2. Verify exact size and Hugging Face LFS SHA-256 or Git blob SHA-1.
3. Compute local CRC64 with `tosutil hash`, confirm the destination is absent, and upload with `tosutil cp -vchecksum`.
4. `tosutil stat` the object and require exact size plus CRC64 equal to the local file.
5. Atomically persist verified remote state, then delete the local file. Retain only `source/README.md` as small evidence for card generation.

Never delete a local file after size-only comparison. If an interruption leaves a new remote object but stale state, adopt it only while the verified local file still exists and its CRC64 exactly matches TOS. Otherwise stop without overwrite or deletion.

A failed run is incomplete but rerunnable with the identical command. Use `status` for local progress. Do not refresh the manifest or change a job's bound TOS prefix.

## Completion gate

The run must perform a full prefix reconciliation: exact paths, per-object sizes, object counts, and aggregate bytes via both TOS listing and `du`. Require final `PASS` and exit code 0. Only then may the manifest record `download_status: verified`, `verification.mode: rolling_file_to_tos`, and `archive_status: verified`.

After this same successful run, use `dataset-card-generator` with the retained README and manifest. Never generate a card after `FAIL`, `ERROR`, interruption, timeout, or nonzero exit. Card upload remains a separate `raw-dataset-card-tos-uploader` operation with its own explicit confirmation.

Report the fixed revision, destination, total count/bytes, newly uploaded and resumed counts, retained local evidence, final reconciliation result, and any unresolved issue.
