---
name: dataset-card-generator
description: Generate or review raw speech dataset_card.yaml files for Hugging Face, OpenSLR, and other repository or website sources, using a verified repo_manifest.json plus traceable upstream evidence. Strictly follow handbook sections 6 and 7 and section 8's raw checklist; never generate cleaned cards or extend the schema or enums.
---

# Dataset Card Generator

Create a raw `dataset_card.yaml` that strictly follows sections 6 and 7 and passes the raw delivery checklist in section 8 of the project's ingestion handbook. Keep it traceable to upstream evidence. This workflow is raw-only.

## Non-negotiable authority

- Sections 6 and 7 of `open-dataset-ingestion-handbook.md` are the sole authority for output structure, field semantics, required fields, allowed values, label paths, enum values, and unknown-value handling. Section 8's raw checklist is the additional delivery acceptance gate.
- Read sections 6 and 7 and the raw subsection of section 8 completely before analyzing evidence or drafting output. Do not rely on memory, a summary, an example from another dataset, or a fallback schema.
- If the handbook is missing or unreadable, sections 6 and 7 cannot be located completely, or section 8's raw checklist cannot be located completely, stop without generating a card.
- Do not add, rename, reinterpret, approximate, or normalize a field or enum unless sections 6 or 7 explicitly authorize it.
- Evidence sources determine facts only. They never override or extend the handbook schema.
- Resolve rule conflicts in this order: explicit normative prose, tables, and hard rules in sections 6 and 7; illustrative YAML fragments in sections 6 and 7; repository examples under `docs/yaml_examples`. A lower-priority example never overrides a higher-priority rule.
- In particular, section 7's explicit label-path and enum rules override an illustrative field shown elsewhere in section 6. If the conflict cannot be resolved by this priority, stop and report the exact clauses instead of guessing.

## How to use repository examples

- Treat every YAML under `docs/yaml_examples` as non-normative, potentially legacy input. Use it only to learn useful evidence presentation, reconciliation notes, provenance wording, and validation summaries.
- Never copy a field, enum value, omission, or structure from an example until it has independently passed sections 6 and 7.
- Known unsafe patterns in the current examples include: a `source.source_platform` other than `dataset_repository`; usage values such as `commercial_use` or `speaker_diarization`; label paths such as `accent`; recording values such as `microphone_array`; raw cards with non-empty `lineage.upstream`; missing `homepage`; incomplete `availability`; and missing or timezone-free `generation.generated_at`. Do not reproduce these patterns.
- Example-specific convenience fields are not permission to extend the schema. Keep a fact only when sections 6 or 7 authorize its destination; otherwise omit it from the YAML and mention it in the review report if material.

## Required inputs

For each dataset, locate:

- The applicable `open-dataset-ingestion-handbook.md`; sections 6 and 7 are mandatory.
- A verified `repo_manifest.json` (`repo_metadata.json` is accepted only as a legacy fallback) with the actual homepage, stable source identity or revision when the source provides one, license, complete file inventory, and verification result.
- Traceable upstream descriptive evidence, using `source/README.md` when the source repository includes it, otherwise dataset-root `upstream_evidence.json`.
- The dataset directory name and downloaded raw `source/` tree.

Do not require Hugging Face-specific fields from non-Hugging Face manifests. A source without commits may use its stable resource identifier plus frozen file URLs, sizes, checksums or HTTP identities.

If `upstream_evidence.json` is used, require a JSON object containing at least `source_url` and timezone-aware `retrieved_at`; `source_url` must match the manifest homepage. Record only facts supported by that official page or a cited primary source. Keep this evidence at the dataset root so it does not alter or enter the raw `source/` inventory.

Do not browse for information already present in local artifacts. If required evidence is absent and browsing is allowed, use the official dataset homepage or cited primary paper and save the evidence source and retrieval time before generation.

## Evidence rules

- Treat `repo_manifest.json` as authoritative for source ID, homepage, revision or stable source identifier, license, archived file count, archived byte count, and download verification.
- For Hugging Face, use `repo_manifest.json.card_data` as the structured Dataset Card front-matter snapshot and combine it with archived `source/README.md`.
- For other sources, use the source's own structured metadata when present and combine it with `source/README.md` or dataset-root `upstream_evidence.json`. Do not invent Hugging Face commits, `hf:` URIs, Dataset Card fields, or repository semantics for a website source.
- Treat the official upstream description or primary paper as authoritative for name, version, splits, sample count, duration, languages, intended use, and dataset characteristics. Do not assume any single evidence artifact contains every fact.
- Treat inspected files/manifests as authoritative for actual counts, formats, annotation availability, and physical layout.
- Never silently resolve conflicting values. Prefer an exact value supported by underlying tables or files, preserve the upstream headline claim in `data.statistics`, and report the discrepancy.
- Keep official claims and locally measured facts distinguishable inside `data.statistics`. State the measurement basis and source for sample counts, duration, file counts, and bytes; do not combine figures computed on incompatible scopes.
- Do not infer facts merely from filenames, marketing language, task tags, or a few samples.
- Missing required values must remain `null`, `[]`, `{}`, or be represented under `labels.unknown` as appropriate.
- Every label path may occur in only one of `labels.fixed`, `labels.distribution`, or `labels.unknown`.
- Use only section 7 label paths and enum values. Section 7 globally permits `unknown` and `other` for enum fields; use `unknown` only when the field is known to apply but its value is unavailable, and `other` only when the value is known but genuinely outside the listed categories. When the whole label path is unknown, follow section 6's `labels.unknown` representation instead of inventing a value.
- Treat every handbook enum as a closed set. Never add a new enum value or map a fact to a merely similar value. When `usage.intended_use` cannot represent an upstream use, leave it as `[]`; this alone is not a review blocker.
- Create `subsets` only for evidenced, mutually exclusive physical partitions. Do not infer subsets from metadata categories, and do not create nested subset levels.
- For a raw snapshot, keep `lineage.upstream: []`. Describe the fixed-revision archive operation under `lineage.derivation`; do not describe unperformed cleaning, resampling, segmentation, or filtering.

