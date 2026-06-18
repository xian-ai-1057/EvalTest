"""情境⑥ 語音轉文字推論（STT，選測）。

量語音辨識的即時率與單卡可服務路數。
  即時率(RTF) = 處理時間 ÷ 音檔長度，< 1 才能即時。

STT 服務非 chat 格式 → 實務上請在 .env 設 generic_json，並依你的端點調整：
    ADAPTER=generic_json
    GENERIC_URL=http://你的STT服務/transcribe
    GENERIC_REQUEST_TEMPLATE={"audio_ref": "{input}"}   # TODO: 多為音檔上傳/base64，依端點調整
    GENERIC_RESPONSE_PATH=text                            # TODO: 依回應結構調整
辨識率（字錯率）屬品質回歸（情境⑦），不在此量。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scenarios._common import output_path
from config import Config
from core.client import make_adapter
from core.metrics import summarize
from core.reporter import print_summary, write_outputs
from core.runner import run_concurrent, run_single


def main():
    cfg = Config.load()
    ap = argparse.ArgumentParser(description="情境⑥ STT 語音轉文字（即時率 / 並發路數）")
    ap.add_argument("--n", type=int, default=cfg.N_REQUESTS, help="音檔筆數")
    ap.add_argument("--concurrency", type=int, default=1, help="並發路數（>1 測單卡可服務路數）")
    ap.add_argument("--audio-seconds", type=float, default=30.0,
                    help="每個音檔長度（秒），用於計算即時率 RTF")
    ap.add_argument("--label", default=cfg.RUN_LABEL)
    args = ap.parse_args()
    cfg.RUN_LABEL = args.label

    adapter = make_adapter(cfg)
    # TODO: 實務上這裡應是音檔路徑/識別碼清單；此處以佔位字串讓流程可跑通
    inputs = [f"audio_sample_{i}" for i in range(args.n)]
    print(f"情境⑥ STT | n={args.n} 並發路數={args.concurrency} 音檔長度={args.audio_seconds}s "
          f"adapter={cfg.ADAPTER} 標籤={cfg.RUN_LABEL}")

    if args.concurrency <= 1:
        results = run_single(adapter, inputs, scenario="s6_stt", run_label=cfg.RUN_LABEL,
                             max_tokens=cfg.MAX_TOKENS, temperature=cfg.TEMPERATURE, stream=False)
        wall = None
    else:
        results, wall = run_concurrent(adapter, inputs, args.concurrency,
                                       scenario="s6_stt", run_label=cfg.RUN_LABEL,
                                       max_tokens=cfg.MAX_TOKENS, temperature=cfg.TEMPERATURE,
                                       stream=False)
    summ = summarize(results, wall_seconds=wall)
    csv_path, json_path = write_outputs(results, output_path(cfg, "s6_stt"))
    print_summary(summ)

    # 即時率：以端到端處理時間 ÷ 音檔長度
    e2e_vals = [r.e2e_s for r in results if r.success and r.e2e_s is not None]
    if e2e_vals and args.audio_seconds > 0:
        rtf_mean = (sum(e2e_vals) / len(e2e_vals)) / args.audio_seconds
        rtf_max = max(e2e_vals) / args.audio_seconds
        print(f"  即時率 RTF：mean={rtf_mean:.3f} max={rtf_max:.3f}（< 1 表示可即時）")
        print(f"  測試並發路數：{args.concurrency}（可逐步加大以找單卡可服務路數）")
    print(f"明細 CSV：{csv_path}")
    print(f"明細 JSON（含輸入/輸出內容/完整回應）：{json_path}")


if __name__ == "__main__":
    main()
