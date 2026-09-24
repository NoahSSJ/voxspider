# 数据集卡与完成登记

本文件仅适用于 Hugging Face；GitHub / OpenSLR 使用 [独立卡片流程](github-openslr-card.md)。

## 证据和卡片生成

完整读项目手册 §6、§7、§8 raw 检查项后起草。按手册字段表和明确规则处理，示例文件不能扩展枚举；旧项目的生成器依赖旧字段、旧目录、外部模型配置，不能直接运行或复制其假设。

由使用本 skill 的助手根据证据生成 YAML，不需要调用另一个大模型服务。卡片生成是一项需要证据判断的工作，不宣称仅复制字段就完成了验证。已有人工卡片先审查并保留有据内容，用户要求修改时可在范围内更新本地卡片。

来源优先级：

1. 同 commit 的 `repo_manifest.json.card_data` 和已验证的 README、LICENSE、配置文件。当前 README 位于 `datasets/{name}/README.md`，不是旧项目 `source/README.md`。
2. 如果本地分片已清理，用保留的证据，或只取所需小文件：从已核验 TOS 对象或相同 HF commit 读取，并验证来源身份。不要拉取整个仓库。
3. 需要额外说明时访问官方主页/原始论文；将 URL、获取时间、与固定版本的关系、引用事实记录在本地 `upstream_evidence.json`。来自最新主页的事实不自动属于旧 commit。

维护值与来源的简短证据表。区分官方声称统计和实际文件统计；必要时逐分片读取元数据或 duration 列，边读边累加，单位转换与范围明确。不从文件大小、分片个数推算样本数/时长，不因一个抽样样本就声称全量标签分布。缺少必填时长或许可依据时报告阻塞，不编造数值、不发布占位卡片。

本地卡片位于 `datasets/{name}/dataset_card.yaml`；远端位置是 `tos_url` 去掉最后一层 `source` 后的 `dataset_card.yaml`。卡片 `dataset.id` 等于远端数据集目录名。

卡片审查至少包括：

- `schema_version: speech_dataset_spec_v1.2`，`status: raw`，§6.1 全部必填字段（允许空值的按手册填）。
- `source.owner_type: public`、`source.source_platform: dataset_repository`。
- raw `lineage.upstream: []`，`lineage.manifests.samples: null`；`derivation` 只记录确实发生的固定 commit 原样归档，不能写未执行的重采样、转码、筛选。将 commit 放在手册允许的描述/来源字段中，不随意扩展结构。
- 使用上游版本名，没版本概念则 `unversioned`；commit 是归档证据，不擅自当上游发布版本。
- `homepage` 为 `https://huggingface.co/datasets/{manifest.id}`；样本数、总时长、语种有证据，已知分 split 统计注明。
- card 里的 split 按手册 train/dev/test/all 表达；对上游 validation 的映射在统计里解释，raw 文件路径保持 validation 不改名。
- `labels.fixed`、`distribution`、`unknown` 同一路径互斥；标签路径、枚举和单/多值基数服从 §7；availability 的八个布尔字段齐全。未知不凭猜测填 true。
- `usage` 只使用手册枚举；不要把所有上游 task tags 自动映射为实际用途。
- `generation.generated_at` 带时区；`generation.validation` 只陈述实际执行的本地大小/摘要、TOS CRC64、全量路径/字节对账，不声称未做的解码、样本级对账。
- 源文件清单不是样本级 `manifest.jsonl`，不要据此自动添加 cleaned 风格 `manifest_schema`。

## 卡片发布门槛

发布卡片会触发手册所述下游检查。仅源文件上传成功不够，源全量对账必须通过、卡片必须完成上述证据和字段审查。

只读审查/生成本地卡的请求不包含发布授权。完整交付或明确发布请求已覆盖具体目标时，完成可审查的卡片和校验即可继续，不照搬旧 skill 的强制第二轮确认。缺少发布授权时先完成所有准备，再展示卡片路径、大小、SHA256、完整远端 URI、全量对账结果和发布后果，最后请求发布授权。

实际发布前：

1. 保持该目标没有并发写入，重新执行本 skill 的 `reconcile` 并要求当前 PASS/退出 0。
2. 计算本地卡片大小、SHA256 和 `tosutil hash ... -type=crc64`；确认未在审查后改变。
3. `tosutil stat` 精确的远端卡片 URI。存在时比较大小和本地 CRC64，完全相同可复用并进入登记；不同或缺乏可靠比较结果则停止。明确不存在才上传；任意 404 都不足以认定不存在。
4. 使用已解析的完整路径（下面是变量示意，先赋为实际值）上传单张卡：

```bash
tosutil cp "$card_path" "$card_uri" -vchecksum
```

不递归上传，不使用覆盖现有远端卡片作为修复方式。当前 CLI 的先查后写要求单写入者；有竞态风险时先实现服务端禁止覆盖。

5. 上传命令返回 0 后重新 `stat` 卡片，比对精确大小和 CRC64。不把命令成功或对象存在等同于内容已验证。
6. 执行 SKILL.md 的 `register` 命令。它会再次核查源对象与卡片，保存 `archive_report.json` 并把原清单完整写入 `archived.json`。

## archived.json

```json
{"datasets": []}
```

数组中每项为完整 `repo_manifest.json` 顶层对象，语义上等于读取原文件得到的 JSON，不是摘要，也不只保存文件路径。以 `id + commit_id` 幂等登记，保留不同固定版本；若同一版本在不同目标重复归档，这里保留最近一次完整清单，历史具体目标证据保存在各工作目录的报告中。

没有现成已完成证据时初始化为空；不要根据 `enabled/status`、控制台“下载完成”、部分文件 `tos_verified`、远端卡片存在或旧项目登记簿直接迁入。需要恢复旧登记时，对当前清单与卡片重新核验后再登记。

登记脚本不删除本地文件、不上传数据、不改 `config/hgf.yaml`、不修改源清单。不要求 `local_deleted` 为 true。本次任务的归档完成定义是“下载核验 + 上传核验 + 全量对账 + 卡片生成 + 卡片远端核验 + 本地登记”。
