"""情境③ LLM 批次推論（離線，吞吐導向）。

量離線大量處理的吞吐與整批完成時間，對應個資脫敏、文本要素辨識的日批量。
輸出：批次吞吐（筆/小時）、整批完成時間、單筆延遲（對 ≤1 秒門檻）。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scenarios._common import (add_stream_flag, load_dataset_inputs, make_inputs,
                               output_path, resolve_stream)
from config import Config
from core.client import make_adapter
from core.metrics import summarize
from core.reporter import print_summary, write_outputs
from core.runner import run_concurrent


def main():
    cfg = Config.load()
    ap = argparse.ArgumentParser(description="情境③ LLM 批次推論（離線吞吐）")
    ap.add_argument("--n", type=int, default=max(cfg.N_REQUESTS, 200), help="批次總筆數")
    ap.add_argument("--concurrency", type=int, default=32, help="批次並發度（盡量拉高）")
    ap.add_argument("--input-len", type=int, default=2000,
                    help="單筆合成輸入字元數（脫敏約 2000 字/筆；給 --dataset 時忽略）")
    ap.add_argument("--max-tokens", type=int, default=cfg.MAX_TOKENS)
    ap.add_argument("--label", default=cfg.RUN_LABEL)
    ap.add_argument("--dataset", default="",
                    help="從檔案讀 prompt 當輸入（.csv 取 prompt 欄/第一欄；.txt 一行一個）。"
                         "給了就覆蓋合成輸入；不足 --n 會循環補滿")
    add_stream_flag(ap)
    args = ap.parse_args()
    cfg.RUN_LABEL = args.label

    stream = resolve_stream(args, cfg)
    adapter = make_adapter(cfg)
    if args.dataset:
        inputs = load_dataset_inputs(args.dataset, args.n)
        src = f"資料集={args.dataset}"
    else:
        inputs = make_inputs(args.n, args.input_len)
        src = f"輸入長度={args.input_len}"
    print(f"情境③ 批次推論 | n={len(inputs)} 並發={args.concurrency} {src} "
          f"max_tokens={args.max_tokens} stream={stream} 標籤={cfg.RUN_LABEL}")

    results, wall = run_concurrent(adapter, inputs, args.concurrency,
                                   scenario="s3_batch", run_label=cfg.RUN_LABEL,
                                   max_tokens=args.max_tokens, temperature=cfg.TEMPERATURE,
                                   stream=stream)
    summ = summarize(results, wall_seconds=wall)
    csv_path, json_path = write_outputs(results, output_path(cfg, "s3_batch"))
    print_summary(summ)

    items_per_hour = (summ.success / wall * 3600.0) if wall > 0 else 0.0
    per_item_p95 = "—" if summ.e2e_s.p95 is None else f"{summ.e2e_s.p95:.3f}s"
    print(f"  批次吞吐量：{items_per_hour:,.0f} 筆/小時")
    print(f"  整批完成時間：{wall:.2f}s（成功 {summ.success}/{summ.count} 筆）")
    print(f"  單筆延遲 p95：{per_item_p95}（門檻：單筆 ≤ 1 秒；整批對 D+1 由業務判定）")
    print(f"明細 CSV：{csv_path}")
    print(f"明細 JSON（含輸入/思考內容/輸出內容/完整回應）：{json_path}")


if __name__ == "__main__":
    main()
