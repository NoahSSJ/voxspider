# 开源语音数据集引入手册

> **给谁看**:负责开源语音数据集引入的同学。
> **做什么**:把一个开源语音数据集,从上游下载下来 → 原样归档到 TOS → 转换成我们的标准成品格式。
> **本文是自包含的**:所有需要的规范、字段、枚举、检查项都写在这里,不需要再看别的文档或代码仓库。
> 遇到本文没写到的情况,不要自行发挥 —— 记下来问对接人。

---

## 0. 先读这一节:你的工作范围与红线

### 0.1 你的工作位置

```
tos://vox-asr-audio-data/SPEECH-DATA-TEMP/
```

本手册里所有路径都在这个前缀之下。

### 0.2 三条红线

1. **只增不改。** 已经上传成功的文件不要覆盖、不要删除。需要改动时先问对接人。
2. **不确定就停下来问。** 数据引入的错误往往是"静默"的 —— 不报错,只是少了一部分数据,
   几个月后训练效果不对才被发现。宁可多问一次。
3. **不要"先传一部分试试"。** 半份数据集比没有更糟:后面的人会以为它是完整的。
   要么完整交付,要么明确标注"未完成"。

### 0.3 你要交付的三样东西

| 阶段               | 位置                            | 交付物                                           |
| ------------------ | ------------------------------- | ------------------------------------------------ |
| ① 下载            | 你自己的机器                    | (中间产物,交付后删掉)                            |
| ② 原始档`raw`   | `SPEECH-DATA-TEMP/raw/…`     | 上游原样的文件 + 一份`dataset_card.yaml`       |
| ③ 成品`cleaned` | `SPEECH-DATA-TEMP/cleaned/…` | 标准格式的音频与清单 + 一份`dataset_card.yaml` |

⚠ **第 ③ 步目前只做 ASR 数据集**。TTS 数据集做到第 ② 步(raw)就算交付完成,不用转 cleaned。

---

## 1. 整体流程

```
   ┌─────────────┐     ┌──────────────────┐     ┌────────────────────┐
   │  上游       │     │  raw 原始档      │     │  cleaned 成品      │
   │  HuggingFace│ ──▶ │  一个字节不改    │ ──▶ │  统一格式、可直接  │
   │  OpenSLR…   │     │  永久留档        │     │  喂训练/评测       │
   └─────────────┘     └──────────────────┘     └────────────────────┘
      第 3 节              第 4 节                    第 5 节
```

**为什么要存两份?**

- `raw` 是**证据**。上游哪天下架了、改版了,我们还能回到原始状态,也能重新转换。
  所以它必须是**原样**的:不解压(除非另有约定)、不转码、不改目录结构、不删文件。
- `cleaned` 是**产品**。格式统一,下游拿到就能用,不用每个使用方自己写解析。
  所以它必须**符合我们的标准格式**,哪怕上游长得千奇百怪。

---

## 2. 目录结构

### 2.1 完整骨架

```
tos://vox-asr-audio-data/SPEECH-DATA-TEMP/
├── raw/
│   └── dataset_repository/          # 来源类型:数据集仓库(开源集都走这个)
│       └── {asr|tts}/               # 用途
│           └── {lang}/              # 语种
│               └── {dataset}/       # 数据集目录,目录名 = dataset_id
│                   ├── dataset_card.yaml
│                   └── source/      # 上游原始内容,保留其原本的目录结构
│                       └── …
└── cleaned/
    └── dataset_repository/
        └── {asr|tts}/
            └── {lang}/
                └── {dataset}/
                    ├── dataset_card.yaml
                    └── …            # 格式见第 5 节
```

### 2.2 每一层怎么填

**`dataset_repository`** —— 固定值,照抄。

**`{asr|tts}`(用途)** —— 这个数据集**打算用来做什么**:

| 值      | 什么时候用                                                               |
| ------- | ------------------------------------------------------------------------ |
| `asr` | 语音识别:有转写文本,关心"说了什么"。**绝大多数情况填这个。**       |
| `tts` | 语音合成:关心音质、音色、韵律,通常要求录音干净、单说话人、有明确音色标识 |

⚠ 这是**立项时的打算**,不是数据的固有属性 —— 同一批录音理论上两边都能用。
拿不准就问对接人。

**`{lang}`(语种)** —— ISO 639-1 两位小写字母码:

| 语种 | 码     | 语种       | 码     |
| ---- | ------ | ---------- | ------ |
| 英语 | `en` | 印尼语     | `id` |
| 法语 | `fr` | 斯瓦西里语 | `sw` |
| 俄语 | `ru` | 豪萨语     | `ha` |
| 中文 | `zh` |            |        |

- **多语种数据集填 `multi`**(如 FLEURS、Common Voice 全量)。
- 只写两位码,**不要**写 `English` / `en-US` / `cmn` 这类。
  (card 里另有一个 `locale` 字段可以写 `en-US` 这种带地区的,见 §6。)

**`{dataset}`(数据集目录名)** —— 这是 **dataset_id**,规则:

1. **沿用上游原名**。`kensho/spgispeech` → `SPGISpeech`,`intronhealth/afrispeech-200` → `afrispeech-200`。
2. **分隔符一律用 `-`**,不用 `_` 和空格。
3. **不加自己发明的后缀**。哪怕你这次只取了英语部分,也不要叫 `xxx-en`;
   哪怕只做了 ASR 用途,也不要叫 `xxx-asr` —— 范围信息写在 card 里,不写在名字里。
4. **上游有版本号且原名里没带**,才追加 `-{version}`(如 `-v2`)。
   上游标 `unversioned` / 没有版本概念的,**不加**。
   上游版本号带语义前缀的要保留(`v1` 不能写成 `1`)。
5. **raw 和 cleaned 用同一个 dataset_id**(同一个数据集的两个阶段)。

**`source/`(仅 raw 有)** —— 上游下载下来的东西**原样**放这里,保留它自己的目录结构。
比如上游是 `train/xxx.parquet`、`test/yyy.parquet`,那就是
`…/{dataset}/source/train/xxx.parquet`。

### 2.3 一个填好的例子

假设引入 HuggingFace 上的 `intronhealth/afrispeech-200`(英语、有转写、做 ASR):

```
SPEECH-DATA-TEMP/raw/dataset_repository/asr/en/afrispeech-200/
├── dataset_card.yaml
└── source/
    ├── audio/
    │   └── {口音}/{split}/*.tar.gz      ← 上游本来就是这个结构,原样保留
    └── transcripts/
        ├── train.csv
        ├── dev.csv
        └── test.csv

SPEECH-DATA-TEMP/cleaned/dataset_repository/asr/en/afrispeech-200/
├── dataset_card.yaml
├── train/
│   ├── audio/{口音}/*.flac
│   └── manifest.jsonl
├── dev/
│   ├── data/
│   └── info.json
└── test/
    ├── data/
    └── info.json
```

