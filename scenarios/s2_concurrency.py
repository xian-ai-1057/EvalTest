"""情境② LLM 高併發服務（吞吐導向）。

掃描多個併發級別，每級記錄 P95 延遲、系統總吞吐與 GPU 使用率；
在延遲不破 SLA 的前提下，找出單卡可承載的最大併發。
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
from core.gpu import GpuSampler
from core.metrics import summarize
from core.reporter import print_summary, write_outputs
from core.runner import run_concurrent


def _parse_levels(text, default):
    if not text:
        return list(default)
    out = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out or list(default)


def _meets_sla(summ, sla_ttft_ms, sla_p95_ms):
    ttft_ok = (summ.ttft_ms.p95 is None) or (summ.ttft_ms.p95 <= sla_ttft_ms)
    e2e_p95_ms = None if summ.e2e_s.p95 is None else summ.e2e_s.p95 * 1000.0
    e2e_ok = (e2e_p95_ms is None) or (e2e_p95_ms <= sla_p95_ms)
    return ttft_ok and e2e_ok and summ.failed == 0


def main():
    cfg = Config.load()
    ap = argparse.ArgumentParser(description="情境② LLM 高併發服務（併發掃描）")
    ap.add_argument("--concurrency", default="", help="併發級別，如 1,8,16,32（預設取 .env）")
    ap.add_argument("--n", type=int, default=cfg.N_REQUESTS, help="每個併發級別的請求筆數")
    ap.add_argument("--input-len", type=int, default=cfg.INPUT_LEN,
                    help="合成輸入的字元長度（給 --dataset 時忽略）")
    ap.add_argument("--max-tokens", type=int, default=cfg.MAX_TOKENS)
    ap.add_argument("--label", default=cfg.RUN_LABEL)
    ap.add_argument("--dataset", default="",
                    help="從檔案讀 prompt 當輸入（.csv 取 prompt 欄/第一欄；.txt 一行一個）。"
                         "給了就覆蓋合成輸入；每個併發級別取 --n 筆，不足循環補滿")
    ap.add_argument("--arrival", choices=["closed", "poisson"], default="closed")
    ap.add_argument("--rate", type=float, default=None, help="poisson 模式每秒請求數")
    add_stream_flag(ap)
    args = ap.parse_args()
    cfg.RUN_LABEL = args.label

    levels = _parse_levels(args.concurrency, cfg.CONCURRENCY_LEVELS)
    stream = resolve_stream(args, cfg)
    adapter = make_adapter(cfg)
    src = f"資料集={args.dataset}" if args.dataset else f"輸入長度={args.input_len}"
    print(f"情境② 高併發 | 併發級別={levels} 每級 n={args.n} {src} max_tokens={args.max_tokens} "
          f"stream={stream} 標籤={cfg.RUN_LABEL} SLA(TTFT≤{cfg.SLA_TTFT_MS}ms, e2e_p95≤{cfg.SLA_P95_MS}ms)")

    all_results = []
    per_level = []
    for level in levels:
        inputs = (load_dataset_inputs(args.dataset, args.n) if args.dataset
                  else make_inputs(args.n, args.input_len))
        sampler = GpuSampler(cfg.GPU_SAMPLE_INTERVAL) if cfg.GPU_MONITOR else None
        if sampler:
            sampler.start()
        results, wall = run_concurrent(adapter, inputs, level,
                                       scenario="s2_concurrency", run_label=cfg.RUN_LABEL,
                                       max_tokens=args.max_tokens, temperature=cfg.TEMPERATURE,
                                       stream=stream, arrival=args.arrival, rate=args.rate)
        gpu_stats = sampler.stop() if sampler else None
        summ = summarize(results, wall_seconds=wall)
        print_summary(summ, gpu_stats)
        all_results.extend(results)
        per_level.append((level, summ))

    csv_path, json_path = write_outputs(all_results, output_path(cfg, "s2_concurrency"))

    # 跨級別總表 + SLA 下最大併發
    print("\n併發掃描總表（依 SLA 判定達標）")
    print(f"{'併發':>6} {'成功率':>7} {'TTFT_p95(ms)':>13} {'e2e_p95(s)':>11} "
          f"{'總吞吐(tok/s)':>14} {'達標':>5}")
    max_ok = None
    for level, summ in per_level:
        ok = _meets_sla(summ, cfg.SLA_TTFT_MS, cfg.SLA_P95_MS)
        if ok:
            max_ok = level if max_ok is None else max(max_ok, level)
        ttft = "—" if summ.ttft_ms.p95 is None else f"{summ.ttft_ms.p95:.1f}"
        e2e = "—" if summ.e2e_s.p95 is None else f"{summ.e2e_s.p95:.3f}"
        thr = "—" if summ.throughput_tokens_per_s is None else f"{summ.throughput_tokens_per_s:.1f}"
        print(f"{level:>6} {summ.success_rate*100:>6.1f}% {ttft:>13} {e2e:>11} {thr:>14} "
              f"{'✓' if ok else '✗':>5}")
    print(f"\nSLA 下最大可承載併發：{max_ok if max_ok is not None else '無（最低併發即超標）'}")
    print(f"明細 CSV：{csv_path}")
    print(f"明細 JSON（含輸入/思考內容/輸出內容/完整回應）：{json_path}")


if __name__ == "__main__":
    main()
