# -*- coding: utf-8 -*-
"""輸出：每次執行產生一個結構化 JSON dict（{summary, params, detail}）＋一個三頁 Excel，
並在主控台印平均/百分位摘要。

「輸出結果」就是 build_report() 回傳的那個 dict：第一層 summary（統計）、params（執行參數）、
detail（逐筆明細，含完整原始回應 raw_response，會試還原成巢狀物件）。這份 dict 直接寫成 JSON，
也是 accuracy.py 讀的對象（取 detail）。Excel 第②頁「明細」用 pandas 轉換（DataFrame → openpyxl），
不再每種輸出各寫一套序列化。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from core.metrics import Summary, field_names


# 統計摘要頁的延遲/速率指標列（標籤 → Summary 屬性名 → 顯示小數位，對齊主控台精度）
_SUMMARY_METRICS = (
    ("TTFT 首字(ms)", "ttft_ms", 2),
    ("TPOT 逐字(ms/字)", "tpot_ms", 3),
    ("端到端 e2e(s)", "e2e_s", 3),
    ("單請求 tok/s", "tokens_per_s", 2),
    ("單請求 字/s", "chars_per_s", 2),
)


def _round(v, nd):
    """四捨五入到 nd 位；None 維持 None（儲存格留空）。完整精度仍保留在明細頁與 JSON。"""
    return None if v is None else round(v, nd)


def build_report(results, summary, params=None) -> dict:
    """組「輸出結果」dict：{summary, params, detail}。

    summary：asdict(Summary)（含巢狀 Stat）。params：dict（list[(k,v)] 會轉成 dict）。
    detail：逐筆 asdict（含 raw_response，會試以 json.loads 還原成巢狀物件，便於檢視思考/輸出/usage）。
    明細刻意走 asdict、不經 DataFrame，避免 pandas 把 None→NaN（非法 JSON）或把 int 欄上轉 float。
    """
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
    if params is None:
        params_out = {}
    elif isinstance(params, dict):
        params_out = dict(params)
    else:
        params_out = {str(k): v for k, v in params}
    return {"summary": asdict(summary), "params": params_out, "detail": records}


def _summary_sheet_rows(summaries, gpus) -> list:
    """把一或多個 Summary（+選用 GpuStats）排成「統計摘要」頁的列。

    版面對齊主控台那張表：每個 Summary 一個區塊（情境/標籤/併發 → 請求數 → 指標表 →
    系統總吞吐 → GPU），多個區塊之間空一列分隔。指標表為 指標×(n/mean/p50/p90/p95/p99/min/max)，
    數值依主控台精度四捨五入（完整精度保留在第②頁明細與 JSON）。
    """
    rows = []
    for idx, (s, g) in enumerate(zip(summaries, gpus)):
        if idx:
            rows.append([])                          # 多個 Summary 之間空一列
        rows.append(["情境", s.scenario, "標籤", s.run_label, "併發", s.concurrency])
        rows.append(["請求數", s.count, "成功", s.success, "失敗", s.failed,
                     "成功率(%)", round(s.success_rate * 100, 1),
                     "牆鐘(s)", _round(s.wall_seconds, 2)])
        rows.append(["指標", "n", "mean", "p50", "p90", "p95", "p99", "min", "max"])
        for label, attr, nd in _SUMMARY_METRICS:
            st = getattr(s, attr)
            rows.append([label, st.n,
                         _round(st.mean, nd), _round(st.p50, nd), _round(st.p90, nd),
                         _round(st.p95, nd), _round(st.p99, nd),
                         _round(st.minimum, nd), _round(st.maximum, nd)])
        if s.throughput_tokens_per_s is not None:
            rows.append(["系統總吞吐", "tokens/s", _round(s.throughput_tokens_per_s, 2),
                         "chars/s", _round(s.throughput_chars_per_s, 2),
                         "Σtokens", s.total_output_tokens, "Σchars", s.total_output_chars])
        if g is not None:
            rows.append(["GPU", "使用率mean(%)", _round(g.util_mean, 2), "max(%)", _round(g.util_max, 2),
                         "記憶體mean(MB)", _round(g.mem_mean_mb, 2), "max(MB)", _round(g.mem_max_mb, 2),
                         "samples", g.samples])
    return rows


def _params_rows(params) -> list:
    """把執行參數排成「執行參數」頁的列（表頭＝參數 / 值）。

    params 可為 dict 或 [(key, value), …]；值保留原型別（bool / 數值由 openpyxl 寫成對應儲存格格式）。
    """
    items = params.items() if isinstance(params, dict) else params
    rows = [["參數", "值"]]
    for k, v in items:
        rows.append([str(k), v])
    return rows


def _rect(rows) -> list:
    """把不規則列補成等寬（缺格填 None），讓 pandas 能直接建表。"""
    width = max((len(r) for r in rows), default=0)
    return [list(r) + [None] * (width - len(r)) for r in rows]


def write_outputs(results, summary, base_path, *, params=None):
    """輸出一個結構化 JSON dict（{summary, params, detail}）＋一個三頁 Excel。回傳 (xlsx, json)。

    JSON 由 build_report 產生（明細走 asdict、不經 DataFrame）。Excel 用 pandas(openpyxl) 寫：
    第①頁統計摘要、第②頁明細（pandas 轉換、略過超長的 raw_response）、第③頁執行參數（有 params 才寫）。
    """
    report = build_report(results, summary, params)

    base = Path(base_path)
    xlsx_path = base.with_suffix(".xlsx")
    json_path = base.with_suffix(".json")
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 第②頁「明細」：用 pandas 轉換；表頭＝field_names()（略過 raw_response）。
    # astype(object).where(notna, None)：保住整數顯示、缺值留空（避免 pandas 的 NaN/float 化）。
    detail_df = pd.DataFrame(report["detail"], columns=field_names()).astype(object)
    detail_df = detail_df.where(detail_df.notna(), None)
    # 第①頁「統計摘要」：沿用既有版面（不規則列補成等寬）。
    summary_df = pd.DataFrame(_rect(_summary_sheet_rows([summary], [None])))
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as xw:
        summary_df.to_excel(xw, sheet_name="統計摘要", index=False, header=False)
        detail_df.to_excel(xw, sheet_name="明細", index=False)
        if params:
            params_df = pd.DataFrame(_rect(_params_rows(report["params"])))
            params_df.to_excel(xw, sheet_name="執行參數", index=False, header=False)
    return str(xlsx_path), str(json_path)


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
