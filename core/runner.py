# -*- coding: utf-8 -*-
"""並發跑分：把每筆 prompt 組成 body、丟給 client.call，並逐筆 stamp 中繼資料。

body 在這層（管線）組好後傳入 client.call —— call 只負責呼叫。closed-loop：固定
cfg.concurrency 個 worker 同時在飛（ThreadPoolExecutor）。
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.client import call
from core.metrics import RequestResult
from core.payload import build_body


def run_benchmark(pairs, cfg):
    """並發跑一批 (prompt, answer)。回傳 (results, wall_seconds)。

    每筆先用 cfg.body_builder（或通用版 build_body）組好 body，再交給 client.call；完成後
    逐筆 stamp scenario / run_label / index / concurrency / answer / input_text。
    wall_seconds 量整段牆鐘，交給 summarize 算系統總吞吐。
    """
    url = cfg.base_url.rstrip("/") + cfg.api_path
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    builder = cfg.body_builder or build_body              # None＝用內建通用版

    results = [None] * len(pairs)
    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, cfg.concurrency)) as ex:
        fut_map = {}
        for i, (prompt, answer) in enumerate(pairs):
            body = builder(prompt, model=cfg.model, max_tokens=cfg.max_tokens,
                           temperature=cfg.temperature, stream=cfg.stream, reasoning=cfg.reasoning)
            fut = ex.submit(call, url, headers, body, stream=cfg.stream,
                            response_parser=cfg.response_parser, timeout=cfg.timeout)
            fut_map[fut] = (i, prompt, answer)
        done = 0
        for fut in as_completed(fut_map):
            i, prompt, answer = fut_map[fut]
            try:
                r = fut.result()
            except Exception as exc:                     # 理論上 call 不拋；保險起見
                r = RequestResult(ts=time.time(), success=False,
                                  error=f"{type(exc).__name__}: {exc}")
            r.scenario = cfg.scenario
            r.run_label = cfg.run_label
            r.index = i
            r.concurrency = cfg.concurrency
            r.answer = answer
            r.input_text = str(prompt)
            results[i] = r
            done += 1
            if cfg.progress and sys.stderr.isatty():
                print(f"\r  進度 {done}/{len(pairs)}", end="", file=sys.stderr, flush=True)
    if cfg.progress and pairs and sys.stderr.isatty():
        print("", file=sys.stderr)                       # 換行收尾
    return results, time.perf_counter() - wall_start
