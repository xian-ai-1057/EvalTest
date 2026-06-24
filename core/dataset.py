# -*- coding: utf-8 -*-
"""輸入資料集：合成固定長度輸入，或讀 CSV/TXT，組成 [(prompt, answer), ...]。"""
from __future__ import annotations

import csv
from pathlib import Path

# 合成輸入用的基底句；CSV 表頭辨識（小寫比對）
_BASE_SENTENCE = "請以繁體中文回答以下問題並盡量延伸說明，涵蓋背景、原因與實務影響。"
_PROMPT_COLUMNS = ("prompt", "input", "text", "question", "問題", "輸入")
_ANSWER_COLUMNS = ("answer", "答案", "正解", "label", "標籤", "ground_truth")


def build_prompt(char_len: int) -> str:
    """構造固定字元長度的合成輸入，確保跨卡比較時輸入長度一致。"""
    if char_len <= 0:
        return _BASE_SENTENCE
    reps = (char_len // len(_BASE_SENTENCE)) + 1
    return (_BASE_SENTENCE * reps)[:char_len]


def load_dataset(path: str) -> list:
    """讀資料集，回傳 [(prompt, answer), ...]。

    .csv：以 csv.reader 讀（utf-8-sig 相容 Excel 匯出的 BOM）。首列若含可辨識欄名，
      prompt 取 prompt/input/text/question/問題/輸入，answer 取 answer/答案/正解/label/標籤/
      ground_truth（找不到 answer 欄則留空）；無可辨識欄名則取第一欄為 prompt、無正解。
    其他（.txt 等）：一行一個 prompt、answer 留空。
    皆去除前後空白、略過空白列。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到資料集檔案：{path}")
    if p.suffix.lower() == ".csv":
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
        if not rows:
            return []
        header = [c.strip().lower() for c in rows[0]]
        p_col = next((i for i, h in enumerate(header) if h in _PROMPT_COLUMNS), None)
        a_col = next((i for i, h in enumerate(header) if h in _ANSWER_COLUMNS), None)
        if p_col is None:                         # 無可辨識表頭 → 第一欄當 prompt、整份都算資料
            return [(r[0].strip(), "") for r in rows if r and r[0].strip()]
        out = []
        for r in rows[1:]:                        # 認得的表頭 → 跳過表頭列
            prompt = r[p_col].strip() if p_col < len(r) else ""
            answer = r[a_col].strip() if (a_col is not None and a_col < len(r)) else ""
            if prompt:
                out.append((prompt, answer))
        return out
    lines = [ln.strip() for ln in p.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
    return [(ln, "") for ln in lines]


def build_pairs(*, dataset, n_requests, input_len) -> list:
    """依參數決定輸入：有 dataset 讀檔（依 n_requests 對齊：不足循環補滿、過多截斷），
    否則造 n_requests 筆合成輸入。回傳 [(prompt, answer), ...]。"""
    if dataset:
        pairs = load_dataset(dataset)
        if not pairs:
            raise ValueError(f"資料集沒有可用內容：{dataset}")
        if n_requests and n_requests > 0:
            pairs = [pairs[i % len(pairs)] for i in range(n_requests)]
        return pairs
    return [(build_prompt(input_len), "") for _ in range(n_requests)]
