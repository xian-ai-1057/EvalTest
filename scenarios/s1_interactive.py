"""情境① 互動式 LLM 生成（延遲導向，併發=1）。

量單一請求的 TTFT / TPOT / 端到端延遲 / 單請求輸出速率，反映使用者實際打字感受。
FP8 / FP4 各跑一輪（用 --label 區分）。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scenarios._common import load_dataset_inputs, make_inputs, output_path
from config import Config
from core.client import make_adapter
from core.metrics import summarize
from core.reporter import print_summary, write_outputs
from core.runner import run_single


def main():
    cfg = Config.load()
    ap = argparse.ArgumentParser(description="情境① 互動式 LLM 生成（單請求延遲，併發=1）")
    ap.add_argument("--n", type=int, default=cfg.N_REQUESTS, help="請求筆數")
    ap.add_argument("--input-len", type=int, default=cfg.INPUT_LEN,
                    help="合成輸入的字元長度（給 --dataset 時忽略）")
    ap.add_argument("--max-tokens", type=int, default=cfg.MAX_TOKENS, help="輸出長度上限")
    ap.add_argument("--label", default=cfg.RUN_LABEL, help="執行標籤，如 H100-FP8")
    ap.add_argument("--dataset", default="",
                    help="從檔案讀 prompt 當輸入（.csv 取 prompt 欄/第一欄；.txt 一行一個）。"
                         "給了就覆蓋合成輸入；不足 --n 會循環補滿")
    ap.add_argument("--no-stream", action="store_true", help="關閉串流（將無法量 TTFT/TPOT）")
    args = ap.parse_args()
    cfg.RUN_LABEL = args.label

    adapter = make_adapter(cfg)
    if args.dataset:
        inputs = load_dataset_inputs(args.dataset, args.n)
        src = f"資料集={args.dataset}"
    else:
        inputs = make_inputs(args.n, args.input_len)
        src = f"輸入長度={args.input_len}"
    stream = not args.no_stream
    print(f"情境① 互動式生成 | n={len(inputs)} 併發=1 {src} "
          f"max_tokens={args.max_tokens} stream={stream} 標籤={cfg.RUN_LABEL}")

    results = run_single(adapter, inputs, scenario="s1_interactive",
                         run_label=cfg.RUN_LABEL, max_tokens=args.max_tokens,
                         temperature=cfg.TEMPERATURE, stream=stream)
    summ = summarize(results)
    print_summary(summ)
    xlsx_path, json_path = write_outputs(results, summ, output_path(cfg, "s1_interactive"))
    print(f"Excel 報表（第①頁統計摘要、第②頁明細）：{xlsx_path}")
    print(f"JSON 明細（含輸入/思考內容/輸出內容/完整回應）：{json_path}")


if __name__ == "__main__":
    main()
