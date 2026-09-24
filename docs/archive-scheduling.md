# 定时数据集归档

服务器 cron 每 3 小时运行一次 `scripts/run_archive_scan.sh`，扫描 `datasets/*/repo_manifest.json`。使用服务器北京时间，计划为每天 00:00、03:00、06:00、09:00、12:00、15:00、18:00、21:00。

## 清单状态

- `status`：全部源文件已有匹配固定 commit、路径及目标的本地与 TOS 核验记录。下载器初始写 false，处理结束时计算并持久写入；不以本地源文件是否还存在判定。清理失败不会抹去已经完成的传输事实。
- `is_datasetcard_gen_ok`：本地数据卡按 skill 审查通过，保存卡片 SHA256 和源清单指纹到 `datasetcard_generation`。
- `is_datasetcard_upload_ok`：远端卡片大小和 CRC64 与本地相同；保存在 `datasetcard_upload`。
- `is_archived`：通过 skill 的 register 后，完整最终清单已登记并落盘。
- `needs_manual_review`：暂停此数据集；后续轮次跳过，直到人工解除。
- `last_error`：包含时间、`manual_review` 或 `retry` 类型和具体原因；全部交付完成后清空。

以上布尔字段默认 false。旧清单由新版下载器读取时补齐；扫描时缺少 status 的清单视为未就绪，不能仅凭文件夹或配置 status 推断完成。

## 每轮流程

1. 获取全局扫描锁；上一轮未结束则退出。
2. 只选择 `status=true`、`is_archived!=true`、`needs_manual_review!=true`。
3. 获取数据集锁和目标前缀锁。下载器使用同样的锁，锁定后才读写清单。
4. 完整远端对账；缺失、多余、内容差异转人工，网络错误下轮重试。
5. 尚未生成卡片时调用本机 `codex exec --approve-for-me`，读取 `docs/archive-card-task.md` 和归档 skill；只生成本地卡片及证据。人工不确定项必须返回 manual_review，不等待交互、不擅自填值。
6. 校验生成记录和卡片内容；发布前重新对账。卡片远端相同则复用，不同则暂停，明确不存在才上传；上传后重新核验大小和 CRC64。
7. 调用 skill register 再次核验源对象、卡片并登记；将含 `is_archived=true` 的最终完整快照写入登记簿，再持久化本地清单。断电中断时保持可重试，登记按 repo ID + commit 幂等。

文件传输完成的清单记录用于续跑跳过，不重复逐文件 stat；最终交付全量对账保留。不同数据集逐个处理，一个暂停不会阻止其余数据集。

## 查看与手动操作

```bash
# 查看调度
crontab -l
# 只看本轮候选，不生成卡片或访问远端
scripts/run_archive_scan.sh --dry-run
# 立即执行一轮
scripts/run_archive_scan.sh
# 阅读 last_error 并处理问题后，解除该数据集暂停
scripts/run_archive_scan.sh --resume WorldSpeech
# 只为指定历史清单补字段；必须先停止会写此清单的旧下载进程
scripts/run_archive_scan.sh --initialize OpenDialog
```

解除暂停保留已完成阶段。若人工修改了卡片或需要重新审查，必须在无下载/归档写入时将 `is_datasetcard_gen_ok`、`is_datasetcard_upload_ok`、`is_archived` 设为 false 并移除旧 `datasetcard_generation`/`datasetcard_upload`；不能覆盖不同的远端卡片。

运行日志：`logs/archive_scan/cron.log`；最近一轮：`last_scan.json`；卡片生成日志：`<数据集名>-generation.log`。数据集内保留 `source_reconciliation.json`、`card_generation_report.json`、`card_publication.json`、`archive_report.json`。

## 运行条件

cron 使用 `scripts/run_archive_scan.sh` 中的 Python 和 PATH，以及 `config/archive_schedule.json` 中的 Codex 路径。Codex 使用现有账户登录和默认模型；保持登录有效，工具需要读取本地文件并访问 HF/TOS。继续使用自动审批，不关闭沙箱或绕过审批。生成最长 7200 秒，超时终止该任务及子进程组，下轮重试。审批拒绝转人工并保留原因。

新版锁与状态需要重启下载器才能生效；旧进程不会热加载代码，也不会写入新的 status 字段。不能在旧进程写清单时手工补字段。