### 2.4 目录里不要平铺大量文件

一个目录下放几万个文件会让列举、增量同步都很慢。**按上游本来的语义结构分子目录**
(按 split、按口音、按分片、按日期都行),不要把十万个 wav 直接倒在一个目录里。

---

## 3. 第一步:选集与下载

### 3.1 最重要的一条规则:必须完整引入

**以「数据集 + 版本」为完整性边界。你必须把这个版本的全部官方内容拿下来:**

- 全部官方 **split**(train / dev(validation) / test)
- 全部官方 **subset / config**(有的数据集分 S/M/L,或按领域、口音分)
- 全部官方 **语种**(如果你引的是多语种集)

**不允许**只取一个 split、一个领域、一种口音就当成一个新数据集注册。
如果因为某种原因确实拿不全(上游文件损坏、部分内容需要额外授权),
**不要发布正式的 cleaned**,而是记录清楚缺什么、为什么,交给对接人决定。

唯一的例外:**上游自己就是重复存储的**。比如某数据集同时提供 S/M/L 三档,而 M、S 只是 L 的
前缀子集(内容完全重复),这时可以只存 L —— 但**必须先用证据证明重复关系**(比对文件校验和,
或比对样本 id 列表),并在 card 里写清楚为什么没存、怎么从 L 复现出 M/S。
**不要靠"看起来像"就跳过。**

### 3.2 下载前先做这三件事

**① 记下上游的确切身份**

- 主页 URL
- 版本号 / commit id(HuggingFace 每个仓库都有一个 `sha`,记下来)
- 许可协议(license)

**② 拿到官方的文件清单和字节数**

这是后面对账的依据。HuggingFace 可以用它的 API 列出整个仓库的文件、字节数和校验和。
拿不到清单就没法验证下载是否完整 —— 想办法拿到。

**③ 记下官方声称的样本数和时长**

通常在数据集主页、论文或 README 里。比如"5,000 小时、1,926,805 条"。
这是后面验证转换是否漏数的依据。

### 3.3 下载时的技术要点

大数据集(几十 GB 到 TB)的下载,靠"点一下等它跑完"是不行的。以下每一条都是踩过坑的:

**① 必须支持断点续传。**
下载会断,这是常态而非异常。断了要能从断点接着下,不能从头再来。
用 `curl` 就加 `-C -`;用 `wget` 就加 `-c`;自己写脚本就用 HTTP `Range` 头。
**丢掉续传参数的后果**:每断一次就从零开始,跑一整天文件反而变小了。

**② 重试判据要看"有没有进展",不是"重试了几次"。**
一个 20 GB 的文件可能需要几十次续传才能下完。如果你写"最多重试 3 次",
它会在刚下了 2% 的时候被判定失败。
**正确做法**:只要字节数还在涨就继续重试;连续若干次(比如 8 次)一个字节都没推进,才算失败。

**③ 并发要适度,先测再定。**
很多站点对**单个连接**限速。这时候单线程下载会很慢,加并发能线性提升。
但并发太高会适得其反 —— 连接频繁被掐断,反而更慢。

先做个小测试:分别用 1、4、8、16、24 个并发下同一个文件,看哪个总速度最高。
以 HuggingFace 为例,实测过 16 路附近最优,24 路反而回落。
**不要照抄这个数字**,不同站点、不同时段、不同机器都可能不一样。

⚠ 还有一点反直觉的:**掉线常常是你自己的高并发压出来的**,不是对方的固定规矩。
单连接能稳定跑很久,16 路并发时就频繁断。所以如果断得特别厉害,试试**降**并发。

**④ 大文件要在文件内部切片并行。**
如果限速是按连接算的,那么一个 20 GB 的单文件,无论你开多少个"文件级并发"都没用 ——
它只占一路。要把这个文件按字节区间切成若干段,多条连接同时拉不同段,最后拼起来。

**⑤ 下完必须校验。**
HuggingFace 的大文件(LFS 对象)都提供 sha256,下完逐个比对。
比对不上就删掉重下,**绝不要把存疑的文件传上去**。
没有校验和的,至少要比对字节数。

**⑥ 不要把 token 写在命令行参数里。**
`curl -H "Authorization: Bearer hf_xxx"` 会让 token 出现在进程列表里,同机器上的其他人能看到。
改用配置文件:把 header 写进一个权限 600 的文件,然后 `curl -K 那个文件`。

### 3.4 下载完成的检查

在进入下一步之前,确认:

- [ ] 文件数与官方清单一致(不多不少)
- [ ] **每一个**文件的字节数与官方一致(不是抽查,是全量)
- [ ] 有校验和的,校验和全部通过
- [ ] 官方的全部 split / subset / 语种都在

---

## 4. 第二步:上传到 raw

### 4.1 原则:原样

`source/` 下的内容 = 上游的内容,**一个字节都不改**:

- ✅ 保留上游的目录结构和文件名
- ✅ 压缩包保持压缩状态(`.tar.gz` / `.zip` 就那样传上去,不要解压后传)
- ✅ 连 `README.md`、`.gitattributes` 这类附带文件也一起传
- ❌ 不要转码、不要重采样、不要改文件名
- ❌ 不要"顺手"删掉你觉得没用的文件

**为什么连 README 也要留**:上游的 README 里往往有 split 定义、字段说明、引用格式、
许可条款原文 —— 这些是后面写 card 和排查问题的依据。上游哪天改版了,这就是唯一的记录。

### 4.2 上传要点

**① 幂等**:同一个文件重复上传应该是安全的。上传前先检查目标位置是否已存在且大小一致,
   一致就跳过 —— 这样中断后重跑不会浪费时间。

**② 不覆盖**:如果目标已存在但大小/内容不一致,**报错停下**,不要默默覆盖。
   这种情况说明有问题(可能上游变了,可能你下错了),需要人来判断。

**③ 上传与下载并行**:如果本地磁盘放不下整个数据集,可以边下边传边删。
   但注意**上传不要阻塞下载** —— 用独立的线程/进程做上传,否则下载会在等上传时闲置。

**④ TOS 的 S3 兼容接口有个坑**:必须用 **virtual-hosted 寻址风格**(`addressing_style: virtual`)。
   boto3 默认是 path 风格,用默认值访问会报 `InvalidPathAccess`,看起来像是**没有权限**,
   实际上只是寻址方式不对。遇到权限报错先检查这一项。

### 4.3 上传完成后:全量对账

**这一步不能省,也不能抽样。**

列出 TOS 上该目录下的所有对象,和官方清单逐个比对:

- 对象数量一致?
- **每个**对象的字节数一致?
- 有没有多出来的对象?有没有缺的?
- 总字节数一致?

写个脚本跑一遍,输出类似:

