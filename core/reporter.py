"""輸出：每筆明細寫 CSV + JSON、主控台印平均/百分位摘要。

CSV 走 field_names()（略過超長的 raw_response），保留可快速檢視的數值與原文欄位；
JSON 明細則完整保存每筆所有欄位，含 raw_response（完整原始回應）。
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from core.metrics import Summary, field_names


def write_csv(results: list, path: str) -> str:
    """把每一筆 RequestResult 寫成 CSV（欄位＝field_names()，含輸入/思考/輸出原文）。回傳實際路徑。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cols = field_names()
    with p.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for r in results:
            row = asdict(r)
            writer.writerow({k: ("" if row[k] is None else row[k]) for k in cols})
    return str(p)


def write_json(results: list, path: str) -> str:
    """把每一筆 RequestResult 寫成 JSON 明細（含完整原始回應 raw_response）。回傳實際路徑。

    raw_response 會試著還原成巢狀 JSON 物件，便於檢視思考/輸出/usage 等；無法解析則保留原字串。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for r in results:
        row = asdict(r)
        raw = row.get("raw_response")
        if raw:
            try:
                row["raw_response"] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass  # 無法解析就保留原字串
        records.append(row)
    with p.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    return str(p)


def write_outputs(results: list, csv_path: str):
    """同時輸出 CSV 明細與 JSON 明細（JSON 為 CSV 同名改 .json）。回傳 (csv_path, json_path)。

    情境腳本統一呼叫這支：CSV 供快速檢視（含輸入/思考內容/輸出內容原文），
    JSON 供保存完整原始回應（raw_response）。
    """
    csv_out = write_csv(results, csv_path)
    json_out = write_json(results, str(Path(csv_path).with_suffix(".json")))
    return csv_out, json_out


def _fmt(v, nd=2):
    return "—" if v is None else f"{v:.{nd}f}"


def _stat_line(label, stat, nd=2):
    return (f"  {label:18} n={stat.n:<4} "
            f"mean={_fmt(stat.mean, nd):>10} "
            f"p50={_fmt(stat.p50, nd):>10} "
            f"p95={_fmt(stat.p95, nd):>10} "
            f"p99={_fmt(stat.p99, nd):>10}")


def print_summary(summary: Summary, gpu_stats=None) -> None:
    s = summary
    print("=" * 78)
    head = f"情境 {s.scenario} | 標籤 {s.run_label}"
    if s.concurrency is not None:
        head += f" | 併發 {s.concurrency}"
    print(head)
    print("-" * 78)
    print(f"  請求數 {s.count}（成功 {s.success} / 失敗 {s.failed}，成功率 {s.success_rate*100:.1f}%）"
          + (f" | 牆鐘 {s.wall_seconds:.2f}s" if s.wall_seconds else ""))
    print(_stat_line("TTFT 首字(ms)", s.ttft_ms))
    print(_stat_line("TPOT 逐字(ms/字)", s.tpot_ms, nd=3))
    print(_stat_line("端到端 e2e(s)", s.e2e_s, nd=3))
    print(_stat_line("單請求 tok/s", s.tokens_per_s))
    print(_stat_line("單請求 字/s", s.chars_per_s))
    if s.throughput_tokens_per_s is not None:
        print(f"  系統總吞吐         tokens/s={_fmt(s.throughput_tokens_per_s)}  "
              f"chars/s={_fmt(s.throughput_chars_per_s)}  "
              f"(Σtokens={s.total_output_tokens}, Σchars={s.total_output_chars})")
    if gpu_stats is not None:
        print(f"  GPU                使用率 mean={_fmt(gpu_stats.util_mean)}% "
              f"max={_fmt(gpu_stats.util_max)}% | "
              f"記憶體 mean={_fmt(gpu_stats.mem_mean_mb)}MB max={_fmt(gpu_stats.mem_max_mb)}MB "
              f"(samples={gpu_stats.samples})")
    print("=" * 78)
