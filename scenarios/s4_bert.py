"""情境④ 編碼器分類推論（BERT，短序列）。

量 BERT 類模型的單筆延遲與批次吞吐 / QPS。
BERT 服務多半不是 OpenAI chat 格式 → 實務上請在 .env 設：
    ADAPTER=generic_json
    GENERIC_URL=http://你的BERT服務/predict
    GENERIC_REQUEST_TEMPLATE={"text": "{input}"}     # TODO: 依你的端點調整
    GENERIC_RESPONSE_PATH=label                        # TODO: 依回應結構調整
（未設定時會使用 .env 既定 adapter，方便先用假伺服器跑通流程。）
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scenarios._common import make_inputs, output_path
from config import Config
from core.client import make_adapter
from core.metrics import summarize
from core.reporter import print_summary, write_outputs
from core.runner import run_concurrent, run_single


def main():
    cfg = Config.load()
    ap = argparse.ArgumentParser(description="情境④ BERT 分類推論（單筆延遲 / 批次吞吐）")
    ap.add_argument("--mode", choices=["single", "batch"], default="single",
                    help="single=即時(batch=1)；batch=拉大並發測吞吐")
    ap.add_argument("--n", type=int, default=cfg.N_REQUESTS)
    ap.add_argument("--concurrency", type=int, default=32, help="batch 模式的並發度")
    ap.add_argument("--seq-len", type=int, default=128, help="固定序列長度（字元）")
    ap.add_argument("--label", default=cfg.RUN_LABEL)
    args = ap.parse_args()
    cfg.RUN_LABEL = args.label

    adapter = make_adapter(cfg)
    inputs = make_inputs(args.n, args.seq_len)
    print(f"情境④ BERT 推論 | mode={args.mode} n={args.n} 序列長度={args.seq_len} "
          f"adapter={cfg.ADAPTER} 標籤={cfg.RUN_LABEL}")

    if args.mode == "single":
        results = run_single(adapter, inputs, scenario="s4_bert",
                             run_label=cfg.RUN_LABEL, max_tokens=cfg.MAX_TOKENS,
                             temperature=cfg.TEMPERATURE, stream=False)
        summ = summarize(results)
        print_summary(summ)
        print("  即時(batch=1) 重點看單筆延遲 e2e。")
    else:
        results, wall = run_concurrent(adapter, inputs, args.concurrency,
                                       scenario="s4_bert", run_label=cfg.RUN_LABEL,
                                       max_tokens=cfg.MAX_TOKENS, temperature=cfg.TEMPERATURE,
                                       stream=False)
        summ = summarize(results, wall_seconds=wall)
        print_summary(summ)
        qps = (summ.success / wall) if wall > 0 else 0.0
        print(f"  批次吞吐量：{qps:,.1f} 筆/秒（QPS）｜整批 {wall:.2f}s")
    xlsx_path, json_path = write_outputs(results, summ, output_path(cfg, "s4_bert"))
    print(f"Excel 報表（第①頁統計摘要、第②頁明細）：{xlsx_path}")
    print(f"JSON 明細（含輸入/輸出內容/完整回應）：{json_path}")


if __name__ == "__main__":
    main()
