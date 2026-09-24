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


def get_urls(url: str) -> list:
    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
        'Cache-Control': 'max-age=0',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
    }

    response = requests.get(url, headers=headers)
    # print(response.text)
    # urls = re.findall(r'<a href="(.*?)"> \[EU\] </a>', response.text)
    urls = re.findall(r'<a href="(.*?)">', response.text)
    # urls = [url for url in urls if url.startswith("https://openslr.trmal.net/")]
    print(urls)
    print(len(urls))
    return urls


if __name__ == "__main__":
    log = setup_logger("100")
    root_dir = get_root_dir()
    save_dir = root_dir / "datasets" / "openslr" / "Multilingual-TEDx"
    save_dir.mkdir(parents=True, exist_ok=True)
    
    urls = get_urls("https://openslr.org/94") 
    exit()
    for index, url in enumerate(urls):
        file_name = url.split("/")[-1]
        file_path = save_dir / file_name
        try:
            subprocess.run(
                [
                    "curl",
                    "--fail",           # HTTP 错误时返回失败
                    "--location",       # 跟随重定向
                    "--retry", "10",     # 部分临时错误最多重试 10 次
                    "--continue-at", "-",
                    "--output", str(file_path),
                    "--url", url,
                ],
                check=True,
            )
            log(f"下载完成：{file_path}")
        except subprocess.CalledProcessError as e:
            print(f"下载失败：{url}，退出码：{e.returncode}")