```
清单 154 个 · TOS 上 154 个
  缺失 0 · 多余 0 · 字节数不符 0
总字节: 期望 602.1 GB · 实得 602.1 GB
```

**任何一项不为 0 都要查清楚再继续。**

### 4.4 写 raw 的 dataset_card.yaml

字段写法见第 6 节。raw 的 card 填 `status: raw`。

写好后传到 `…/{dataset}/dataset_card.yaml`(注意:在 dataset 目录下,不在 `source/` 里)。

> ⚠ **card 一定要最后传 —— 它是你宣告「这份传完了」的唯一信号。**
>
> 有一个每天自动跑的检查程序在盯着你的目录。它的判据就是:
>
> - 目录里**有数据、没有 card** → 认为你还在传,不打扰、不检查;
> - 目录里**有数据、也有 card** → 认为你传完了,当天就会检查,通过就自动搬进正式库。
>
> 所以顺序是:**先传完所有数据 → 对完账(§4.3)→ 最后才传 card**。
> 提前传 card 会让程序在你还没传完时就去检查,把正常的中间状态报成有问题。
>
> 这一条对 cleaned 同样适用(第 5 节)。

---

## 5. 第三步:转换成 cleaned

> ⚠ **本节只适用于 ASR 数据集**(路径里用途那一层是 `asr` 的)。
> **TTS 数据集目前只做到 raw,不转 cleaned** —— 传完 raw、写好 card 就算交付完成。
> TTS 的 cleaned 格式另有要求,等确定后再补进本手册。

### 5.1 两种视图,按数据集类型选

cleaned 的格式取决于这份数据是**拿来训练**还是**拿来评测**:

| 数据类型                                      | 视图     | 目录内容                        |
| --------------------------------------------- | -------- | ------------------------------- |
| **训练集**(split = `train`)           | 训练视图 | `audio/` + `manifest.jsonl` |
| **评测集**(split = `dev` 或 `test`) | 评测视图 | `data/` + `info.json`       |

**一个数据集同时有 train 和 dev/test 时**,在 dataset 根目录下按 `train/`、`dev/`、`test/`
分子目录,每个目录用它对应的视图:

```
{dataset}/
├── dataset_card.yaml        ← 只有这一份 card,放在最外层
├── train/
│   ├── audio/
│   └── manifest.jsonl
├── dev/
│   ├── data/
│   └── info.json
└── test/
    ├── data/
    └── info.json
```

⚠ **子目录里不要再放 `dataset_card.yaml`**,整个数据集只有最外层那一份。

### 5.2 split 命名统一

上游可能管验证集叫 `validation` / `valid` / `val`,**我们统一叫 `dev`**。

正式的四个值:`train` / `dev` / `test` / `all`(`all` 用于 card 里表示"这个集三样都有")。

### 5.3 音频规格

**训练视图的音频**:

- 格式 **flac**(无损压缩,比 wav 省一半空间)
- 采样率 **16000 Hz**
- **单声道**(mono)
- 上游如果是多声道,按约定混成单声道;如果上游是每个说话人一个声道且这个信息有价值,
  **先问对接人**再决定怎么处理

**评测视图的音频**:内嵌在 arrow 文件里,采样率同样是 16000。

⚠ 从高采样率降到 16k 是有损的,但这是我们的统一标准。**原始的高采样率版本在 raw 里留着**,
需要时可以重新转。

### 5.4 训练视图:manifest.jsonl

这是一个**每行一个 JSON 对象**的纯文本文件(不要压缩成 .gz —— 相对音频体积可以忽略,
换来的是可以直接 `grep` / `head` / 流式读)。

**字段表**:

| 字段               | 必填     | 说明                                                                                    |
| ------------------ | -------- | --------------------------------------------------------------------------------------- |
| `audio_filepath` | ✓       | 音频路径。**相对于 manifest 文件本身**,如 `audio/xxx.flac`                      |
| `text`           | ✓       | 参考转写。**保持原始,不要做归一化**(不要转小写、不要去标点)——归一化由下游按需做 |
| `duration`       | ✓       | 这条样本的时长,**秒**(浮点)                                                       |
| `pipeline_id`    | ✓       | **录音级唯一标识**,格式恒为 `{dataset_id}/{音频文件名去扩展名}`。见下方说明     |
| `offset`         | 段级才有 | 这一段在音频文件里的起始时刻,秒。整条音频作为一条样本时**省略这个字段**           |
| `rec_duration`   | 段级才有 | 整个音频**文件**的时长,秒(注意和 `duration` 的区别)                             |
| `lang`           | 可选     | 语种码,多语种集要带                                                                     |
| `speaker`        | 可选     | 说话人 id,有就带上                                                                      |

**关于 `pipeline_id` —— 这一列最容易漏,但很重要**:

外部数据集的清单通常只有前几个字段,没有这一列,**你必须自己补上**。

它的作用是标识"这条样本来自哪条录音"。因为一条长录音可能被切成很多段(manifest 里就是很多行),
下游需要知道哪些行属于同一条录音:

- **切分训练/测试集时必须按它分组**。否则同一条录音的相邻两段会被分到训练集和测试集两边,
  这是**数据泄漏** —— 同一个说话人、同一个场景、甚至上下句,评测分数会虚高,而且不容易发现。
- 统计"有多少条录音"、"每条录音平均切了几段"也靠它。

**格式硬约定**:`{dataset_id}/{recording_stem}`,两段,中间一个 `/`。
`dataset_id` 必须等于 card 里的 `dataset.id`(也就是目录名),这样多个数据集的 manifest
合并时 id 不会撞车。

⚠ 从上游 id 反解文件名时小心分隔符 —— 比如 YouTube 的 video id 本身就可能含 `-`
(像 `---vAa2xk0c`),按第一个分隔符切会切错。

**补完要自检**:唯一 `pipeline_id` 的数量 == 音频文件数量,且每个 `pipeline_id` 的后半段
与对应的 `audio_filepath` 文件名一致。

**数值精度**:`offset` 和 `duration` 最多保留 **3 位小数**(毫秒级)。再多是假精度
(16k 采样率下 1 毫秒 = 16 个采样点,已经远细于任何切分边界的误差)。

**可以加自定义字段**。上游有说话人、口音、年龄、领域这些信息,尽量都带上 ——
但每个自定义字段都要在 card 的 `manifest_schema` 段里说明它是什么意思。

**样例**(整条 utterance,无 offset):

```json
{"audio_filepath": "audio/afemai/afrispeech-200-train-671409.flac", "text": "The patient had massive Cocaine induced rhabdomyolysis .", "duration": 6.346, "pipeline_id": "afrispeech-200/afrispeech-200-train-671409", "lang": "en", "speaker": "cd0561f6033b217db408c03d76dc452a", "accent": "afemai", "domain": "medical"}
```

**样例**(段级,从长录音切出来的):

