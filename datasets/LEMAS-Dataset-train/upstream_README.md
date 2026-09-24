---
license: cc-by-4.0
language:
- it
- pt
- es
- fr
- de
- vi
- id
- ru
- en
- zh
task_categories:
- text-to-speech
- automatic-speech-recognition
size_categories:
- n>1T
---

## Overview

This dataset is part of **LEMAS-Project** ([lemas-project.github.io/LEMAS-Project](https://lemas-project.github.io/LEMAS-Project/)).
It contains a large-scale training set (150k+ hours) and a curated evaluation set
(500 utterances per language) covering 10 languages, all with word-level alignment.


## Fields
- `key`: unique utterance identifier; the first two characters indicate the language ID
- `audio`: relative path to the MP3 audio file (in the eval set, this key is renamed to "file_name" for compatibility with the viewer)
- `dur`: audio duration in seconds
- `txt`: original transcription
- `align`: alignment information, including:
  - `align.txt`: normalized text used for alignment
  - `align.words`: list of word-level timestamps and confidence scores



## Methods

### Train Set

- The training set is constructed by filtering large-scale aligned audio–text pairs with language- and dataset-specific constraints.
- URL: [https://huggingface.co/datasets/LEMAS-Project/LEMAS-Dataset-train](https://huggingface.co/datasets/LEMAS-Project/LEMAS-Dataset-train)

**Filtering rules:**
- Only samples with successfully extracted word-level alignments are kept; failed alignments are skipped.
- The average alignment confidence score is greater than a threshold **x**, where **x ∈ [0.2, 0.5]** depending on the source dataset.
- Audio duration is between **0.5 and 30 seconds**, and the maximum pause between consecutive words does not exceed **4 seconds**.
- Text is restricted to supported languages; samples containing characters outside the supported set (10 languages) are removed.
- The text-to-duration ratio `len(txt) / dur` falls within a language-specific range.


#### **Statistics**
| lang | utterances | total_dur(h) | avg_dur(s) | total_chars | avg_chars | char/sec | total_words | avg_words | word/sec |
|------|-----------:|-------------:|-----------:|------------:|----------:|---------:|------------:|----------:|---------:|
| it   |    7209567 |      6116.69 |      3.054 |   281128388 |    38.994 |  12.7669 |    48859270 |     6.777 |   2.2188 |
| fr   |    7557428 |      6535.94 |      3.113 |   344633332 |    45.602 |  14.6469 |    65843169 |     8.712 |   2.7983 |
| vi   |    6426292 |      6600.07 |      3.697 |   387621747 |    60.318 |  16.3139 |    89903814 |    13.990 |   3.7838 |
| pt   |    8682442 |      7384.42 |      3.062 |   339417251 |    39.092 |  12.7678 |    60316112 |     6.947 |   2.2689 |
| de   |   11003833 |      9842.14 |      3.220 |   487581808 |    44.310 |  13.7612 |    80119609 |     7.281 |   2.2612 |
| id   |   11659550 |     11246.27 |      3.472 |   543857918 |    46.645 |  13.4331 |    85967394 |     7.373 |   2.1234 |
| es   |   26407271 |     21224.55 |      2.893 |  1011116926 |    38.289 |  13.2331 |   183673862 |     6.955 |   2.4038 |
| ru   |   27474400 |     22919.31 |      3.003 |   991530233 |    36.089 |  12.0172 |   163018329 |     5.933 |   1.9758 |
| en   |    9515267 |     25347.90 |      9.590 |  1419864294 |   149.220 |  15.5597 |   268676221 |    28.236 |   2.9443 |
| zh   |   17776663 |     32919.28 |      6.667 |  2474249286 |   139.185 |  20.8781 |   496957308 |    27.956 |   4.1934 |

Words and chars statistics are computed based on the normalized alignment text (`align.txt`).

### Eval Set

- The eval set is built by filtering, trimming, and ranking aligned audio–text pairs.
- URL: [https://huggingface.co/datasets/LEMAS-Project/LEMAS-Dataset-eval](https://huggingface.co/datasets/LEMAS-Project/LEMAS-Dataset-eval)

**Filtering rules:**
- Average word-level alignment score > **0.9**
- Number of aligned words > **5**
- Duration between **3 and 15 seconds**
- Sentence-end silence is trimmed to at most **0.2s**

**Selection:**
- Samples are ranked by  
  `final_score = edge_gap × density_diff`, where  
  `edge_gap = words[0].start + (dur - words[-1].end)` and  
  `density_diff = |len(align_txt)/dur − global_mean_density|`

#### **Statistics**

| lang | utterances | total_dur(min) | avg_dur(s) | total_chars | avg_chars | char/sec | total_words | avg_words | word/sec |
|------|-----------:|---------------:|-----------:|------------:|----------:|---------:|------------:|----------:|---------:|
| it   |        500 |          44.22 |      5.306 |       40388 |     80.78 |   15.22  |        6599 |     13.20 |   2.49   |
| fr   |        500 |          38.17 |      4.580 |       38098 |     76.20 |   16.64  |        6546 |     13.09 |   2.86   |
| vi   |        500 |          36.74 |      4.409 |       28546 |     57.09 |   12.95  |        6727 |     13.45 |   3.05   |
| pt   |        500 |          41.69 |      5.003 |       33343 |     66.69 |   13.33  |        5812 |     11.62 |   2.32   |
| de   |        500 |          38.65 |      4.638 |       36571 |     73.14 |   15.77  |        5599 |     11.20 |   2.41   |
| id   |        500 |          47.20 |      5.665 |       41026 |     82.05 |   14.49  |        6133 |     12.27 |   2.17   |
| es   |        500 |          40.52 |      4.862 |       37075 |     74.15 |   15.25  |        6216 |     12.43 |   2.56   |
| ru   |        500 |          40.24 |      4.828 |       33886 |     67.77 |   14.04  |        5138 |     10.28 |   2.13   |
| en   |        500 |          67.46 |      8.095 |       62449 |    124.90 |   15.43  |       11325 |     22.65 |   2.80   |
| zh   |        500 |          75.84 |      9.101 |       95669 |    191.34 |   21.02  |       18627 |     37.25 |   4.09   |

Statistics are computed based on trimmed audio and normalized alignment text (`align.txt`).


## Citation
[https://arxiv.org/abs/2601.04233](https://arxiv.org/abs/2601.04233)
```
@article{zhao2026lemas,
  title={LEMAS: A 150K-Hour Large-scale Extensible Multilingual Audio Suite with Generative Speech Models},
  author={Zhao, Zhiyuan and Lin, Lijian and Zhu, Ye and Xie, Kai and Liu, Yunfei and Li, Yu},
  journal={arXiv preprint arXiv:2601.04233},
  year={2026}
}
```