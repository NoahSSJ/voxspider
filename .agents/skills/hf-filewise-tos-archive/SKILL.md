---
name: hf-filewise-tos-archive
description: 在 voxspider 中完成 Hugging Face 固定 commit 逐文件归档、全量对账、raw 数据集卡生成发布与清单登记；也用于按 GitHub README 或 OpenSLR 数据集主页生成 raw 数据集卡，后两者不要求持久化归档清单。
---

# 数据集卡生成与 Hugging Face 逐文件归档

## 按来源选择流程

- **Hugging Face**：继续阅读下文，固定 commit、持久化清单、逐文件核验、CRC64、全量对账、卡片最后发布和登记要求全部保持不变。
- **GitHub**：读取 [GitHub / OpenSLR 数据集卡](references/github-openslr-card.md)，以指定仓库的 README 为主要依据生成卡片。
- **OpenSLR**：读取 [GitHub / OpenSLR 数据集卡](references/github-openslr-card.md)，以对应数据集主页为主要依据生成卡片。

GitHub / OpenSLR 不要求创建或持久化 `repo_manifest.json`，不要求 `card_data`、HF commit、HF 格式 CRC64 记录或 `archived.json` 登记，也不调用本技能的 `reconcile/register` 脚本。生成本地卡片不以传输完成为前提；发布时按该来源说明确认实际传输状态。GitHub README 指向 Zenodo 等文件托管站时，仍可按 GitHub 分支制作卡片，并如实记录文件来源；实际归档的是 HF 镜像时使用 HF 分支。

以下各节及 `references/transfer-state.md`、`references/card-and-publication.md` 专用于 Hugging Face。现有 `docs/archive-card-task.md` 和定时调度器仍是 HF 任务入口，本次新增分支不表示已接入其自动调度。

## Hugging Face 逐文件归档与交付

本 skill 面向当前 voxspider 项目，不调用 `cankao/` 的旧项目脚本。所有项目路径以包含 `core/huggingface.py`、`config/hgf.yaml` 和 `docs/open-dataset-ingestion-handbook.md` 的目录为根；本 skill 的脚本相对于本文件所在目录。

## 先明确实际能力

读取当前 `core/huggingface.py`、目标配置和已有清单，不把本 skill 当作已实现的程序功能。

- 当前入口是 `python core/huggingface.py`，它会调度配置中所有 `enabled: true` 且 `status: false` 的数据集，并保持监听。只处理用户选择的数据集；不要为方便测试悄悄启用其他任务。
- 每个仓库按 `file_manifest` 顺序处理一个完整文件：下载 → 本地核验 → 上传 → 远端核验 → 保存状态 → 删除本地文件 → 保存删除状态。仓库本身不需要 clone 或完整 snapshot_download；文件内的传输分块不是清单条目。
- 当前下载器会在远端核验结果持久保存后删除确切的本地源文件，再保存 `local_deleted: true`。不删除管理清单、卡片、报告或整个目录；清理失败要保留已验证的远端事实并报错。不要绕过此流程批量清理。
- 单个文件与缓存/临时数据余量必须放得下；报告全部文件总字节、最大文件、当前剩余磁盘，以及多个仓库并发的总在途空间。不得假定清理后没有 HF 缓存或之前失败的残留文件。空间不足时停止，不把“最大文件约 10 GB”的观察推广为所有仓库保证。
- 当前下载器不自动执行本 skill 的全量对账、卡片生成、发布和登记。调度器的 `finished`/成功日志不等于完整交付；核查失败任务是否被加入 `finished`，需要时在授权范围内重新启动指定任务。
- `core/huggingface_transfer.py` 是另一实现，状态结构不同，不能不经迁移直接混用本 skill 的工具。

## 权威与范围

开始归档前读手册 §0、§2、§3、§4、§8 的 raw 检查项；准备卡片时完整读 §6、§7。`docs/yaml_examples/` 只是参考，不覆盖正文规则。不为创建/使用 skill 修改手册。

默认只交付 raw，不执行 cleaned 转换。用户选择的 ASR/TTS 是用途；现有明确配置或用户决定优先，不因上游同时标 ASR/TTS 而反复询问。缺少决定目的地所需的信息时才澄清。

TOS 目标为：

```text
tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/{asr|tts}/{lang}/{dataset_id}/source/
```

数据集目录名按 §2.2 使用 `-`，固定 `dataset_repository` 保留 `_`，`source/` 内文件名和目录原样保留。配置 `name` 是本地工作目录名，可以与远端 dataset_id 不同；卡片 `dataset.id` 取已确认的远端数据集目录名，不能从本地中文别名猜测。

来源、固定 commit、许可和完整范围需有证据。保留全部 split/config/语种及 README、点文件。遇到许可不明、目标冲突或实质性范围缺失，按手册报告，不能发布正式卡片。仅依据当前任务及既有授权执行远端写入；用户已明确授权完整归档交付时无需对同一范围重复确认。只请求制作/审查 skill 或生成本地卡片，不授权上传。

## 清单和逐文件处理

读取 [references/transfer-state.md](references/transfer-state.md) 了解新清单和恢复规则。

以 `repo_manifest.json.id` 和 `commit_id` 标识来源；`file_manifest` 是唯一上传清单。首次按固定 commit 获取完整树，续跑沿用原清单和 commit。必要时对照同 commit 的官方树验证清单完整性。不要刷新 main 把不同版本混在一个归档里。

当前本地文件是 `datasets/{name}/{path}`，管理清单也在该工作目录；并非旧项目的 `datasets/{id}/source/{path}`。不要递归上传工作目录。只处理清单中的文件，避免把缓存、日志、管理清单、报告和生成卡片混入远端 `source/`。若上游路径与这些管理文件冲突，先解决布局冲突，不能跳过上游文件或覆盖管理文件。