```json
{"audio_filepath": "audio/meeting_001.flac", "offset": 2697.76, "duration": 5.03, "rec_duration": 3612.5, "text": "…", "pipeline_id": "my-dataset/meeting_001"}
```

### 5.5 评测视图:data/ + info.json

评测集用 HuggingFace `datasets` 库的磁盘格式,好处是评测程序 `load_from_disk` 一行就能加载,
音频内嵌不用管路径。

**做法**:构造一个 `datasets.Dataset`,音频列用 `Audio(sampling_rate=16000)`,
然后 `save_to_disk("data/")`。

**`info.json`** 是给评测程序看的元信息,和 `data/` 平级(注意别和 `data/` 里面
`save_to_disk` 自动生成的 `dataset_info.json` 搞混,那是库自己的文件):

```json
{
  "lang": "en",
  "loader": "disk",
  "split": "test",
  "num_samples": 6318,
  "total_duration_hours": 18.604585,
  "text_key": "text",
  "task_type": "short",
  "origin": "hf:intronhealth/afrispeech-200@b538c6e111914a812af28ff677f8cffc9b404b7d",
  "note": "CC-BY-NC-SA-4.0; non-commercial ASR evaluation only.",
  "split_note": "AfriSpeech-200 official test split; clinical domain normalized to medical."
}
```

字段说明:

| 字段                     | 说明                                                              |
| ------------------------ | ----------------------------------------------------------------- |
| `lang`                 | 语种码                                                            |
| `loader`               | 固定填`disk`                                                    |
| `split`                | `dev` 或 `test`                                               |
| `num_samples`          | 样本条数                                                          |
| `total_duration_hours` | 总时长,小时                                                       |
| `text_key`             | 参考转写在哪一列(通常是`text`)                                  |
| `task_type`            | `short` = 短音频逐条识别;`long` = 长音频(需要切分/长文本识别) |
| `origin`               | 上游身份,带版本/commit                                            |
| `note`                 | 使用注意事项,尤其是许可限制                                       |
| `split_note`           | 这个 split 的来历,以及你做过什么改动                              |

### 5.6 保留有语义的目录结构,丢掉没语义的

上游目录结构里,**有含义的保留**,**纯粹是临时组织的丢掉**:

| 上游目录                               | 怎么处理                                        |
| -------------------------------------- | ----------------------------------------------- |
| 按语种分的`en_us/`、`sw_ke/`       | **保留** —— 这是数据的语义              |
| 按口音分的`{accent}/`                | **保留**                                  |
| 按 split 分的`train/`、`test/`     | **保留**(而且是我们要求的一级结构)        |
| 泛泛的`audio/`、`text/`、`data/` | **丢掉** —— 只是上游的组织方式,没有语义 |
| `part_001/`、`shard_00/` 这类分片  | **丢掉**,除非分片本身有含义               |

### 5.7 转换完成后:逐 split 对账

**和 raw 逐 split 比对样本数和时长。**

```
              官方/raw          cleaned         差异
train         57,819 条         57,819 条        0
dev            3,227 条          3,227 条        0
test           6,319 条          6,318 条       -1   ← 必须解释清楚
```

**有差异就必须解释,并写进 card 的 `lineage.derivation[].filters`。**
比如上面那个 -1,真实原因是"官方 test 清单里有一行引用的音频文件在所有压缩包里都不存在",
这就要如实记下来。

**绝对不允许**:发现对不上,就把数字改成实际值蒙混过去。少数据是可以的(只要说清楚),
**瞒着少数据不行**。

---

## 6. dataset_card.yaml 怎么写

每个 dataset 目录里放一份,文件名固定就叫 `dataset_card.yaml`。

### 6.1 最小必填字段

这些字段**一个都不能少**(没有值就填 `null` / `[]` / `{}`):

```yaml
schema_version: speech_dataset_spec_v1.2

dataset:
  id: ...
  name: ...
  version: ...
  split: ...

status: ...

source:
  owner_type: ...
  source_platform: ...

lineage:
  upstream: []
  derivation: []
  manifests:
    samples: ...

data:
  num_samples: ...
  total_duration_hours: ...
  languages: []

labels:
  fixed: {}
  distribution: {}
  unknown: []
  availability: {}

usage:
  intended_use: []
  not_recommended_for: []

homepage: ...              # 上游数据集主页 URL,必填

generation:
  generated_at: ...        # ISO 8601 时间戳,必填
```

### 6.2 字段逐个说明

#### `dataset`

| 字段            | 说明                                                                                                  |
| --------------- | ----------------------------------------------------------------------------------------------------- |
| `id`          | 数据集标识,**等于目录名**。规则见 §2.2                                                         |
| `name`        | 人类可读的名字,用上游的正式名称(如`AfriSpeech-200`)                                                 |
| `version`     | 上游版本。没有版本概念的填`unversioned`;有的保留原样(`v1` 不写成 `1`)                           |
| `split`       | 这个 card 覆盖的数据切分:`train` / `dev` / `test` / `all`。三样都有就填 `all`               |
| `description` | 一两句话说清楚这是什么数据                                                                            |
| `subsets`     | *(可选)* 数据集由多个**互斥的物理分区**组成时列出。**只允许一级**,子集目录不得再出 card |

⚠ `subsets` 的前提是**互斥**。如果上游的几个 config 内容互相重叠(比如 S ⊂ M ⊂ L),
那不是 subsets,不要这么写 —— 在 `data.statistics` 里说明关系即可。

#### `status`

| 值          | 用在哪                |
| ----------- | --------------------- |
| `raw`     | raw 目录下的 card     |
| `cleaned` | cleaned 目录下的 card |

#### `source`

开源数据集这两个字段是固定的,照抄即可:

| 字段                | 填什么                           |
| ------------------- | -------------------------------- |
| `owner_type`      | **`public`**             |
| `source_platform` | **`dataset_repository`** |

#### `lineage`(溯源)

**raw 的 card**:`upstream` 写上游身份,`derivation` 基本是空的(你没改任何东西)。

```yaml
lineage:
  upstream: []                    # raw 就是源头,没有更上游的内部数据集
  derivation:
    - step: archive_hf_snapshot
      description: "完整归档 xxx/yyy 的全部内容,保留仓库原始目录与文件;未做任何转换。"
      date: "2026-08-25"
      recipe_uri: "hf:xxx/yyy"
  manifests:
    samples: null                 # raw 阶段没有样本级清单
```

**cleaned 的 card**:`upstream` 指回 raw,`derivation` 逐步记录你做了什么。

