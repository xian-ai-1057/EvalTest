#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""準確率比對（單檔、用 function 分功能，非模組化）。

讀一份同時含「正解」與「模型回覆」的資料（simple_bench 產出的 JSON 明細，或自備 CSV），
讓你挑這兩個欄位做「完全相等」比對，輸出準確率與逐筆結果（主控台 ＋ 一個雙頁 Excel）。

欄位用下方「參數設定區」指定（即「讓我選擇要比對的欄位」）；若指定的欄名不存在，
程式會印出檔案裡可用的欄位清單、請你回設定區改成正確欄名。直接執行：

    python accuracy.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.xlsx import write_workbook   # noqa: E402


# ===== 參數設定區（改這裡就好；執行：python accuracy.py）=====
INPUT_PATH = "results/simple_H100-FP8_<ts>.json"  # 跑分產出的 JSON，或自備 CSV
ANSWER_FIELD = "answer"        # 你要比對的「正解」欄位名
REPLY_FIELD = "output_text"    # 你要比對的「模型回覆」欄位名
OUT_PATH = ""                  # 留空＝輸入檔同名 _accuracy.xlsx
# ===========================================================


def load_records(path: str) -> list:
    """讀資料為 list[dict]。

    .json：json.load（取 simple_bench 產出的明細陣列，最外層需為 list）。
    .csv ：csv.DictReader（utf-8-sig 相容 Excel BOM）。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到輸入檔：{path}")
    suffix = p.suffix.lower()
    if suffix == ".json":
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError(f"JSON 最外層需為陣列（list），實際為 {type(data).__name__}")
        return [d for d in data if isinstance(d, dict)]
    if suffix == ".csv":
        with p.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    raise ValueError(f"不支援的副檔名：{p.suffix}（請用 .json 或 .csv）")


def list_fields(records: list) -> list:
    """依出現順序去重的欄位名聯集（給「選欄位」用）。"""
    seen = {}
    for rec in records:
        for k in rec:
            seen.setdefault(k, None)
    return list(seen)


def check_field(fields: list, name: str, role: str) -> str:
    """驗證設定區指定的欄位存在；不存在則印可用欄位清單並結束（請回設定區改變數）。"""
    if name in fields:
        return name
    print(f"✗ 找不到{role}欄位「{name}」。檔案裡可用的欄位有：")
    for i, f in enumerate(fields, 1):
        print(f"    {i}. {f}")
    print("→ 請編輯 accuracy.py 設定區，把對應變數改成上面其中一個欄位名再執行。")
    sys.exit(1)


def compare_exact(records: list, answer_field: str, reply_field: str):
    """逐筆「完全相等」比對。回傳 (summary, rows)。

    兩值各自 str().strip() 後比較相等；正解為空者跳過（無 ground truth、不計入）。
    summary：total（總筆數）/ compared（可比對）/ correct（正確）/ accuracy（0~1）。
    rows：[index, 正解, 回覆, 正確(bool)]，供寫進逐筆比對頁。
    """
    rows = []
    correct = compared = 0
    for i, rec in enumerate(records):
        a = str(rec.get(answer_field, "") or "").strip()
        b = str(rec.get(reply_field, "") or "").strip()
        if a == "":
            continue                              # 沒有正解可比，跳過
        compared += 1
        ok = (a == b)
        if ok:
            correct += 1
        rows.append([rec.get("index", i), a, b, ok])
    accuracy = (correct / compared) if compared else 0.0
    summary = {"total": len(records), "compared": compared,
               "correct": correct, "accuracy": accuracy}
    return summary, rows


def write_result(out_path, summary, rows, meta):
    """主控台印摘要 ＋ 用 write_workbook 出 .xlsx（第①頁準確率摘要、第②頁逐筆比對）。"""
    skipped = summary["total"] - summary["compared"]
    print("=" * 60)
    print(f"準確率比對｜輸入 {meta['input']}")
    print(f"  比對欄位：正解＝{meta['answer_field']}　回覆＝{meta['reply_field']}")
    print(f"  總筆數 {summary['total']}（可比對 {summary['compared']}，跳過 {skipped} 筆無正解）")
    print(f"  正確 {summary['correct']} / {summary['compared']}"
          f"　準確率 {summary['accuracy'] * 100:.2f}%")
    print("=" * 60)

    sheet_summary = [
        ["輸入檔", meta["input"]],
        ["正解欄位", meta["answer_field"], "回覆欄位", meta["reply_field"]],
        ["總筆數", summary["total"], "可比對", summary["compared"], "跳過(無正解)", skipped],
        ["正確", summary["correct"], "準確率(%)", round(summary["accuracy"] * 100, 2)],
    ]
    detail = [["index", meta["answer_field"], meta["reply_field"], "正確"]] + rows
    write_workbook(out_path, [("準確率摘要", sheet_summary), ("逐筆比對", detail)])
    print(f"已輸出：{out_path}")


def main():
    records = load_records(INPUT_PATH)
    if not records:
        print(f"✗ 輸入檔沒有可比對的資料：{INPUT_PATH}")
        sys.exit(1)
    fields = list_fields(records)
    answer_field = check_field(fields, ANSWER_FIELD, "正解")
    reply_field = check_field(fields, REPLY_FIELD, "模型回覆")

    summary, rows = compare_exact(records, answer_field, reply_field)

    src = Path(INPUT_PATH)
    out_path = OUT_PATH or str(src.with_name(src.stem + "_accuracy.xlsx"))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    write_result(out_path, summary, rows,
                 {"input": INPUT_PATH, "answer_field": answer_field, "reply_field": reply_field})


if __name__ == "__main__":
    main()