每个文件：

1. 固定 commit 下载，核验字节数；有 LFS SHA256 则比较 SHA256，否则普通 Git 文件按 `blob <size>\0 + content` 核验 blob SHA1。摘要都缺失时明确报告只有大小核验，不冒充摘要核验。
2. 本地通过后保存 `local_verified: true`，开始远端处理前清空 `tos_verified` 和旧远端证据。
3. 对已核验的本地文件计算 TOS 兼容 CRC64；完整对象 URI 是 `tos_url.rstrip('/') + '/' + path`。
4. 查询已有对象；大小与 CRC64 一致才复用，不同则停止、不覆盖。明确不存在才 `tosutil cp -vchecksum`，上传后重新 `stat` 比较大小和 CRC64。查询失败不等于不存在；没有 CRC64 或解析失败保持未核验。
5. 原子保存并 fsync `tos_verified: true` 和 `tos_verification`；核对本地文件未在核验后变化，然后只 unlink 当前源文件，持久化父目录并保存 `local_deleted: true`。文件变化、链接路径或清单保存失败时不删除。

`tosutil` 文本格式需结合本机 `help` 和真实输出。已知对象不存在的返回包括 `NoSuchKey`，以及 `Status [404], error code [], error message [0017-00000003]`；不能将任意 404 当作对象不存在。保留异常而非改写成“未安装 tosutil”。不把 ETag 当作 SHA256/分片上传的 MD5。

检查并保证同一目标 key 只有一个写入者；`stat` 后 `cp` 不能原子地禁止竞态覆盖。若需多进程共同写同一目标，应先实现服务端禁止覆盖，不能声称当前 CLI 已提供此保证。

## 全量对账：远端与清单比较

无需全量本地文件。所有清单条目有匹配目标的两端核验记录后，在源对象和清单停止变化时运行：

```bash
python .agents/skills/hf-filewise-tos-archive/scripts/archive_checks.py reconcile \
  'datasets/本地工作目录/repo_manifest.json' \
  'tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/tts/zh/数据集/source/' \
  --report 'datasets/本地工作目录/source_reconciliation.json'
```

占位路径必须替换为已解析的实际路径。脚本验证：完整分页路径集合、所有预期对象的大小及 CRC64、对象数量和总字节，并用 `du` 交叉比较。预期总量是 `sum(file_manifest[*].size)`，不使用 HF `used_storage` 或样本数代替。

读取本次完整输出、退出码和报告，必须最终 `PASS`、退出 0、报告 `passed: true`。缺失、多余、大小/CRC64 不符、查询异常、分页不完整、清单变化均阻止交付。不能修改预期清单来凑对账，也不删除多余远端对象。若 CLI 格式不支持，调整并验证解析或明确实现 SDK 分页，不能降级为仅检查总量。

本工具要求当前 `core/huggingface.py` 的 CRC64 证据格式；旧清单仅布尔值或其他下载器格式不能伪造补齐，需重新核验。报告不证明存在跨整个桶的原子快照，发布期间应保持目标无人写入。

## 生成、发布卡片并登记

仅当本次全量对账通过后，读取 [references/card-and-publication.md](references/card-and-publication.md)，完成以下步骤：

1. 用同 commit 的清单、README、结构化 card_data 和可追溯统计，生成本地 `datasets/{name}/dataset_card.yaml`；按手册验证，不要求所有大分片同时在本地。
2. 需要发布且已获授权时，重新确认源对账、卡片内容和目标；卡片是完成信号，最后上传到 dataset 根目录，永远不在 `source/` 下。
3. 上传成功后核验远端卡片大小和 CRC64。远端已有相同卡片可复用并恢复登记；不同则停止，不覆盖。
4. 最后执行 `register`，它重新对账源对象，检查卡片基本结构、远端卡片内容一致性，再登记完整清单：

```bash
python .agents/skills/hf-filewise-tos-archive/scripts/archive_checks.py register \
  'datasets/本地工作目录/repo_manifest.json' \
  'tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/tts/zh/数据集/source/' \
  --card 'datasets/本地工作目录/dataset_card.yaml' \
  --registry archived.json \
  --report 'datasets/本地工作目录/archive_report.json'
```

`register` 只读 TOS，不代替卡片生成或上传；其机器结构检查不能替代手册的标签枚举、证据和许可审查。没有完整卡片上传核验就不能写登记簿。

`archived.json` 的结构为 `{"datasets": [完整的 repo_manifest.json 对象, ...]}`。保存 JSON 数据结构完整相等的副本，不筛字段、不存路径引用、不另造摘要。身份使用新清单的 `id + commit_id`；同版本再次完成更新原条目，不重复追加，不同版本保留历史。登记锁和原子替换防止并发丢记录。登记失败时报告“远端已交付，登记失败”，补登记前重新核验，不能重复覆盖卡片。

清理是下载器释放磁盘的步骤，不是远端交付成功的额外门槛；历史 `local_deleted: false` 的清单也可在上述交付流程完成后登记。`config.status`、单文件 `tos_verified` 和历史 archived 记录均不能单独证明当前完整交付；核查当前状态时查询远端。现有 archived 条目不授权自动删除本地数据。

## 交付报告

分别报告文件传输、全量对账、本地卡片、远端卡片、archived 登记五个阶段，不把其中一个阶段成功写成全部成功。包含 repo ID/commit、TOS 路径、文件数/字节、差异/异常、本地保留情况、报告及卡片路径。如实报告本地清理结果；若仅完成技能编写或只读审查，明确未执行真实归档。