```yaml
lineage:
  upstream:
    - id: afrispeech-200                     # raw 的 dataset_id
      type: raw_corpus
      version: unversioned
      uri: tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/asr/en/afrispeech-200/
      homepage: https://huggingface.co/datasets/intronhealth/afrispeech-200
      revision: b538c6e111914a812af28ff677f8cffc9b404b7d
  derivation:
    - step: standardize_audio
      description: "解出官方 WAV,转成无损 flac 16kHz 单声道;保持官方 train/dev/test 划分。"
    - step: rename_split
      description: "官方 validation 按规范改名为 dev。"
    - step: filter_missing_audio
      description: "剔除官方 test 清单中 1 行,其引用的 WAV 在所有上游压缩包里都不存在。"
      filters:
        - split: test
          key: "d75a44c8-.../f5908541823bbca8a171"
  manifests:
    samples: "train/manifest.jsonl"
```

⚠ **每一个让数量对不上的操作,都要在 `derivation` 里有一条对应记录。**

#### `data`

```yaml
data:
  num_samples: 67365                  # 总样本条数
  total_duration_hours: 200.286       # 总时长,小时
  languages:
    - language: en                    # ISO 639-1
      locale: en                      # BCP-47,地区未知时和 language 一样
      duration_hours: 200.286
  format:                             # 可选,但建议写
    storage: flac_16k_mono
    layout: "train/audio/{accent}/*.flac + train/manifest.jsonl"
    text_column: text
  statistics:                         # 可选,想记什么记什么
    splits:
      train: {num_samples: 57819, total_duration_hours: 172.880}
      dev:   {num_samples: 3227,  total_duration_hours: 8.722}
      test:  {num_samples: 6318,  total_duration_hours: 18.684}
```

`data.statistics` 是自由发挥的地方 —— 分 split 的数量、时长分布、按口音/领域的拆分,
你觉得有用就写。**没有固定字段集**。

#### `labels`(标签)

这是描述"这批语音是什么样的"。分四块:

| 块               | 含义                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------- |
| `fixed`        | **整个数据集都一样**的标签。比如全是电话录音 → `scenario.channel: telephone` |
| `distribution` | **数据集内部有差异**的标签。值可以是空 `{}`、计数或比例                       |
| `unknown`      | **不知道**的标签,列字段名                                                       |
| `availability` | 有哪些类型的标注可用                                                                  |

**硬规则:同一个标签路径只能出现在这三块中的一块**,不能重复。

```yaml
labels:
  fixed:
    interaction.type: monologue
    speaking_style: read
    domain: medical
  distribution:
    acoustic: {}                    # 知道有差异但没统计 → 空字典
    accent:                         # 有统计就写
      afemai: 1203
      yoruba: 8891
  unknown:
    - speaker.age_group
    - device
  availability:
    transcript: true
    timestamps: false
    speaker_labels: true
    diarization: false
    pronunciation: false
    phoneme: false
    prosody: false
```

**填不出来就放 `unknown`,不要瞎猜。** 标错比不标更糟 —— 下游会当真。

各个标签的可选值见 §7 附录。

#### `usage`(用途)

```yaml
usage:
  intended_use:
    - asr_training
    - asr_evaluation
  not_recommended_for:
    - tts_training
    - voice_cloning
```

可选值:`asr_training` / `asr_evaluation` / `tts_training` / `tts_evaluation` /
`voice_cloning` / `speech_llm_training`

⚠ ASR 和 TTS 必须分清楚。ASR 数据往往录音质量参差,拿去做 TTS 或声音克隆会出问题。
录音条件不干净、说话人不明确的,把 `tts_training` 和 `voice_cloning` 写进 `not_recommended_for`。

#### `homepage`(必填)

上游数据集的**正式主页 URL**:

```yaml
homepage: https://huggingface.co/datasets/intronhealth/afrispeech-200
```

**这一项必须写。** 它是别人回头查证这份数据的唯一入口 —— 想知道原始的 split 怎么划分的、
字段什么含义、有没有更新版本,都要靠它找回上游。

填**数据集自己的主页**,不要填论文链接、不要填某个文件的下载直链、也不要填搜索结果页。
上游有多个入口时(比如既有官网又有 HuggingFace 镜像),填**你实际下载的那个**。

#### `license`

```yaml
license: CC-BY-NC-SA-4.0
license_uri: https://huggingface.co/datasets/intronhealth/afrispeech-200
```

`license` 填协议名称,`license_uri` 填能看到协议原文的地址。

#### `manifest_schema`(有 manifest 时)

把你的 `manifest.jsonl` 每一列都说明一遍,这样别人不用翻代码就知道每列是什么:

```yaml
manifest_schema:
  - {field: audio_filepath, required: true,  description: "音频路径,相对 manifest 文件"}
  - {field: text,           required: true,  description: "参考转写,原始未归一化"}
  - {field: duration,       required: true,  description: "样本时长(秒)"}
  - {field: pipeline_id,    required: true,  description: "录音级唯一标识 {dataset_id}/{stem}"}
  - {field: accent,         required: false, description: "自定义列:上游标注的口音"}
```

#### `generation`(出货快照)

```yaml
generation:
  generated_at: "2026-08-25T14:30:00+08:00"   # 必填
  entrypoint: "python convert_afrispeech.py"
  args: "--src ... --dst ..."
  validation: "478 个文件、61,921,048,011 字节与上游一致;245 个 tar.gz 全部通过 gzip -t"
```

`generated_at` 是**必填**的 —— 它是整份 card 里唯一的时间戳,下游据它判断这份数据什么时候产出/更新。
用 ISO 8601 格式带时区。
`validation` 那一行建议写清楚你做了哪些校验、结果如何。

### 6.3 完整示例:raw 的 card

```yaml
schema_version: speech_dataset_spec_v1.2

dataset:
  id: afrispeech-200
  name: AfriSpeech-200
  version: unversioned
  split: all
  description: >-
    AfriSpeech-200 官方 Hugging Face 仓库固定 revision 的完整原始快照,
    包含 train/dev/test 转写 CSV 及按口音和 split 分片的原始音频 tar.gz。

status: raw

source:
  owner_type: public
  source_platform: dataset_repository

lineage:
  upstream: []
  derivation:
    - step: archive_hf_snapshot
      description: >-
        完整归档 intronhealth/afrispeech-200 固定 revision,保留仓库原始目录和文件内容;
        未做任何转换或重编码。
      date: "2026-08-25"
      recipe_uri: "hf:intronhealth/afrispeech-200"
  manifests:
    samples: null

data:
  num_samples: 67365
  total_duration_hours: 200.286
  languages:
    - language: en
      locale: en
      duration_hours: 200.286
  statistics:
    archived_files: 478
    archived_size_bytes: 61921048011
    splits:
      train: {num_samples: 57819, total_duration_hours: 172.880}
      dev:   {num_samples: 3227,  total_duration_hours: 8.722}
      test:  {num_samples: 6319,  total_duration_hours: 18.684}

labels:
  fixed:
    interaction.type: monologue
    speaking_style: read
  distribution:
    domain:
      medical: 41647
      general: 25718
    accent: {}
  unknown:
    - device
    - scenario.channel
    - scenario.environment
  availability:
    transcript: true
    timestamps: false
    speaker_labels: true
    diarization: false
    pronunciation: false
    phoneme: false
    prosody: false

usage:
  intended_use:
    - asr_training
    - asr_evaluation
  not_recommended_for:
    - tts_training
    - voice_cloning

license: CC-BY-NC-SA-4.0
license_uri: https://huggingface.co/datasets/intronhealth/afrispeech-200
homepage: https://huggingface.co/datasets/intronhealth/afrispeech-200

provenance:
  repo: intronhealth/afrispeech-200
  revision: b538c6e111914a812af28ff677f8cffc9b404b7d
  archive_uri: tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/asr/en/afrispeech-200/source/

generation:
  generated_at: "2026-08-25T14:30:00+08:00"
  entrypoint: "hf download + 上传脚本"
  args: "intronhealth/afrispeech-200@b538c6e111914a812af28ff677f8cffc9b404b7d"
  validation: "478 个文件、61,921,048,011 字节逐个与上游比对一致;245 个 tar.gz 全部通过 gzip -t"
```

