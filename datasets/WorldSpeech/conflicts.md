# WorldSpeech 固定版本差异说明

版本：`7fc2c2f19528b3d3972110a04e500098f6fc7f24`。以下差异来自同一个版本的上游文档和目录，不是混用 commit。

## 归档范围与配置数量

固定 README 声称 127 个语言地区变体；front matter 有 125 个 configs；清单实际有 128 个 data 目录。cs_cz 同时引用 cs_cz 与 cz_cs 两个目录，是明确的多目录映射；ga_ie、iu_ca 有文件但未列入 configs。

处理：本卡范围严格采用固定 commit 的 14,238 个清单文件，保留全部原路径；配置数不等于物理目录数，不按 README 数量增删文件。

## 时长表与实际目录差异

时长表 127 行合计 65,072.4 小时；rw_voa 在表中（14.5 小时）但没有同名清单目录；cz_cs、fr_ca 是实际目录却没有单独时长行。不能据目录缺失断言 rw_voa 内容未合并在其他目录，亦不能认定 ca_fr 与 fr_ca 重复。

处理：总时长及分项只作为官方声明保留，不等同于归档音频实测总量；准确样本数和分 split 时长未知。

## 语言数量与代码

README 声称 88 种语言；front matter language 列表 79 项；时长表有 90 个不同语言名称字符串（含方言和来源限定，不能直接当作 90 种语言）。存在 ckb、cnr、crs、grc、mfe、nso、yue 等三字母代码。ca_fr 在 README 指加拿大法语，不能机械按目录前缀理解为加泰罗尼亚语。

处理：标准化 data.languages 暂留空列表，表示完整映射未核实，不表示无语言；原始语言代码及完整时长表保存在 statistics，不强制合并或伪造两字母码。

## 混合许可及许可表缺项

仓库声明 CC BY-NC 4.0，同时明示各 config 来源条款适用、更严格的条款优先。be_by 声明 CC BY-NC-SA 4.0；ar_un、la_va、rm_ch 等另有用途限制或联系权利方的说明。ca_es、en_pk、fr_ca、ur_pk 无单独许可表条目。

处理：保留仓库许可、完整逐 config 原始条款和缺项清单；未列条目的来源条款记为未明确，不把当前用户授权视为上游权利许可。tts_training 仅为项目拟定用途，不代表所有内容已获训练或商业使用许可。

## 仅元数据的子集

README 明示 kh_km、la_lo、mm_my、vn_vi、cn_ug 仅包含元数据、转写和 source_url，没有内嵌音频。

处理：按固定仓库原样归档这五个子集；不额外抓取外链音频，不声称归档包含这些外链音频。

## 标注可用性与证据层级

README Schema 声明 human_transcript 和 source_start_s/source_end_s，因此声明有转写和源录音片段时间偏移；字段表未声明说话人、说话人分离、发音、音素或韵律标注。

处理：availability 仅表达官方公开 Schema 的标注能力：transcript/timestamps 为 true，其余为 false（官方 Schema 未提供），不是逐行数据实测结论；时间戳不是词级对齐，质量指标不是韵律标注。尚未核实各分片 Schema 差异及空值比例。
