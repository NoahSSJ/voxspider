import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv
from openai import OpenAI


def get_root_path(current_path: Path) -> Path:
    """从当前路径逐级向上查找包含 .git 的项目根目录。"""
    path = current_path.resolve()
    if path.is_file():
        path = path.parent

    for directory in (path, *path.parents):
        if (directory / ".git").exists():
            return directory

    raise FileNotFoundError(f"未找到包含 .git 的项目根目录：{path}")


ROOT_DIR = get_root_path(Path(__file__))
DEFAULT_DATASETS_DIR = ROOT_DIR / "datasets"
SKILL_PATH = (
    ROOT_DIR / ".agents" / "skills" / "dataset-card-generator" / "SKILL.md"
)
HANDBOOK_PATH = ROOT_DIR / "docs" / "open-dataset-ingestion-handbook.md"
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_BASE_URL = "https://api.deepseek.com"
REPOSITORY_INFO_FILENAMES = ("repo_manifest.json", "repo_metadata.json")
UPSTREAM_EVIDENCE_FILENAME = "upstream_evidence.json"

REQUIRED_PATHS = (
    "schema_version",
    "dataset",
    "dataset.id",
    "dataset.name",
    "dataset.version",
    "dataset.split",
    "status",
    "source",
    "source.owner_type",
    "source.source_platform",
    "lineage",
    "lineage.upstream",
    "lineage.derivation",
    "lineage.manifests",
    "lineage.manifests.samples",
    "data",
    "data.num_samples",
    "data.total_duration_hours",
    "data.languages",
    "labels",
    "labels.fixed",
    "labels.distribution",
    "labels.unknown",
    "labels.availability",
    "usage",
    "usage.intended_use",
    "usage.not_recommended_for",
    "homepage",
    "generation",
    "generation.generated_at",
)

ALLOWED_SPLITS = {"train", "dev", "test", "all"}
ALLOWED_STATUS = {"raw"}
ALLOWED_USAGE = {
    "asr_training",
    "asr_evaluation",
    "tts_training",
    "tts_evaluation",
    "voice_cloning",
    "speech_llm_training",
}
AVAILABILITY_KEYS = {
    "transcript",
    "timestamps",
    "speaker_labels",
    "diarization",
    "pronunciation",
    "phoneme",
    "prosody",
}

ALLOWED_LABEL_PATHS = {
    "language",
    "locale",
    "code_switching",
    "secondary_languages",
    "language.code_switching",
    "language.secondary_languages",
    "speaker.speaker_id",
    "speaker.voice_id",
    "speaker.perceived_voice_gender",
    "speaker.age_group",
    "speaker.speaker_consistency",
    "interaction.type",
    "interaction.dialogue_role",
    "speaking_style",
    "scenario.environment",
    "scenario.channel",
    "scenario.product_context",
    "scenario.content_genre",
    "acoustic",
    "device",
    "domain",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate raw dataset_card.yaml files from verified datasets."
    )
    parser.add_argument(
        "--datasets-dir",
        type=Path,
        default=DEFAULT_DATASETS_DIR,
        help="Root directory containing downloaded datasets.",
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        help="Only process this repo id, for example owner/name. Repeatable.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("MODEL", DEFAULT_MODEL),
        help=f"Model name (default: MODEL or {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing dataset_card.yaml after validation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Call the model and validate, but do not write dataset_card.yaml.",
    )
    parser.add_argument(
        "--max-datasets",
        type=int,
        default=None,
        help="Maximum number of discovered datasets to process.",
    )
    return parser.parse_args()


def get_path(mapping: dict[str, Any], dotted_path: str) -> tuple[bool, Any]:
    value: Any = mapping
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def extract_handbook_sections(handbook: str) -> str:
    start = handbook.find("## 6.")
    section_seven = handbook.find("## 7.", start)
    section_eight = handbook.find("## 8.", start)
    raw_start = handbook.find("### raw", section_eight)
    cleaned_start = handbook.find("### cleaned", raw_start)
    if start == -1:
        raise ValueError("手册中未找到第 6 节")
    if section_seven == -1:
        raise ValueError("手册中未找到第 7 节")
    if section_eight == -1:
        raise ValueError("手册中未找到第 8 节，无法确定第 7 节是否完整")
    if raw_start == -1 or cleaned_start == -1:
        raise ValueError("手册第 8 节中未找到完整的 raw 自检清单")
    if not start < section_seven < section_eight < raw_start < cleaned_start:
        raise ValueError("手册第 6、7、8 节的顺序异常")
    schema_and_enums = handbook[start:section_eight].strip()
    raw_checklist = handbook[section_eight:cleaned_start].strip()
    return f"{schema_and_enums}\n\n{raw_checklist}"