### 6.4 完整示例:cleaned 的 card

```yaml
schema_version: speech_dataset_spec_v1.2

dataset:
  id: afrispeech-200
  name: AfriSpeech-200
  version: unversioned
  split: all
  description: AfriSpeech-200 完整 cleaned 版本,含 train/dev/test。
  subsets_note: "train/dev/test 是物理切分;general/medical 是领域逻辑切片,不是 subset。"
  subsets:
    - {id: train, split: train, num_samples: 57819}
    - {id: dev,   split: dev,   num_samples: 3227}
    - {id: test,  split: test,  num_samples: 6318}

status: cleaned

source:
  owner_type: public
  source_platform: dataset_repository

lineage:
  upstream:
    - id: afrispeech-200
      type: raw_corpus
      version: unversioned
      uri: tos://vox-asr-audio-data/SPEECH-DATA-TEMP/raw/dataset_repository/asr/en/afrispeech-200/
      homepage: https://huggingface.co/datasets/intronhealth/afrispeech-200
      revision: b538c6e111914a812af28ff677f8cffc9b404b7d
  derivation:
    - step: standardize_audio
      description: "解出官方 WAV,转为无损 flac 16 kHz 单声道;保持官方 train/dev/test 划分。"
    - step: normalize_labels
      description: "上游 domain 值 clinical 归一为 medical;口音、说话人、国家、性别、年龄段保留为样本字段。"
    - step: filter_missing_audio
      description: "剔除官方 test 清单中 1 行 —— 其引用的 WAV 在所有上游压缩包里都不存在。"
      filters:
        - split: test
          accent: ibibio
          key: "d75a44c8-fa78-4fdf-8ad0-2ad9808dee30/f5908541823bbca8a171"
  manifests:
    samples: "train/manifest.jsonl"

data:
  num_samples: 67364
  total_duration_hours: 200.206
  languages:
    - language: en
      locale: en
      duration_hours: 200.206
  format:
    storage: flac_16k_mono
    layout: "train/audio/{accent}/*.flac + train/manifest.jsonl;dev,test 为 HF arrow"
    text_column: text
  statistics:
    splits:
      train: {num_samples: 57819, total_duration_hours: 172.880}
      dev:   {num_samples: 3227,  total_duration_hours: 8.722}
      test:  {num_samples: 6318,  total_duration_hours: 18.604}
    reconciliation: "与 raw 逐 split 对账:train/dev 完全一致,test 少 1 条(见 derivation.filter_missing_audio)"

labels:
  fixed:
    interaction.type: monologue
    speaking_style: read
  distribution:
    domain:
      medical: 41647
      general: 25717
    accent: {}
  unknown:
    - device
    - scenario.channel
    - scenario.environment
  availability:
    transcript: true
    timestamps: false
    speaker_labels: true
    diarization: false
    pronunciation: false
    phoneme: false
    prosody: false

usage:
  intended_use:
    - asr_training
    - asr_evaluation
  not_recommended_for:
    - tts_training
    - voice_cloning

license: CC-BY-NC-SA-4.0
license_uri: https://huggingface.co/datasets/intronhealth/afrispeech-200
homepage: https://huggingface.co/datasets/intronhealth/afrispeech-200

manifest_schema:
  - {field: audio_filepath, required: true,  description: "音频路径,相对 manifest 文件"}
  - {field: text,           required: true,  description: "参考转写,原始未归一化"}
  - {field: duration,       required: true,  description: "样本时长(秒)"}
  - {field: pipeline_id,    required: true,  description: "录音级唯一标识 {dataset_id}/{stem}"}
  - {field: lang,           required: false, description: "语种码"}
  - {field: speaker,        required: false, description: "上游说话人 id"}
  - {field: accent,         required: false, description: "自定义列:上游标注的口音"}
  - {field: domain,         required: false, description: "自定义列:medical / general"}

generation:
  generated_at: "2026-08-25T18:00:00+08:00"
  entrypoint: "python convert_afrispeech.py"
  args: "--src tos://…/raw/…/afrispeech-200/source/ --dst tos://…/cleaned/…/afrispeech-200/"
  validation: "逐 split 与 raw 对账:train 57819=57819,dev 3227=3227,test 6318=6319-1(已记录);flac 全部可解码"
```

---

## 7. 附录:标签枚举表

填 `labels` 时从这些表里取值。**每个枚举都隐含 `unknown`(不知道)和 `other`(知道但不在表里)
两个值**,表里不再重复列出。

### 7.1 language(语言)

| 字段                    | 基数 | 说明                                                                      |
| ----------------------- | ---- | ------------------------------------------------------------------------- |
| `language`            | 单值 | 主语言,ISO 639-1,如`en`                                                 |
| `locale`              | 单值 | BCP-47,如`en-US` / `en-IN` / `zh-CN`;地区未知时和 `language` 一样 |
| `code_switching`      | 单值 | 见下表                                                                    |
| `secondary_languages` | 多值 | 混入的其他语言,如`[hi, zh]`                                             |

**code_switching**:`none`(无混语)/ `intra_sentential`(句内混)/ `inter_sentential`(句间混)/
`named_entity_only`(只在人名地名品牌上混)/ `heavy_code_switching`(重度混语)

⚠ 口音统一用 `locale` 表达,**没有独立的 accent 枚举**。

### 7.2 speaker(说话人)

| 字段                       | 说明                               |
| -------------------------- | ---------------------------------- |
| `speaker_id`             | 说话人唯一标识                     |
| `voice_id`               | 音色标识(TTS 用)                   |
| `perceived_voice_gender` | 听感性别                           |
| `age_group`              | 听感年龄段                         |
| `speaker_consistency`    | 同一个 speaker_id 下是不是同一个人 |