## Workflow

1. Require the target to be `raw`. If the request asks for a cleaned card, stop; cleaned generation is outside this skill.
2. Extract evidence into a short working table containing value, source file, and confidence. Identify contradictions before drafting YAML.
3. Map facts into the section 6 schema. Use `archive_hf_snapshot` with `recipe_uri: hf:<repo_id>` for Hugging Face; use `archive_source_snapshot` with the exact manifest homepage URL for other sites. State the actual frozen revision or stable source identity and that files were archived without transformation.
4. Populate every minimum required field, even when its value is empty or null.
5. Reconcile official and measured statistics. Choose the primary `data.num_samples` and duration only when their scope and basis are supported; retain materially different official figures in `data.statistics` with an explicit discrepancy note.
6. For `generation.validation`, summarize only checks actually recorded in `repo_manifest.json`: expected and archived file counts, archived bytes, missing files, size mismatches, checksum mismatches, and the verification outcome. Do not claim checksum or archive-content verification when it was not performed.
7. Keep provenance traceable to the actual downloaded source, revision, and source/archive URI only in handbook-authorized fields. `homepage` must identify the actual source repository, not a secondary article or organization page.
8. For raw ingestion, require evidence that the intended fixed-revision archive is complete. A deliberately partial split, missing archive, or unresolved verification failure is blocked rather than described as a complete raw snapshot.
9. Re-read sections 6 and 7 and section 8's raw checklist. Validate every output field and value against them: minimum keys, all required availability booleans, optional-field semantics, raw/cleaned rules, types, label placement, single-value/multi-value cardinality, allowed enums, ISO 8601 timestamp with timezone, homepage, revision, and the raw delivery checks listed below.
10. If contradictory evidence makes a required field genuinely ambiguous, such as conflicting split names, report it and ask the user instead of guessing. License conflicts and incompatible unresolved count scopes are also review blockers. A fact that falls outside a closed handbook enum is not an ambiguity: use the handbook-prescribed empty/unknown representation without extending the schema.
11. Generate the local raw card by invoking `scripts/generate_card.py`; do not recreate its model call or YAML writer inside the skill.

## Section 8 raw delivery gate

Before writing or accepting a card, require every card-related item from section 8's raw checklist:

- write exactly one `dataset_card.yaml` at `datasets/{dataset_id}/dataset_card.yaml`, never under `source/`;
- require `status: raw` and every minimum field from section 6, including required empty containers;
- require `homepage` to be the actual dataset homepage from which this snapshot was downloaded, never a paper, direct download, or search-result URL;
- require `data.total_duration_hours` to be filled with an evidence-backed numeric value rather than `null`; when split statistics are documented, include the duration for each documented split;
- require `generation.generated_at` to be a valid ISO 8601 timestamp with an explicit timezone.

The non-card raw checklist items remain delivery prerequisites: correct raw path and `source/` layout, complete official splits/subsets/languages or a written explanation, zero missing/extra/size-mismatched objects, and successful checksums wherever available. When this skill is invoked by the uploader, accept those remote facts only from the uploader's current full reconciliation result. Do not invent them in the card or claim them in `generation.validation` based solely on the local download manifest.

## Required execution

For a requested dataset, read `repo_id` from its verified manifest. Resolve the local directory by matching that full `repo_id` inside manifests rather than assuming the final repo ID component equals the directory name, then run from the repository root:

```bash
python .agents/skills/dataset-card-generator/scripts/generate_card.py --dataset owner/dataset
```

This invocation is automatic whenever this skill is asked to generate a raw card; the user does not need to run it manually. Use `--dry-run` when the user asks only for review. Do not use `--overwrite` unless the user explicitly authorizes replacing an existing card. Require the command to exit 0 and its per-dataset status to be `generated`, `dry_run_valid`, or `existing_valid`.

The output is local at `datasets/{dataset_id}/dataset_card.yaml`. Never upload it to TOS as part of this skill.

## Final rejection gates

Do not mark a card generated successfully when any of these conditions remains:

- a required field from section 6 is absent, including a required empty container;
- a field or enum cannot be traced to sections 6 and 7;
- a label path appears in more than one label block or violates its section 7 cardinality;
- `availability` omits a required boolean;
- `generation.generated_at` lacks an explicit timezone;
- `data.total_duration_hours` is null, non-numeric, or otherwise not evidence-backed, or documented split statistics omit their durations;
- the output path is not exactly the dataset-root `dataset_card.yaml` or another card exists under `source/`;
- `status` is not exactly `raw`;
- raw `lineage.upstream` is non-empty, `lineage.manifests.samples` is non-null, or derivation claims an unperformed transformation;
- a raw card contains cleaned-only `manifest_schema`;
- the archived repository is partial, missing files, or has unresolved size/checksum failures;
- conflicting licenses or incompatible primary statistics were silently resolved.

## Bulk operation

For hundreds of datasets, process one dataset directory at a time and produce an audit summary with:

- generated successfully;
- generated with unknown fields;
- blocked by contradictory evidence;
- blocked by missing upstream evidence or `repo_manifest.json`;
- failed schema validation.

Do not let one failed dataset stop unrelated datasets. Do not overwrite an existing `dataset_card.yaml` without reviewing it and preserving supported human-curated values.
