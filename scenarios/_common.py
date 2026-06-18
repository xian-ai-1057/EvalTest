"""情境腳本共用小工具：路徑設定、輸入構造、輸出檔名。"""
from __future__ import annotations

import csv as _csv
import glob as _glob
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


# 可辨識為「prompt 內容」的 CSV 表頭名稱（小寫比對）。找不到就退回第一欄。
_DATASET_PROMPT_COLUMNS = ("prompt", "input", "text", "question", "問題", "輸入")


def _load_csv_prompts(p: Path) -> list:
    """從 .csv 取出 prompt 清單。

    規則：若首列含可辨識的欄名（prompt / input / text…）則取該欄、其餘列為資料；
    否則視整份無表頭、取第一欄。略過空白儲存格與空白列。
    以 utf-8-sig 讀檔，相容 Excel 匯出的 BOM。
    """
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        rows = [row for row in _csv.reader(f) if any(cell.strip() for cell in row)]
    if not rows:
        return []
    header = [cell.strip().lower() for cell in rows[0]]
    col = next((i for i, name in enumerate(header) if name in _DATASET_PROMPT_COLUMNS), None)
    if col is not None:
        body = rows[1:]          # 認得的表頭 → 取該欄、跳過表頭列
    else:
        col, body = 0, rows      # 無可辨識表頭 → 取第一欄、整份都算資料
    out = []
    for row in body:
        if col < len(row) and row[col].strip():
            out.append(row[col].strip())
    return out


def load_prompts_file(path: str) -> list:
    """讀外部資料集，回傳 prompt 清單。

    依副檔名分流：
      - .csv：取 prompt 欄（找不到欄名就取第一欄），見 _load_csv_prompts。
      - 其他（.txt 等）：一行一個 prompt。
    皆去除前後空白、略過空白行；以 utf-8-sig 讀檔以相容含 BOM 的檔案。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到資料集檔案：{path}")
    if p.suffix.lower() == ".csv":
        return _load_csv_prompts(p)
    return [ln.strip() for ln in p.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]


def load_dataset_inputs(path: str, n=None) -> list:
    """把資料集檔案讀成情境輸入；語意對齊 load_image_paths：
    給定 n 時，不足則循環補滿、過多則截斷，確保跨卡比較筆數一致。
    """
    prompts = load_prompts_file(path)
    if not prompts:
        raise ValueError(f"資料集沒有可用的 prompt：{path}")
    if n and n > 0:
        prompts = [prompts[i % len(prompts)] for i in range(n)]
    return prompts


def load_image_paths(glob_or_dir: str, n=None) -> list:
    """從資料夾或 glob 取圖片路徑；若 n 大於圖片數則循環補滿。"""
    pattern = glob_or_dir
    if os.path.isdir(pattern):
        pattern = os.path.join(pattern, "*")
    paths = sorted(p for p in _glob.glob(pattern) if os.path.isfile(p))
    if n and paths:
        paths = [paths[i % len(paths)] for i in range(n)]
    return paths


def output_path(cfg, scenario: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    label = (cfg.RUN_LABEL or "run").replace("/", "_").replace(" ", "")
    return os.path.join(cfg.OUTPUT_DIR, f"{scenario}_{label}_{ts}.csv")