def summarize_repo_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    verification = metadata.get("verification") or {}
    return {
        "repo_id": metadata.get("repo_id"),
        "homepage_url": metadata.get("homepage_url"),
        "commit_id": metadata.get("commit_id"),
        "source_identifier": metadata.get("source_identifier"),
        "source_type": metadata.get("source_type"),
        "license": metadata.get("license"),
        "language": metadata.get("language"),
        "task_categories": metadata.get("task_categories"),
        "pretty_name": metadata.get("pretty_name"),
        "card_data": metadata.get("card_data"),
        "file_count": metadata.get("file_count"),
        "total_size": metadata.get("total_size"),
        "download_status": metadata.get("download_status"),
        "verification": {
            "passed": verification.get("passed"),
            "verified_at": verification.get("verified_at"),
            "expected_file_count": verification.get("expected_file_count"),
            "checked_sha256_files": verification.get("checked_sha256_files"),
            "sha256_verification_enabled": verification.get(
                "sha256_verification_enabled"
            ),
            "missing_file_count": len(verification.get("missing_files") or []),
            "size_mismatch_count": len(
                verification.get("size_mismatches") or []
            ),
            "checksum_mismatch_count": len(
                verification.get("checksum_mismatches") or []
            ),
        },
    }


def discover_datasets(
    datasets_dir: Path, selected_repo_ids: list[str]
) -> list[Path]:
    if selected_repo_ids:
        discovered = sorted(
            {
                info_path.parent
                for filename in REPOSITORY_INFO_FILENAMES
                for info_path in datasets_dir.glob(f"*/{filename}")
            }
        )
        by_repo_id: dict[str, Path] = {}
        for dataset_dir in discovered:
            metadata_path = find_repository_info_path(dataset_dir)
            if metadata_path is None:
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            repo_id = metadata.get("repo_id")
            if isinstance(repo_id, str):
                if repo_id in by_repo_id and by_repo_id[repo_id] != dataset_dir:
                    raise ValueError(f"多个数据集目录使用相同 repo_id: {repo_id}")
                by_repo_id[repo_id] = dataset_dir
        return [
            by_repo_id.get(
                repo_id, datasets_dir / repo_id.rstrip("/").rsplit("/", 1)[-1]
            )
            for repo_id in selected_repo_ids
        ]

    return sorted(
        {
            info_path.parent
            for filename in REPOSITORY_INFO_FILENAMES
            for info_path in datasets_dir.glob(f"*/{filename}")
        }
    )


def find_repository_info_path(dataset_dir: Path) -> Path | None:
    """优先读取新版 repo_manifest.json，并兼容旧版元数据文件。"""
    for filename in REPOSITORY_INFO_FILENAMES:
        path = dataset_dir / filename
        if path.is_file():
            return path
    return None


def source_profile(metadata: dict[str, Any]) -> tuple[str, str | None, str | None]:
    homepage = metadata.get("homepage_url")
    repo_id = metadata.get("repo_id")
    source_type = metadata.get("source_type")
    hostname = urlparse(homepage).hostname if isinstance(homepage, str) else None
    is_huggingface = source_type == "huggingface" or hostname in {
        "huggingface.co",
        "www.huggingface.co",
    }
    if is_huggingface:
        return "huggingface", "archive_hf_snapshot", (
            f"hf:{repo_id}" if isinstance(repo_id, str) else None
        )
    return str(source_type or "website"), "archive_source_snapshot", homepage


