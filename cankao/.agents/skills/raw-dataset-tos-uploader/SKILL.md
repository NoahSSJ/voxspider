---
name: raw-dataset-tos-uploader
description: Upload a fully downloaded and verified open speech dataset's source files unchanged to the TOS raw archive, perform full reconciliation, then generate a local raw dataset_card.yaml via the dataset-card-generator script. Never upload the card to TOS.
---

# Raw Dataset TOS Uploader

Upload only an already complete local dataset snapshot into the TOS `raw` archive. Preserve every included source byte, relative path, and filename, and prove that the uploaded objects match the local upload inventory.

After source-object reconciliation passes, invoke the raw-only dataset card generator to create or validate the local `dataset_card.yaml`. Never copy or upload the card to TOS.

## Authority and scope

- Read sections 0, 2, 3.4, 4, 8 (raw checklist), 9, and 10 of `docs/open-dataset-ingestion-handbook.md` before acting. If the handbook is missing or those sections cannot be read, stop.
- The only permitted destination is beneath:
  `tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/{asr|tts}/{lang}/{dataset_id}/source/`
- Upload the contents of the local dataset's `source/` directory, not the `source/` directory itself as an extra nesting level.
- Exclude exactly the dataset-root `repo_manifest.json` and the entire `source/.cache/` subtree.
- Upload everything else beneath `source/`, including README, metadata, ordinary dotfiles, and files not listed in the repository manifest. Do not introduce any other implicit exclude pattern.
- Preserve the included directory tree and all included file contents exactly.
- Do not unpack archives, transcode audio, rename files, normalize paths, delete source files, or upload any file stored outside `source/`.
- Treat TOS as append-only: never overwrite or delete an existing object.

## Inputs that must be resolved

Before any remote mutation, determine and show the user:

- the exact local `source/` path;
- the exact local verification manifest (normally `repo_manifest.json`);
- `asr` or `tts` as the intended use;
- the ISO 639-1 two-letter language code, or `multi`;
- `dataset_id`, following section 2.2 of the handbook;
- the exact destination TOS URI.

Infer values only from unambiguous local configuration, metadata, or the user's request. If use, language, dataset ID, destination, credentials, endpoint, or bucket access is ambiguous, stop and ask. Never guess a TOS path.

### Determine ASR or TTS

Determine the `{asr|tts}` path component from both the dataset name and `repo_manifest.json.task_categories`:

- Treat `task_categories` as the primary structured evidence:
  - `automatic-speech-recognition` means `asr`;
  - `text-to-speech` means `tts`.
- Treat the dataset name as supporting evidence. A distinct `stt` or `asr` token supports `asr`; a distinct `tts` token supports `tts`. Do not match arbitrary substrings inside unrelated words.
- If `task_categories` identifies exactly one of ASR or TTS and the name agrees or is neutral, use the manifest result.
- If `task_categories` is absent or identifies neither, use the name only when it contains one unambiguous task token; show that this is a name-based inference before upload.
- If the manifest and name conflict, both task categories appear, or neither source is decisive, stop and ask the user. Never choose silently.

For example, dataset ID `stt-tedx-test-fr` plus `task_categories: [automatic-speech-recognition]` resolves to `asr`.

## Preflight gates

Perform read-only checks before requesting authorization to upload:

1. Confirm the local dataset directory and `source/` exist and are readable.
2. Require the download manifest to report successful full verification (for the current downloader, `download_status: verified` and `verification.passed: true`). A downloading, failed, partial, or unverifiable snapshot is blocked.
3. Reconcile every official manifest entry against local `source/`: relative path, byte size, and checksum wherever the manifest provides one. Do not use sampling. No official entry may resolve inside the excluded `.cache/` subtree.
4. Build the upload inventory by recursively enumerating every regular file beneath `source/` and excluding only `source/.cache/**`. Include ordinary hidden files; use exact path-relative matching, not a broad `*.cache*` or basename-only rule.
5. Confirm `repo_manifest.json`, `source/.cache/`, directories, symlinks, sockets, and other non-regular entries are absent from the upload inventory. If a symlink or special entry is found, stop rather than dereferencing it implicitly.
6. Compute the upload-inventory file count and total bytes. Report separately any included files absent from the official manifest; they remain in upload scope.
7. Check available TOS tooling and credentials without printing secrets. Never place access keys or tokens in command-line arguments, logs, generated scripts, or reports.
8. Confirm the S3-compatible client uses virtual-hosted addressing (`addressing_style: virtual`). On `InvalidPathAccess`, check this setting before diagnosing permissions.

Do not upload when any preflight gate fails. Report exact missing paths, mismatched sizes/checksums, or unresolved metadata.

