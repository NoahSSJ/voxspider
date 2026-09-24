import asyncio

import requests
import threading
import httpx
import json
import os
import sys
import math
import random
from pprint import pprint
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import time
import re
import subprocess
import base64
import urllib
import yt_dlp
from concurrent.futures import ThreadPoolExecutor, as_completed


def get_root_dir() -> Path:
    current_dir = Path(__file__).parent.resolve()
    for dir in (current_dir, *current_dir.parents):
        if dir.joinpath("pyproject.toml").exists():
            return dir
    raise RuntimeError("找不到根目录")

def setup_logger(dataset_name: str) -> logging.Logger:
    root_dir = get_root_dir()
    log_dir = root_dir / "logs" / dataset_name
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir.joinpath("run.log")
    logger = logging.getLogger(dataset_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger
    file_handler = RotatingFileHandler(
        filename=log_path,
        maxBytes=30 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    console_handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d | %(funcName)s | %(message)s"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

log = setup_logger('ytb')
root_dir = get_root_dir()
save_dir = root_dir / "data" / "ytb"
save_dir.mkdir(parents=True, exist_ok=True)
save_path = save_dir / "ytb.json"

def search_channels(keyword: str, query_num: int) -> list[str]:
    cmd = [
        "yt-dlp", f"ytsearch{query_num}:{keyword}",
        "--print", "%(channel_url)s",
            "--flat-playlist", "--no-download", "--quiet", "--no-warnings",
        ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    channels = set([
        line.strip().rstrip('/') for line in r.stdout.splitlines() if line.startswith("https://www.youtube.com/")
    ])
    for channel in channels:
        log.info(f"Found channel: {channel}")
    log.info(f"Found channels for keyword '{keyword}': {len(channels)}")
    return channels


    
KEYWORDS = [
    'explication de la programmation',                 # 编程知识讲解
    'explication des connaissances en classe',         # 课堂知识讲解
    'explication des connaissances du primaire',       # 小学知识讲解
    'explication des connaissances du collège',        # 初中知识讲解
    'explication des connaissances du lycée',          # 高中知识讲解
    'explication des connaissances universitaires',    # 大学知识讲解
    'explication des mathématiques',                    # 数学知识讲解
    'explication de la physique',                       # 物理知识讲解
    'explication de la médecine',                       # 医学专业知识讲解
    'explication de l’informatique',                    # 计算机专业知识讲解
]


        



if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=3) as executors:
        futures = {
            executors.submit(search_channels(KEYWORDS[0], query_num=10))
        }
        for future in as_completed(futures):
            channels = futures[future]
            try:
                log.info(channels)
            except Exception as e:
                # log.error(str{e})
                pass