def validate_card(
    card: Any, dataset_dir: Path, metadata: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    if not isinstance(card, dict):
        return ["模型输出的 card 不是对象"]

    for dotted_path in REQUIRED_PATHS:
        present, _ = get_path(card, dotted_path)
        if not present:
            errors.append(f"缺少必填字段: {dotted_path}")

    if errors:
        return errors

    if card["schema_version"] != "speech_dataset_spec_v1.2":
        errors.append("schema_version 必须为 speech_dataset_spec_v1.2")

    if card["dataset"]["id"] != dataset_dir.name:
        errors.append(
            "dataset.id 必须等于数据集目录名: " f"{dataset_dir.name}"
        )

    split = card["dataset"]["split"]
    if split is not None and split not in ALLOWED_SPLITS:
        errors.append(f"dataset.split 非法: {split}")

    status = card["status"]
    if status != "raw":
        errors.append("当前流程只允许 status: raw")

    if card["source"]["owner_type"] != "public":
        errors.append("source.owner_type 必须为 public")
    if card["source"]["source_platform"] != "dataset_repository":
        errors.append("source.source_platform 必须为 dataset_repository")

    if not isinstance(card["lineage"]["upstream"], list):
        errors.append("lineage.upstream 必须是列表")
    elif card["lineage"]["upstream"]:
        errors.append("raw card 的 lineage.upstream 必须为空列表")
    if not isinstance(card["lineage"]["derivation"], list):
        errors.append("lineage.derivation 必须是列表")
    else:
        source_kind, expected_step, expected_recipe_uri = source_profile(metadata)
        accepted_steps = {expected_step}
        # Backward compatibility for non-Hugging Face cards delivered before
        # archive_source_snapshot was introduced. New generation is instructed
        # to use the source-neutral step.
        if source_kind != "huggingface":
            accepted_steps.add("archive_hf_snapshot")
        archive_steps = [
            item
            for item in card["lineage"]["derivation"]
            if isinstance(item, dict) and item.get("step") in accepted_steps
        ]
        if not archive_steps:
            errors.append(
                "raw card 的 lineage.derivation 缺少来源匹配的归档步骤: "
                f"{expected_step}"
            )
        elif expected_recipe_uri and any(
            item.get("recipe_uri") != expected_recipe_uri
            for item in archive_steps
        ):
            errors.append(
                "lineage.derivation.recipe_uri 与实际下载来源不一致: "
                f"应为 {expected_recipe_uri}"
            )
    if not isinstance(card["lineage"]["manifests"], dict):
        errors.append("lineage.manifests 必须是对象")
    elif card["lineage"]["manifests"].get("samples") is not None:
        errors.append("raw card 的 lineage.manifests.samples 必须为 null")

    if card.get("manifest_schema") is not None:
        errors.append("raw card 不应包含 cleaned 样本清单的 manifest_schema")

    total_duration = card["data"]["total_duration_hours"]
    if (
        isinstance(total_duration, bool)
        or not isinstance(total_duration, (int, float))
        or total_duration < 0
    ):
        errors.append(
            "第 8 节 raw 自检要求 data.total_duration_hours "
            "必须填写非负数值，不能为 null"
        )

    statistics = card["data"].get("statistics")
    if isinstance(statistics, dict) and isinstance(statistics.get("splits"), dict):
        missing_split_durations = sorted(
            str(split_name)
            for split_name, split_stats in statistics["splits"].items()
            if not isinstance(split_stats, dict)
            or isinstance(split_stats.get("total_duration_hours"), bool)
            or not isinstance(split_stats.get("total_duration_hours"), (int, float))
        )
        if missing_split_durations:
            errors.append(
                "第 8 节 raw 自检要求已记录 split 的时长齐全: "
                f"{missing_split_durations}"
            )

    homepage = card["homepage"]
    expected_homepage = metadata.get("homepage_url")
    if not homepage:
        errors.append("homepage 不能为空")
    elif expected_homepage and homepage.rstrip("/") != expected_homepage.rstrip("/"):
        errors.append(
            f"homepage 与 repo_manifest.json 不一致: {homepage}"
        )

    generated_at = card["generation"]["generated_at"]
    if not isinstance(generated_at, str):
        errors.append("generation.generated_at 必须是 ISO 8601 字符串")
    else:
        try:
            parsed_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            if parsed_time.tzinfo is None:
                errors.append("generation.generated_at 必须包含时区")
        except ValueError:
            errors.append("generation.generated_at 不是有效的 ISO 8601 时间")

    usage = card["usage"]
    for field in ("intended_use", "not_recommended_for"):
        values = usage[field]
        if not isinstance(values, list):
            errors.append(f"usage.{field} 必须是列表")
            continue
        if any(not isinstance(value, str) for value in values):
            errors.append(f"usage.{field} 的每一项都必须是字符串")
            continue
        invalid = sorted(set(values) - ALLOWED_USAGE)
        if invalid:
            errors.append(f"usage.{field} 包含非法值: {invalid}")

    labels = card["labels"]
    fixed = labels["fixed"]
    distribution = labels["distribution"]
    unknown = labels["unknown"]
    if not isinstance(fixed, dict) or not isinstance(distribution, dict):
        errors.append("labels.fixed 和 labels.distribution 必须是对象")
    elif not isinstance(unknown, list):
        errors.append("labels.unknown 必须是列表")
    else:
        duplicates = (
            set(fixed) & set(distribution)
            | set(fixed) & set(unknown)
            | set(distribution) & set(unknown)
        )
        if duplicates:
            errors.append(f"标签路径在多个区块重复: {sorted(duplicates)}")
        used_paths = set(fixed) | set(distribution) | set(unknown)
        invalid_paths = sorted(used_paths - ALLOWED_LABEL_PATHS)
        if invalid_paths:
            errors.append(f"labels 包含第 7 节未定义路径: {invalid_paths}")

    availability = labels["availability"]
    if not isinstance(availability, dict):
        errors.append("labels.availability 必须是对象")
    else:
        missing_availability = sorted(AVAILABILITY_KEYS - set(availability))
        if missing_availability:
            errors.append(
                "labels.availability 缺少字段: " f"{missing_availability}"
            )
        extra_availability = sorted(set(availability) - AVAILABILITY_KEYS)
        if extra_availability:
            errors.append(
                "labels.availability 包含手册未定义字段: "
                f"{extra_availability}"
            )
        non_boolean = sorted(
            key
            for key, value in availability.items()
            if key in AVAILABILITY_KEYS and not isinstance(value, bool)
        )
        if non_boolean:
            errors.append(
                "labels.availability 必须使用布尔值: " f"{non_boolean}"
            )

    provenance = card.get("provenance")
    if isinstance(provenance, dict):
        expected_revision = metadata.get("commit_id")
        if expected_revision and provenance.get("revision") != expected_revision:
            errors.append("provenance.revision 与 repo_manifest.json 不一致")

    return errors


def build_instructions(skill_text: str, handbook_sections: str) -> str:
    return f"""{skill_text}

以下内容是当前项目第 6、7 节以及第 8 节 raw 自检清单的权威规范，必须遵守：

{handbook_sections}

额外执行要求：
- 上面的手册第 6、7 节是输出结构、字段语义、枚举和空值处理的唯一权威，第 8 节 raw 清单是交付验收门禁；严禁扩展、近似映射或凭常识补充。
- 只生成 raw dataset card。
- card 必须包含手册的全部最小必填字段。
- data.total_duration_hours 必须有证据支持并填写数值；如果 data.statistics.splits 记录了 split，每个已记录 split 都必须填写 total_duration_hours。
- dataset_card.yaml 必须位于数据集根目录，不能位于 source/ 中。
- repo_manifest 摘要优先于其他证据中的来源身份和核验信息。
- Hugging Face 来源使用 step: archive_hf_snapshot 和 recipe_uri: hf:<repo_id>；其他来源使用 step: archive_source_snapshot，recipe_uri 必须逐字使用 repo_manifest.homepage_url。严禁为网站来源虚构 hf: URI、commit 或 Dataset Card 结构。
- 不要把下载器生成的 repo_manifest.json 当成上游仓库文件。
- generation.generated_at 使用当前时间并包含时区。
- 如果证据冲突会改变必填字段，将 needs_review 设为 true 并逐条写入 issues。
- 普通未知字段按手册填 null、空集合或 labels.unknown，不要仅因普通未知值设置 needs_review。
- 只有上游证据明确支持时才能填写 labels.fixed 或 labels.distribution；不得从“媒体语音”“对话”“语音数据”等宽泛描述推断 domain: general 或其他标签。缺少明确证据的标签必须放入 labels.unknown。
- 手册中的所有枚举都是封闭集合，严禁新增枚举或映射成含义相近但不准确的枚举。
- 如果上游用途无法用 usage.intended_use 的现有枚举准确表达，必须填写 intended_use: []；可以在 issues 说明，但 needs_review 必须为 false，不能因此阻止生成。
- 输出语言以中文为主，枚举和字段名保持规范原文。
- 只输出一个合法 JSON 对象，不要输出 Markdown 代码块或 JSON 之外的文字。
- JSON 必须采用这个顶层结构：
  {{"card": {{完整 dataset card}}, "needs_review": false, "issues": []}}
"""


def generate_card(
    client: OpenAI,
    model: str,
    instructions: str,
    dataset_dir: Path,
    upstream_evidence: str,
    metadata_summary: dict[str, Any],
) -> dict[str, Any]:
    input_text = f"""请为以下已下载并核验的数据集生成 dataset card。

数据集目录名：{dataset_dir.name}

repo_manifest 摘要：
{json.dumps(metadata_summary, ensure_ascii=False, indent=2)}

上游证据（可能来自仓库 README，也可能是带来源 URL 和抓取时间的结构化证据）：
{upstream_evidence}
"""
    # deepseek-v4-pro 暂不支持 Responses API，因此使用兼容 OpenAI SDK 的
    # Chat Completions JSON Output。完整字段约束继续由本地校验器负责。
    for attempt in range(1, 3):
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": input_text},
            ],
            response_format={"type": "json_object"},
            reasoning_effort="low",
            max_tokens=16384,
            stream=False,
        )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("模型输出达到 max_tokens，JSON 可能被截断")

        content = choice.message.content
        if content and content.strip():
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise RuntimeError("模型输出的 JSON 顶层不是对象")
            return parsed

        logging.warning(
            "DeepSeek returned empty JSON content; retrying attempt=%d", attempt
        )

    raise RuntimeError("DeepSeek 连续两次返回空内容")


