"""VLM 圖片→文本測試（接入已開發好的服務）。

同一支腳本支援兩種呼叫形態，差別只在 .env 的 ADAPTER：
  - ADAPTER=vlm     ：HTTP 自訂 JSON（base64），見 VLM_* 設定。
  - ADAPTER=package ：模型已封裝成 Python 套件，直接呼叫（見 core/package_adapter_example.py）。
圖片來源：資料夾/glob（--images，預設取 .env 的 VLM_IMAGE_GLOB），所有圖片配同一段提示詞。
量端到端延遲；--concurrency>1 另量吞吐（張/秒）與並發路數。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scenarios._common import load_image_paths, output_path
from config import Config
from core.client import make_adapter
from core.metrics import summarize
from core.reporter import print_summary, write_csv
from core.runner import run_concurrent, run_single


def main():
    cfg = Config.load()
    ap = argparse.ArgumentParser(description="VLM 圖片→文本（ADAPTER=vlm 或 package）")
    ap.add_argument("--images", default=cfg.VLM_IMAGE_GLOB, help="圖片資料夾或 glob")
    ap.add_argument("--n", type=int, default=None, help="總筆數（不足循環補滿；預設=圖片數）")
    ap.add_argument("--concurrency", type=int, default=1, help="1=單張延遲；>1=吞吐/並發路數")
    ap.add_argument("--prompt", default=None, help="共用提示詞（預設取 .env VLM_PROMPT）")
    ap.add_argument("--max-tokens", type=int, default=cfg.MAX_TOKENS)
    ap.add_argument("--label", default=cfg.RUN_LABEL)
    args = ap.parse_args()
    cfg.RUN_LABEL = args.label
    if args.prompt is not None:
        cfg.VLM_PROMPT = args.prompt

    images = load_image_paths(args.images, args.n)
    if not images:
        print(f"找不到圖片：{args.images}（請放圖片，或用 --images 指定資料夾/glob）")
        return

    adapter = make_adapter(cfg)
    print(f"VLM 圖片→文本 | adapter={cfg.ADAPTER} 圖片數={len(images)} 並發={args.concurrency} "
          f"提示詞={cfg.VLM_PROMPT!r} 標籤={cfg.RUN_LABEL}")

    if args.concurrency <= 1:
        results = run_single(adapter, images, scenario="s_vlm", run_label=cfg.RUN_LABEL,
                             max_tokens=args.max_tokens, temperature=cfg.TEMPERATURE, stream=False)
        wall = None
    else:
        results, wall = run_concurrent(adapter, images, args.concurrency, scenario="s_vlm",
                                       run_label=cfg.RUN_LABEL, max_tokens=args.max_tokens,
                                       temperature=cfg.TEMPERATURE, stream=False)
    summ = summarize(results, wall_seconds=wall)
    path = write_csv(results, output_path(cfg, "s_vlm"))
    print_summary(summ)
    if wall:
        thr = (summ.success / wall) if wall > 0 else 0.0
        print(f"  吞吐量：{thr:,.1f} 張/秒（並發路數 {args.concurrency}，整批 {wall:.2f}s）")
    print(f"明細 CSV：{path}")


if __name__ == "__main__":
    main()
