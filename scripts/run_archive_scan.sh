#!/usr/bin/env bash
set -eu
cd /home/shuxianzhong/voxspider
export PATH=/home/shuxianzhong/miniconda3/envs/hgf/bin:/home/shuxianzhong/.local/bin:/usr/local/bin:/usr/bin:/bin
exec /home/shuxianzhong/miniconda3/envs/hgf/bin/python -m core.archive_scan "$@"
