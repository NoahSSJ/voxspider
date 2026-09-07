---
name: tos-dataset-status-auditor
description: 只读查询 voxspider 项目中 TOS raw 数据集的文件上传、全量大小对账、CRC64 内容核验、数据集卡存在及 archived.json 登记状态。适配 config/hgf.yaml 和逐文件 repo_manifest.json，支持单个或批量查询；不上传、不删除、不更新归档登记。
---

# TOS 数据集状态查询

用于回答“哪些数据集上传了”“这个数据集传完了吗”“卡片在不在”“登记了但远端还完整吗”等只读问题。实际完成上传、生成卡片或登记归档使用兄弟 skill `hf-filewise-tos-archive`，不能在查询时顺手执行修复。

## 输入与边界

从项目根目录运行，读取：

- `config/hgf.yaml`：目标数据集 `name`、`repo_id`、`tos_url`。查询不受 `enabled` 或 `status` 过滤，以便看到暂停和历史任务。
- `datasets/*/repo_manifest.json`：来源 `id`、`commit_id`、完整 `file_manifest` 及逐文件核验证据。
- `archived.json`：`datasets` 数组中的完整清单副本，以 `id + commit_id` 精确匹配历史登记。

取以上来源的并集；登记簿中有记录但本地目录已移走的版本，也可通过保存的远端核验 URI 查询。默认不扫描整个桶来发现完全未知的数据集；当前工具审计的是项目已知的数据集/版本。

不要求所有源文件还留在本地，不要求所有 `local_verified/tos_verified` 已经为 true。未完成的数据集也要能查询。禁止将旧项目 `download_status`、`verification.passed`、`file_count`、`total_size` 当作必需字段。

仅向 `tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/` 下的已确定目标发起只读请求。优先使用配置中的完整 `tos_url`；配置缺失时从清单 `tos_verification.uri` 和原始 `path` 反解一致前缀。配置和证据冲突、多个前缀、身份不一致、没有目标时报告 `needs_review`，不要靠仓库名或双 task tags 猜测路径。

不上传、覆盖、删除 TOS 对象；不启动下载器，不生成/发布卡片，不改配置、清单、`archived.json`，也不删除本地文件。默认仅向标准输出打印 JSON；用户需要保存报告时可以新建报告文件，拒绝覆盖已有文件。`tosutil` 自身可能按现有配置产生运行日志，不把这描述为本工具完全没有文件系统副作用。

## 执行

```bash
# 查询项目所有已知数据集，包括未启用的配置和历史归档版本
python .agents/skills/tos-dataset-status-auditor/scripts/audit_status.py

# 精确匹配完整 repo ID 或本地工作目录名，可重复 --dataset
python .agents/skills/tos-dataset-status-auditor/scripts/audit_status.py \
  --dataset yilele/synth-qa-taste-codec-chat

# 需要本地审计报告时，指定一个尚不存在的文件路径
python .agents/skills/tos-dataset-status-auditor/scripts/audit_status.py \
  --dataset yilele/synth-qa-taste-codec-chat \
  --json-output logs/tos-audits/一次查询的唯一报告名.json
```

报告名替换为本次时间戳等唯一名称。运行环境需有 Python 3.10+、PyYAML、已配置好的 `tosutil`。不打印凭证，不把密钥放进命令参数。脚本明确依赖本项目兄弟 skill 的 [分页和前缀校验工具](../hf-filewise-tos-archive/scripts/archive_checks.py)，它只调用其中的只读函数，不调用 `register` 或登记簿写入函数；移动或单独分发此 skill 时必须一并保留该依赖。

查询数量较多时，说明会完整分页列举并逐对象 stat，需要时间。不要将首 1000 条、抽样结果或 `du` 总量一致当作全量对账。当前脚本按数据集、对象串行查询，避免不受控并发。

## 输出语义

三态字段：`true` = 已证实通过，`false` = 已证实不满足，`null` = 未知/证据不足/查询异常。不要把 null 格式化成“否”。

| 字段 | 含义 |
| --- | --- |
| `upload_complete` | 清单中每个对象均存在；不代表大小和内容正确，也不排除额外对象 |
| `reconciliation_complete` | 完整路径集合、逐文件大小、文件数和总字节对账通过，并与 du 交叉核对；独立于 CRC64 |
| `checksum_complete` | 全部预期源对象的当前 CRC64 与匹配目标的已验证本地 CRC64 记录一致；缺失对象/摘要不符为 false，缺少证据为 null |
| `datacard_uploaded` | 精确 dataset 根目录下存在非空 dataset_card.yaml；不证明 schema 或内容正确 |
| `datacard_matches_local` | 有本地卡片和远端 CRC64 时比较大小与 CRC64；无本地卡片或缺少校验和为 null；不代替卡片规范审查 |
| `archived_registered` | archived.json 有相同 id + commit_id 的历史条目；不代表当前远端仍完整 |
| `archived_manifest_matches` | 历史完整清单副本与当前审计清单在 JSON 数据上相等；本地状态后来变化可能导致 false |
| `needs_review` | 身份、路径、查询或证据存在待确认问题 |

`local_verified_count` 和 `recorded_tos_verified_count` 只是清单保存的历史进度，不能作为本次远端事实。报告同时提供缺失、多余、大小/CRC64 不符、缺少校验依据和 stat 错误的明细。

对象不存在只识别明确 `NoSuchKey`，或 `Status [404]` 且错误消息为 `0017-00000003`。权限、网络、桶不存在及未知 CLI 输出均报告未知/错误。源查询失败时仍尽量独立查询卡片，显示可能提前发布的卡片。

退出码 `0` 表示本次查询完成且无需人工解释查询/证据错误，不代表所有数据集均已交付；正常查到缺失文件可能仍返回 0。退出码 `2` 表示至少存在未知、证据缺失、身份冲突或执行错误，其他数据集的有效结果仍应展示。

## 向用户报告

输出紧凑表格：数据集/commit、上传齐全、大小对账、CRC64、卡片存在、卡片匹配、历史登记。展示目标 URI 和具体异常数字；用“未知”表示 null。

明确区分源文件传完但未发布卡片、卡片存在但源不完整、历史已登记但当前失败等情况。不要用单个布尔值合成“完整交付”。不因当前看到相同内容就自动修改 archived.json；有修复请求时再按归档 skill 的证据与权限流程执行。

若下载器仍在写入，清单或远端可能变化，说明报告是时间点观测；清单在本次查询期间变化会使源核验状态失效，不能称为发布门槛的稳定结果。
