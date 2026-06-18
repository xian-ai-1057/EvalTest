"""情境腳本共用小工具：路徑設定、輸入構造、輸出檔名。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# 讓 `python scenarios/xxx.py` 直接執行時，能 import 到專案根目錄的 config / core
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_BASE_SENTENCE = "請以繁體中文回答以下問題並盡量延伸說明，涵蓋背景、原因與實務影響。"


def build_prompt(char_len: int) -> str:
    """構造固定字元長度的輸入，確保比較時輸入長度一致。"""
    if char_len <= 0:
        return _BASE_SENTENCE
    reps = (char_len // len(_BASE_SENTENCE)) + 1
    return (_BASE_SENTENCE * reps)[:char_len]


def load_sample_prompts() -> list:
    p = Path(ROOT) / "data" / "prompts.sample.txt"
    if not p.exists():
        return []
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def make_inputs(n: int, char_len: int) -> list:
    return [build_prompt(char_len) for _ in range(n)]


def output_path(cfg, scenario: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    label = (cfg.RUN_LABEL or "run").replace("/", "_").replace(" ", "")
    return os.path.join(cfg.OUTPUT_DIR, f"{scenario}_{label}_{ts}.csv")
