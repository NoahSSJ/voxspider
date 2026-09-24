---
dataset_info:
  features:
  - name: audio
    dtype: audio
  - name: file_name
    dtype: string
  - name: transcription
    dtype: string
  - name: speaker_name
    dtype: string
  - name: transcription_normalised
    dtype: string
  splits:
  - name: train
    num_bytes: 106549604076.4
    num_examples: 435384
  - name: test
    num_bytes: 26582977366.024
    num_examples: 108846
  download_size: 129501393125
  dataset_size: 133132581442.424
configs:
- config_name: default
  data_files:
  - split: train
    path: data/train-*
  - split: test
    path: data/test-*
---
