---
name: raw-dataset-card-tos-uploader
description: Prepare and, only after a separate explicit user confirmation, upload one verified raw dataset's local dataset_card.yaml to its TOS dataset root as the final delivery signal, then register its complete repo_manifest.json in processed_datasets.json. Use when the user asks to upload or publish a dataset card; never upload source data or overwrite an existing remote card.
---

# Raw Dataset Card TOS Uploader

Upload exactly one local raw `dataset_card.yaml` as the final delivery signal. The preparation phase is read-only. The remote write requires a separate, informed confirmation from the user after all checks and the exact destination have been shown.

## Authority and scope

- Read sections 0, 2, 4.3, 4.4, 6, 7, and section 8's raw checklist in `docs/open-dataset-ingestion-handbook.md` before acting. Stop if they are unavailable.
- The only local upload candidate is `datasets/{dataset_id}/dataset_card.yaml`.
- The only permitted destination is:
  `tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/{asr|tts}/{lang}/{dataset_id}/dataset_card.yaml`
- The card belongs at the dataset root, alongside `source/`; never upload it beneath `source/`.
- Upload no source file, manifest, cache, directory, cleaned card, or other YAML.
- Treat TOS as append-only. Never overwrite or delete an existing remote card.

## Resolve the target

Resolve the requested dataset directory without fuzzy matching. Require its `repo_manifest.json`.

Determine `{asr|tts}`, `{lang}`, and `{dataset_id}` with the same rules as `raw-dataset-tos-uploader`:

- `repo_manifest.json.task_categories` is primary evidence: `automatic-speech-recognition` maps to `asr`, and `text-to-speech` maps to `tts`;
- distinct `stt`/`asr` or `tts` name tokens are supporting evidence; stop on conflict or ambiguity;
- one manifest language uses its ISO 639-1 code and multiple languages use `multi`;
- `dataset_id` is the dataset directory name and must equal `dataset.id` in the card.

Never guess or normalize an ambiguous destination.

## Read-only preparation gates

Complete all checks before asking for upload confirmation:

1. Require the local card to exist as a readable, non-symlink regular file at the dataset root. If absent, report the exact missing path and stop without asking for upload confirmation.
2. Parse the YAML and apply the card rules in sections 6 and 7 plus section 8's raw checklist. At minimum require `status: raw`, all minimum fields, matching `dataset.id`, the actual dataset homepage, a filled numeric `data.total_duration_hours`, and a timezone-aware `generation.generated_at`. Reject a card under `source/`.
3. Require `repo_manifest.json` to show `download_status: verified` and `verification.passed: true`.
4. Because uploading the card declares delivery complete, rerun the full TOS source reconciliation from section 4.3 using the current manifest and resolved `source/` URI. Require the same invocation to print `PASS` and exit `0`. A prior log or historical PASS is insufficient.

   ```bash
   python .agents/skills/raw-dataset-tos-uploader/scripts/reconcile_tos.py \
     datasets/{dataset_id}/repo_manifest.json \
     'tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/{asr|tts}/{lang}/{dataset_id}/source/'
   ```

5. Run `tosutil stat` on the exact remote card URI:
   - if it does not exist, the destination is eligible;
   - if it exists and represents the same card, report `already uploaded` and do not upload again;
   - if it exists but differs or cannot be compared reliably, stop. This skill never overwrites it.
6. Compute the local card's exact byte size and SHA-256. Record its modification time so a later confirmation can be bound to this exact file.
7. Check TOS tooling and credentials without printing or placing secrets in commands, logs, or reports.

Any failed or ambiguous gate stops preparation and no confirmation question is issued.

## Mandatory confirmation boundary

After every preparation gate passes, show the user:

- dataset ID and resolved ASR/TTS/language;
- exact local card path;
- exact byte size and SHA-256;
- exact destination URI;
- source reconciliation result;
- remote card status: absent;
- the consequence: uploading this card signals that raw delivery is complete and may trigger the daily checker.

Then ask a direct confirmation such as:

`以上检查均已通过。是否确认将这个 dataset_card.yaml 上传到上述 TOS 路径？`

Stop the turn and wait. Preparation, an initial request such as “我要上传…”, permission to upload source data, or vague replies do not authorize the card upload. Proceed only after a subsequent user message explicitly confirms this displayed upload, for example “确认上传” or an equally unambiguous affirmative. A question, correction, changed target, conditional reply, or silence is not confirmation.

Confirmation applies only to the displayed local fingerprint and destination and is single-use.

## Upload after explicit confirmation

Immediately before the remote write:

1. Recompute local size and SHA-256. If either differs from the prepared values, stop and require a new preparation and confirmation.
2. Repeat `tosutil stat` for the exact destination. If it now exists or the check is inconclusive, stop without overwriting.
3. Upload only the confirmed file:

```bash
tosutil cp datasets/{dataset_id}/dataset_card.yaml \
  'tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/{asr|tts}/{lang}/{dataset_id}/dataset_card.yaml' \
  -vchecksum
```

Do not use recursive mode, force, update, wildcard, or any option that permits overwrite.

After upload, require command exit status `0`, run `tosutil stat` on the exact object, and verify at least the exact byte size plus a checksum when TOS exposes one with compatible semantics. Report success only after verification. Otherwise report an incomplete or unverifiable upload; never delete or overwrite the object in an attempted repair.

## Register the processed dataset

Only after the new remote card has been uploaded and post-upload verification has succeeded, record the dataset in the repository-root `processed_datasets.json` by running:

```bash
python .agents/skills/raw-dataset-card-tos-uploader/scripts/record_processed_dataset.py \
  datasets/{dataset_id}/repo_manifest.json
```

- Store the complete top-level JSON object from `repo_manifest.json`, without selecting, renaming, or adding fields.
- The registry shape is `{ "datasets": [<complete repo manifest>, ...] }`.
- Use `repo_id` as the identity key. Re-running registration replaces the existing entry at its current position instead of adding a duplicate.
- Do not update the registry during preparation, after a failed or unverifiable upload, or when the remote card merely already existed and no upload occurred in this run.
- Registry failure never authorizes retrying or overwriting the already uploaded remote card. Report the remote upload as successful but local bookkeeping as failed, including the registry error.
- Validate the resulting registry as JSON and confirm that its stored entry is exactly equal to the source manifest.

## Result

Report the local path, destination, size, SHA-256, upload exit status, post-upload remote size/checksum result, whether the card is verified uploaded, and whether the complete manifest was registered in `processed_datasets.json`. Never claim that the card or dataset was delivered when any required check failed.