## Upload behavior

- Obtain user authorization immediately before the first remote write unless the current request already explicitly authorizes uploading this resolved dataset to this resolved destination.
- Upload source files independently and with bounded concurrency appropriate to the available client and network.
- Make retries progress-aware. Continue while bytes are advancing; stop after repeated attempts with no progress and report the affected object.
- For each destination object:
  - if absent, upload it;
  - if present with the same byte size, verify content using a trustworthy checksum when TOS exposes one with matching semantics; otherwise treat it as provisionally skipped and require it to pass final reconciliation;
  - if present with a different size or content, stop without overwriting;
  - never assume an S3 ETag is MD5 for multipart uploads.
- A failed or interrupted run must remain safely rerunnable. Do not delete local files as part of this skill.
- Do not use broad process-killing or container-deletion commands on shared machines.

## Mandatory final reconciliation

After upload, run `scripts/reconcile_tos.py` with the verified `repo_manifest.json` and exact destination `source/` prefix. The script recursively lists the prefix, calls `tosutil stat` for every expected object, and independently checks exact prefix totals with `tosutil du -bf=raw`. This must be a full comparison, not a sample.

```bash
python scripts/reconcile_tos.py \
  datasets/{dataset_id}/repo_manifest.json \
  "tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/{asr|tts}/{lang}/{dataset_id}/source/"
```

Treat `repo_manifest.json.file_manifest` as the authoritative upload inventory. Before upload, confirm that recursively scanning local `source/` while excluding `.cache/**` produces exactly the same path set; if it does not, stop and report the difference.

Require all of the following:

- expected object count equals actual object count;
- missing objects = 0;
- extra objects = 0;
- size mismatches = 0;
- expected total bytes equals actual total bytes;
- checksums match wherever TOS provides a checksum with semantics compatible with the manifest.

If any condition fails, the upload is incomplete. Do not conceal the mismatch, do not upload a card, and do not overwrite or delete objects to repair it without explicit user direction.

Require the reconciliation script to print `PASS` and exit with status 0. `FAIL`, `ERROR`, or any nonzero exit status means the upload is incomplete.

## Generate the local raw card after PASS

Card generation is a hard-gated consequence of the reconciliation command from the immediately preceding step. Capture both that command's complete output and its exit status. Only when the same invocation prints an unambiguous final `PASS` **and** exits with status `0` may this skill read `repo_id` from the verified `repo_manifest.json` and automatically run from the repository root:

```bash
python .agents/skills/dataset-card-generator/scripts/generate_card.py --dataset owner/dataset
```

This step uses the raw-only `dataset-card-generator` skill and writes or validates `datasets/{dataset_id}/dataset_card.yaml`. Do not use `--overwrite` unless the user explicitly authorizes replacing an existing card. Require the card generator to exit 0; otherwise report that TOS source reconciliation passed but local card generation failed.

Never invoke the generator based on upload-command success, matching aggregate size alone, a previous run's `PASS`, a saved log, or a sampled comparison. If reconciliation prints `FAIL` or `ERROR`, exits nonzero, is interrupted, times out, produces ambiguous or incomplete output, or any required comparison is unavailable, stop before invoking the generator and report `dataset_card.yaml: not generated due to reconciliation failure`. Do not create a placeholder or partial YAML.

Before reporting overall success, apply section 8's raw delivery checklist: the card must be at the dataset root rather than under `source/`, have `status: raw`, contain all minimum required fields, use the actual dataset homepage, contain a filled `data.total_duration_hours` (including per-split durations when splits are documented), and contain `generation.generated_at`. A card-generation or section-8 validation failure does not invalidate a recorded TOS reconciliation PASS, but the overall workflow remains incomplete.

Never upload, copy, or publish `dataset_card.yaml` to TOS in this skill.

Return a concise report containing the dataset/revision, local source, destination URI, excluded paths (`repo_manifest.json` and `source/.cache/**`), expected and actual counts and bytes, missing/extra/size/checksum mismatch counts, included files absent from the official manifest, skipped-existing count, newly uploaded count, and any unresolved issue.

## Hard stop conditions

Stop and ask the user or designated owner when:

- the license may restrict redistribution;
- the local snapshot is not fully verified;
- an official manifest file is missing locally or fails size/checksum verification;
- multiple plausible ASR/TTS, language, version, dataset ID, or destination choices exist;
- a destination object conflicts with the local source;
- credentials or endpoint settings are missing;
- the handbook does not cover the situation.

Never signal publication by uploading `dataset_card.yaml`. Successful completion means the source objects were uploaded and fully reconciled, and the local raw card was generated or validated; the card remains local.
