"""執行引擎：單發與並發。

只依賴 adapter 的 `call()` 契約。負責替每筆結果蓋上 scenario / run_label / index /
concurrency（量測欄位由 adapter 填）。並發採 ThreadPoolExecutor，配合阻塞式 requests 最直觀。
"""
from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.metrics import RequestResult


def _failed(exc) -> RequestResult:
    """把未預期的例外包成一筆 failed 結果，避免單筆拖垮整批。"""
    return RequestResult(success=False, error=f"{type(exc).__name__}: {exc}")


def _stamp(r, scenario, run_label, index, concurrency):
    r.scenario = scenario
    r.run_label = run_label
    r.index = index
    r.concurrency = concurrency
    return r


def run_single(adapter, inputs, *, scenario="", run_label="",
               max_tokens=256, temperature=0.0, stream=True) -> list:
    """併發=1，序列逐筆執行。回傳 list[RequestResult]。"""
    results = []
    for i, inp in enumerate(inputs):
        try:
            r = adapter.call(inp, max_tokens=max_tokens, temperature=temperature, stream=stream)
        except Exception as exc:        # adapter 未攔下的例外不應中斷整批
            r = _failed(exc)
        results.append(_stamp(r, scenario, run_label, i, 1))
    return results


def run_concurrent(adapter, inputs, concurrency, *, scenario="", run_label="",
                   max_tokens=256, temperature=0.0, stream=True,
                   arrival="closed", rate=None):
    """並發執行。回傳 (list[RequestResult], wall_seconds)。

    arrival:
      - "closed"：閉環，一次最多 `concurrency` 筆在跑（量最大吞吐/容量用，預設）。
      - "poisson"：以指數分布間隔送出（rate 為每秒請求數），模擬隨機到達。
    """
    n = len(inputs)
    results = [None] * n
    wall_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {}
        if arrival == "poisson" and rate and rate > 0:
            # 主執行緒依指數間隔逐筆送出，pool 限制同時在跑的數量
            for i, inp in enumerate(inputs):
                fut = ex.submit(adapter.call, inp, max_tokens=max_tokens,
                                temperature=temperature, stream=stream)
                futures[fut] = i
                time.sleep(random.expovariate(rate))
        else:
            for i, inp in enumerate(inputs):
                fut = ex.submit(adapter.call, inp, max_tokens=max_tokens,
                                temperature=temperature, stream=stream)
                futures[fut] = i

        for fut in as_completed(futures):
            i = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:    # 單筆例外記為 failed，保住其餘結果
                r = _failed(exc)
            results[i] = _stamp(r, scenario, run_label, i, concurrency)

    wall_seconds = time.perf_counter() - wall_start
    return results, wall_seconds