def write_yaml_atomic(path: Path, card: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(
            card,
            file,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    temporary_path.replace(path)


def process_dataset(
    client: OpenAI,
    model: str,
    instructions: str,
    dataset_dir: Path,
    overwrite: bool,
    dry_run: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "dataset": str(dataset_dir),
        "status": "failed",
        "issues": [],
    }
    metadata_path = find_repository_info_path(dataset_dir)
    readme_path = dataset_dir / "source" / "README.md"
    evidence_path = dataset_dir / UPSTREAM_EVIDENCE_FILENAME
    output_path = dataset_dir / "dataset_card.yaml"

    if not dataset_dir.is_dir():
        result["issues"] = ["数据集目录不存在"]
        return result
    if metadata_path is None:
        result["issues"] = [
            "缺少 repo_manifest.json（也未找到兼容的 repo_metadata.json）"
        ]
        return result
    if readme_path.is_file():
        upstream_evidence = readme_path.read_text(encoding="utf-8")
    elif evidence_path.is_file():
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            result["issues"] = [f"{UPSTREAM_EVIDENCE_FILENAME} 必须是 JSON 对象"]
            return result
        source_url = evidence.get("source_url")
        retrieved_at = evidence.get("retrieved_at")
        expected_homepage = json.loads(
            metadata_path.read_text(encoding="utf-8")
        ).get("homepage_url")
        evidence_errors = []
        if not isinstance(source_url, str) or not source_url:
            evidence_errors.append("source_url 缺失")
        elif expected_homepage and source_url.rstrip("/") != expected_homepage.rstrip("/"):
            evidence_errors.append("source_url 与 repo_manifest.json.homepage_url 不一致")
        if not isinstance(retrieved_at, str):
            evidence_errors.append("retrieved_at 缺失")
        else:
            try:
                parsed_retrieved_at = datetime.fromisoformat(
                    retrieved_at.replace("Z", "+00:00")
                )
                if parsed_retrieved_at.tzinfo is None:
                    evidence_errors.append("retrieved_at 必须包含时区")
            except ValueError:
                evidence_errors.append("retrieved_at 不是有效的 ISO 8601 时间")
        if evidence_errors:
            result["issues"] = [
                f"{UPSTREAM_EVIDENCE_FILENAME}: {error}"
                for error in evidence_errors
            ]
            return result
        upstream_evidence = json.dumps(evidence, ensure_ascii=False, indent=2)
    else:
        result["issues"] = [
            f"缺少 source/README.md 或 {UPSTREAM_EVIDENCE_FILENAME}"
        ]
        return result

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    verification = metadata.get("verification") or {}
    if metadata.get("download_status") != "verified" or not verification.get(
        "passed"
    ):
        result["status"] = "skipped_unverified"
        result["issues"] = ["下载状态或核验结果不是 verified/passed"]
        return result

    if output_path.exists() and not overwrite:
        existing_card = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        validation_errors = validate_card(existing_card, dataset_dir, metadata)
        result["issues"] = validation_errors
        result["status"] = (
            "existing_valid" if not validation_errors else "existing_invalid"
        )
        result["output"] = str(output_path)
        return result

    # Supplement archived upstream text with measured facts without modifying source/.
    measured_path = dataset_dir / "measured_statistics.json"
    if measured_path.exists():
        measured = json.loads(measured_path.read_text(encoding="utf-8"))
        if (
            not isinstance(measured, dict)
            or measured.get("source_url") != metadata.get("homepage_url")
            or not measured.get("commit_id")
            or measured.get("commit_id") != metadata.get("commit_id")
        ):
            result["issues"] = ["measured_statistics.json 来源或 commit 与 manifest 不一致"]
            return result
        upstream_evidence += (
            "\n\n本地全量测量证据（统计范围以 method 和 additional_scope_note 为准，"
            "不得将不同范围相加；不得描述为上游官方声称值）：\n"
            + json.dumps(measured, ensure_ascii=False, indent=2)
        )

    generated = generate_card(
        client=client,
        model=model,
        instructions=instructions,
        dataset_dir=dataset_dir,
        upstream_evidence=upstream_evidence,
        metadata_summary=summarize_repo_metadata(metadata),
    )
    card = generated.get("card")
    if isinstance(card, dict) and isinstance(card.get("generation"), dict):
        # 使用服务器时间，避免模型生成无时区或不准确的时间戳。
        card["generation"]["generated_at"] = datetime.now().astimezone().isoformat()
    model_issues = generated.get("issues") or []
    validation_errors = validate_card(card, dataset_dir, metadata)
    result["issues"] = model_issues + validation_errors

    if generated.get("needs_review"):
        result["status"] = "needs_review"
        return result
    if validation_errors:
        result["status"] = "validation_failed"
        return result

    if dry_run:
        result["status"] = "dry_run_valid"
    else:
        write_yaml_atomic(output_path, card)
        result["status"] = "generated"
        result["output"] = str(output_path)
    return result


def main() -> int:
    load_dotenv(ROOT_DIR / ".env")
    args = parse_args()

    api_key = os.getenv("API_KEY")
    base_url = os.getenv("BASE_URL", DEFAULT_BASE_URL)
    if not api_key:
        print("错误：.env 中未设置 API_KEY", file=sys.stderr)
        return 2
    if not base_url:
        print("错误：.env 中的 BASE_URL 不能为空", file=sys.stderr)
        return 2
    if args.max_datasets is not None and args.max_datasets < 1:
        print("错误：--max-datasets 必须大于 0", file=sys.stderr)
        return 2
    if not SKILL_PATH.is_file():
        print(f"错误：找不到 Skill：{SKILL_PATH}", file=sys.stderr)
        return 2
    if not HANDBOOK_PATH.is_file():
        print(f"错误：找不到手册：{HANDBOOK_PATH}", file=sys.stderr)
        return 2

    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    handbook_sections = extract_handbook_sections(
        HANDBOOK_PATH.read_text(encoding="utf-8")
    )
    instructions = build_instructions(skill_text, handbook_sections)
    dataset_dirs = discover_datasets(args.datasets_dir, args.dataset)
    if args.max_datasets is not None:
        dataset_dirs = dataset_dirs[: args.max_datasets]

    logs_dir = ROOT_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = logs_dir / f"dataset_card_generation_{timestamp}.json"
    logging.basicConfig(
        filename=logs_dir / f"dataset_card_generation_{timestamp}.log",
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        encoding="utf-8",
    )

    client = OpenAI(api_key=api_key, base_url=base_url)
    results = []
    for index, dataset_dir in enumerate(dataset_dirs, start=1):
        print(f"[{index}/{len(dataset_dirs)}] {dataset_dir}")
        try:
            result = process_dataset(
                client=client,
                model=args.model,
                instructions=instructions,
                dataset_dir=dataset_dir,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )
        except Exception as error:
            logging.exception("dataset card generation failed: %s", dataset_dir)
            result = {
                "dataset": str(dataset_dir),
                "status": "failed",
                "issues": [str(error)],
            }
        results.append(result)
        print(f"  -> {result['status']}")
        for issue in result.get("issues", []):
            print(f"     - {issue}")

    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "model": args.model,
        "base_url": base_url,
        "dry_run": args.dry_run,
        "results": results,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"报告：{report_path}")

    successful_statuses = {"generated", "dry_run_valid", "existing_valid"}
    return 0 if all(item["status"] in successful_statuses for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
