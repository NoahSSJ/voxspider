# 当前项目清单与恢复

## 身份和记录

当前配置在 `config/hgf.yaml` 的 `datasets` 数组中：`name`、`repo_id`、`tos_url`、`enabled`、`status`。配置的 `repo_id` 对应清单顶层的 `id`，不是旧脚本期待的 `repo_id` 字段。

清单：`datasets/{name}/repo_manifest.json`。保留所有顶层原字段，例如 `card_data`、`license`、`commit_id`。文件条目包含：

```json
{
  "path": "data/train-00000.parquet",
  "size": 123,
  "blob_id": "上游 Git blob ID",
  "sha256": "上游 LFS SHA256 或 null",
  "local_verified": false,
  "tos_verified": false,
  "local_deleted": false
}
```

当前下载器远端通过后会增加 `tos_verification`：`uri`、`size`、十进制字符串 `local_crc64` 与 `remote_crc64`、`method: size_crc64`、带时区 `verified_at`，以及用于清理恢复的 `identity`（repo_id、commit_id、path、size、sha256、blob_id）。CRC64 存字符串避免跨语言整数精度丢失。必须绑定当前条目对应的完整目标 URI。

- `local_verified` 表示固定版本文件通过本地核验的历史事实，不代表文件此刻存在。
- `tos_verified` 是针对记录中目标对象的一次核验结果，不是永久保证。
- `local_deleted` 是清理记录，历史文件可能为 false；不能只因本地不存在就推定程序完成了删除。
- 不要求旧字段 `download_status`、`verification.passed`、`file_count`、`total_size`、`transfer_state.json`，不为适配旧脚本伪造这些字段。
- 文件数、字节数从清单计算；全量对账证据在独立 `source_reconciliation.json`/`archive_report.json`，不要塞进上游 `source/`。

## 恢复决策

| 状态 | 处理 |
| --- | --- |
| 下载未完成 | 保留本任务临时数据，核查 HF 续传行为；当前外层无进展重试尚非完整实现，不能承诺自动恢复 |
| 本地文件存在、上次验证为 true | 当前实现重新本地核验；不直接凭布尔值上传 |
| 上传成功、记录未保存 | 本地仍在时重新比对本地及远端，内容相同后保存记录，不覆盖 |
| 所有文件完成、尚未卡片交付 | 直接进入全量对账及卡片流程，不重下整个仓库 |
| 本地文件不存在、两端证据及 identity 匹配 | 下载器重新 stat 比对远端大小/CRC64，匹配后补记删除状态并跳过下载；不单凭 local_deleted 跳过 |
| 清理后远端丢失或查询失败 | 报错并保持 tos_verified=false；再次启动时重新下载，不标记成功 |
| 旧记录缺少 identity | 不直接信任为清理恢复证据，正常下载/本地核验；旧记录仍可用于已支持的远端对账 |
| 卡片远端已存在、登记未完成 | 重新核验当前全部源对象和卡片，匹配后 register，允许补登记 |
| 源/卡片冲突 | 停止，不覆盖、不删除、不修改预期值掩盖问题 |

若目标 prefix 或 commit 改变，不沿用旧目标状态，应作为独立归档处理；目录版本策略按手册和用户决定。已有远端卡片意味着该目录已宣告完成，不往这个目录继续混入另一版数据。

## 清理与后续卡片证据

只有远端大小及内容核验通过、核验记录持久保存后，才删除确切的本地文件。删除失败不影响已验证远端事实。保留清单、卡片、报告等管理文件；原始 README 也可能被清理，生成卡片时从相同 commit 或已核验的 TOS 读取所需小文件。

时长统计需要读取分片而证据未提前保存时，按一个分片一次的方式从远端读取并累加，不要求完整快照同时在本地。不要因统计需求重新调用全仓库下载器。对历史已归档数据执行清理需在用户任务范围内；查询 skill 不执行清理。