**perceived_voice_gender**:`male` / `female` / `neutral`(中性)/ `mixed`(多人含多种)

⚠ 只描述**听感**,不承载年龄。童声用 `age_group: child` 表达。

**age_group**:`child`(12 岁以下)/ `teen`(13–17)/ `young_adult`(18–30)/
`adult`(31–60)/ `senior`(60 以上)/ `mixed`(多人含多个年龄段)

**speaker_consistency**:`high`(基本确认同一人,适合 TTS)/ `medium`(大部分是同一人)/
`low`(可能混入多人)/ `mixed`(明确含多人)

### 7.3 interaction(交互结构)

**type**(单值,必填):

| 值              | 含义                                        |
| --------------- | ------------------------------------------- |
| `monologue`   | 独白 —— 一个人连续说,没有实时互动         |
| `dialogue`    | 双人对话                                    |
| `multi_party` | 多人交互 —— 三人及以上                    |
| `command`     | 命令式 —— 对设备/系统发指令、查询、唤醒词 |
| `non_speech`  | 非语音 —— 没有有效人声                    |

**dialogue_role**(单值,可选,只有对话类 TTS/配音才需要):
`narrator`(旁白)/ `host`(主持人)/ `guest`(嘉宾)/ `customer`(客户)/ `agent`(客服)/
`teacher`(老师)/ `student`(学生)/ `character`(角色)/ `assistant`(助手)

### 7.4 speaking_style(表达方式)

单值。**只描述准备程度这一条轴**,不涉及正式度、情绪、音量。

| 值              | 含义                                                              |
| --------------- | ----------------------------------------------------------------- |
| `read`        | 朗读 —— 逐字或基本逐字读已有文本(有声书、朗读语料、照稿播报)    |
| `prepared`    | 有准备表达 —— 内容提前组织过,但现场自然说(演讲、脱稿播报、汇报) |
| `spontaneous` | 即兴表达 —— 没有明显准备,边想边说(聊天、会议讨论)               |

边界例子:单人播客 = `monologue` + `spontaneous` + `content_genre: podcast`;
新闻播报 = `read` 或 `prepared`,取决于是不是明显照稿。

### 7.5 scenario(使用场景)

四个正交子维度,全部单值、全部可选。

**environment(物理环境)**:
`home`(家庭)/ `office`(办公室)/ `meeting_room`(会议室)/ `classroom`(教室)/
`vehicle`(车内)/ `outdoor`(户外)/ `restaurant`(餐厅)/ `shopping_mall`(商场)/
`airport`(机场)/ `hospital`(医院)/ `factory`(工厂)/ `studio`(录音棚)/
`dubbing_studio`(配音棚)

**channel(采集或传播通道)**:
`direct_recording`(麦克风直采)/ `telephone`(电话链路)/ `voip`(网络电话)/
`meeting_platform`(Zoom/Teams/Meet)/ `broadcast`(广播电视)/ `media_platform`(播客视频平台)/
`live_streaming`(直播)/ `product_log`(产品日志)

**product_context(产品上下文)**:
`voice_assistant`(语音助手,含语音搜索、智能家居、导航)/ `dictation`(语音输入)/
`call_center`(呼叫中心)/ `education_app`(教育应用)

> `dictation` 的判据:用户说话的**目的就是生成文字**(输入法、病历口述、邮件口述)。
> 单人播客、Vlog、讲课虽然也是一个人说,但目的不是生成文本,**不算** dictation。

**content_genre(内容形态)**:
`meeting`(会议)/ `lecture`(课程讲座)/ `news`(新闻)/ `podcast`(播客)/
`audiobook`(有声书)/ `interview`(采访面试)/ `vlog`(视频博客)/ `entertainment`(娱乐)/
`dubbing`(配音)/ `social_chat`(社交闲聊)

### 7.6 acoustic(声学条件)

**多值** —— 可以同时选多个。

| 值                 | 含义     | 值                | 含义                     |
| ------------------ | -------- | ----------------- | ------------------------ |
| `clean`          | 干净     | `far_field`     | 远场(>1 米)              |
| `mild_noise`     | 轻微噪声 | `near_field`    | 近场(<30 厘米)           |
| `heavy_noise`    | 强噪声   | `overlap`       | 重叠语音(两人以上同时说) |
| `music`          | 音乐背景 | `reverberation` | 混响                     |
| `traffic_noise`  | 交通噪声 | `clipping`      | 爆音截幅                 |
| `crowd_noise`    | 人群噪声 | `distortion`    | 失真                     |
| `wind_noise`     | 风噪     | `packet_loss`   | 丢包                     |
| `keyboard_noise` | 键盘噪声 | `narrowband`    | 窄带(电话)               |
| `machine_noise`  | 机器噪声 | `breath_noise`  | 呼吸噪声                 |
| `room_tone`      | 房间底噪 | `mouth_click`   | 口水音                   |

### 7.7 device(采集设备)

单值。**只描述物理麦克风**;传输链路(电话/VoIP)归 `scenario.channel`。

| 值                   | 含义                                                |
| -------------------- | --------------------------------------------------- |
| `professional_mic` | 专业设备 —— 棚麦、广播设备、领夹麦、枪麦、录音笔  |
| `builtin_mic`      | 消费设备内置麦 —— 手机、平板、笔记本、摄像头      |
| `headset_mic`      | 耳机麦 —— 有线耳机、头戴麦、蓝牙耳机              |
| `farfield_mic`     | 远场设备麦 —— 智能音箱、车载、会议室全向麦/阵列麦 |

### 7.8 domain(内容领域)

单值,跨领域时取主要的。**只收主题类值** —— 媒体形态归 `content_genre`,产品场景归 `product_context`。

`general`(通用)/ `technology`(科技IT)/ `medical`(医疗)/ `finance`(金融)/ `legal`(法律)/
`education`(教育)/ `politics`(政治)/ `government`(政务)/ `sports`(体育)/
`entertainment`(娱乐)/ `retail`(零售)/ `travel`(旅游)/ `automotive`(汽车)/
`real_estate`(房地产)/ `agriculture`(农业)/ `science`(科学)/ `religion`(宗教)/
`emergency`(应急)

### 7.9 availability(标注可用性)

布尔值,填 `true` / `false`:

| 字段               | 含义                             |
| ------------------ | -------------------------------- |
| `transcript`     | 有转写文本                       |
| `timestamps`     | 有时间戳(词级或段级)             |
| `speaker_labels` | 有说话人标注                     |
| `diarization`    | 有说话人分离标注(谁在什么时候说) |
| `pronunciation`  | 有发音标注                       |
| `phoneme`        | 有音素标注                       |
| `prosody`        | 有韵律标注                       |

### 7.10 一个填标签的完整例子

"印度口音的工程师在 Zoom 会议里用笔记本内置麦讨论 AI 项目":

```yaml
language: en
locale: en-IN
interaction:
  type: multi_party
speaking_style: spontaneous
scenario:
  environment: office
  channel: meeting_platform
  content_genre: meeting
acoustic:
  - far_field
  - reverberation
  - overlap
device: builtin_mic
domain: technology
```

注意**一个事实只存一处**:"Zoom" 只出现在 `channel` 里,不要在 `device` 或 `environment`
里重复表达。

---

## 8. 交付前自检清单

每引入一个数据集,把这张表逐项打勾。**有任何一项打不了勾,就不算完成。**

### raw

- [ ] 目录路径正确:`SPEECH-DATA-TEMP/raw/dataset_repository/{asr|tts}/{lang}/{dataset}/`
- [ ] 上游内容在 `source/` 下,保留原始目录结构
- [ ] 官方全部 split / subset / 语种都在(或者已书面说明为什么不全)
- [ ] **逐个文件**比对字节数:缺失 0 / 多余 0 / 不符 0
- [ ] 有校验和的文件全部校验通过
- [ ] `dataset_card.yaml` 在 dataset 目录下(不在 `source/` 里)
- [ ] card 的 `status: raw`,最小必填字段齐全
- [ ] card 的 `homepage` 已填,且是数据集主页(不是论文链接或下载直链)
- [ ] card 的 `data.total_duration_hours` 已填(分 split 的时长也写上)
- [ ] card 的 `generation.generated_at` 已填

### cleaned(只有 ASR 数据集需要;TTS 数据集到 raw 为止)

- [ ] 目录路径正确:`SPEECH-DATA-TEMP/cleaned/dataset_repository/asr/{lang}/{dataset}/`
- [ ] split 名用的是 `train` / `dev` / `test`(上游的 `validation` 已改名并记录在 card 里)
- [ ] 训练集:`audio/` 是 flac 16k 单声道,`manifest.jsonl` 是明文
- [ ] 评测集:`data/` 可以 `load_from_disk` 加载,`info.json` 字段齐全
- [ ] manifest 每行都有 `audio_filepath` / `text` / `duration` / `pipeline_id`
- [ ] **唯一 `pipeline_id` 数量 == 音频文件数量**
- [ ] `pipeline_id` 的后半段与 `audio_filepath` 的文件名一致
- [ ] `manifest.jsonl` 里引用的每个音频文件都真实存在
- [ ] 每个音频文件都能正常解码(抽样解不够,大集至少抽 1% 且覆盖每个子目录)
- [ ] **逐 split 与 raw 对账**:样本数、时长都对得上,对不上的每一条都在 card 的
  `derivation[].filters` 里有记录
- [ ] 整个数据集只有**一份** `dataset_card.yaml`,在最外层
- [ ] card 的 `status: cleaned`,`lineage.upstream` 指回 raw 的 URI
- [ ] card 的 `homepage` 已填
- [ ] card 的 `manifest_schema` 把 manifest 每一列都说明了

### 交付说明里要写的

- 数据集名称、上游 URL、版本/commit
- 最终的两个 TOS 路径
- 样本数、时长(分 split)
- **许可协议,以及任何使用限制**
- 你做过的所有改动(改名、剔除、格式转换)
- **任何对不上的数字,以及原因**
- 遇到但没解决的问题

---

## 9. 常见坑

这些都是实际踩过的,按"容易造成静默损失"排序:

### 9.1 静默少数据(最危险)

**症状**:不报错,数据就是少了一部分,几个月后才发现。

- **下载丢了断点续传参数** → 每次断线都从头开始,跑一天文件反而变小
- **重试次数写死** → 大文件刚下 2% 就被判失败,而失败是"跳过这个文件"而不是报错停下
- **按目录列举来统计** → 有些数据在压缩包里、有些在别的目录,列举漏了
- **上游清单和实际文件不一致** → 清单有 39,341 行,实际只有 39,340 个音频

**对策**:凡是"数量"都要有**两个独立来源**互相对账 —— 官方声称的 vs 你实际拿到的。
只信一个来源,错了也不知道。

### 9.2 抽样验证骗过了你

**只看前几条**很容易得出完全错误的结论。曾经有一次抽查文件头部,以为某数据集 95% 是测试集数据,
全量扫完实际只有 0.7%。

**对策**:验证数量、验证完整性时**跑全量**。只有在验证"能不能解码"这类昂贵操作时才抽样,
而且抽样要覆盖每个子目录。

### 9.3 测试集污染

很多开源数据集的"训练集"里混着别的数据集的测试集,或者你引入的数据集的测试集已经在
公司别的训练数据里了。**这会让评测分数虚高**,而且极难发现。

**对策**:引入带 test split 的数据集时,在交付说明里**明确写出 test 的样本数和标识方式**,
让下游能查重。发现疑似重叠,报告给对接人。

### 9.4 上游自己就有重复

有的数据集把同一份数据在多个 config 下各存一份(比如 `L` config 里已经含 validation,
而 `dev` config 又是同一份)。原样归档没错,但要**在 card 里说明**,
否则下游会以为是两份不同的数据,统计时重复计算。

**对策**:比对文件校验和。相同 = 重复,写进 card。

### 9.5 权限报错其实是配置错

TOS 的 S3 接口用 boto3 默认的 path 寻址风格会报 `InvalidPathAccess`,看起来像没权限。
改成 `addressing_style: virtual` 就好了。**遇到权限报错先怀疑寻址方式。**

### 9.6 token 泄漏

`curl -H "Authorization: Bearer xxx"` 会把 token 暴露在进程列表里,共享机器上其他人能看到。
改用 `curl -K 配置文件`(文件权限设 600)。

### 9.7 共享机器上的误伤

**绝对不要**用 `pkill -f 某个模式` 或 `docker rm -f $(docker ps -aq --filter name=...)`
这类**广匹配**的删除命令 —— 会误杀同机器上别人的进程和容器。
只操作你自己**确切知道 ID/PID** 的东西。

### 9.8 磁盘写满

几百 GB 的数据集,下载中转会吃掉大量磁盘。**开始前先看剩余空间**,
边下边传边删,不要等下完再传。

---

## 10. 有疑问时

以下情况**停下来问,不要自行决定**:

- 许可协议限制转存 / 再分发,或者读不懂条款
- 数据集需要审批而迟迟批不下来
- 官方数字和实际拿到的对不上,而你找不到原因
- 上游有多个版本 / 多个镜像,不知道该用哪个
- 数据集很大(比如超过 1 TB),不确定值不值得引入
- 发现疑似测试集污染
- 本手册没有覆盖的情况

**报告问题时请带上**:数据集名称和 URL、你执行的命令、完整的报错信息、
你已经确认的事实(数字要具体)。
