"""整合測試（G2 把關）：對假伺服器跑完整鏈路 adapter → runner → metrics → reporter。

驗證 AC1/AC2：
  - 產生對應筆數、全數成功；
  - 數值合理：TTFT < e2e、tokens_per_s ≈ output_tokens ÷ (e2e − ttft)；
  - 並發回傳 wall_seconds、總吞吐可算。
直接執行：python tests/test_integration.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.client import OpenAIChatAdapter, GenericJSONAdapter
from core.metrics import summarize
from core.reporter import print_summary
from core.runner import run_concurrent, run_single
from tests.mock_server import start_in_thread


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def main():
    httpd, port = start_in_thread(0)
    base = f"http://127.0.0.1:{port}"
    print(f"mock server @ {base}")
    try:
        adapter = OpenAIChatAdapter(base_url=base, model="mock", timeout=30)

        # --- 單發（AC1/AC2）---
        print("[單發] run_single n=10")
        results = run_single(adapter, ["hello"] * 10, scenario="it_single",
                             run_label="MOCK", max_tokens=12, stream=True)
        _check(len(results) == 10, "產生 10 筆")
        _check(all(r.success for r in results), "全部成功")
        r0 = results[0]
        _check(r0.ttft_ms is not None and r0.e2e_s is not None, "有 TTFT 與 e2e")
        _check(r0.ttft_ms / 1000.0 < r0.e2e_s, "TTFT < e2e")
        _check(r0.output_tokens and r0.output_tokens > 0, "有輸出 token 數（來自 usage）")
        # tokens_per_s ≈ output_tokens / (e2e - ttft)
        decode_s = r0.e2e_s - r0.ttft_ms / 1000.0
        expect = r0.output_tokens / decode_s
        _check(abs(r0.tokens_per_s - expect) < 1e-6, "tokens_per_s ≈ tokens ÷ (e2e − ttft)")
        print_summary(summarize(results))

        # --- 並發（AC3 雛形）---
        print("[並發] run_concurrent concurrency=8 n=24")
        cres, wall = run_concurrent(adapter, ["hi"] * 24, concurrency=8,
                                    scenario="it_concurrent", run_label="MOCK",
                                    max_tokens=12, stream=True)
        _check(len(cres) == 24 and all(r.success for r in cres), "並發 24 筆全部成功")
        _check(wall > 0, "回傳 wall_seconds")
        _check(all(r.concurrency == 8 for r in cres), "每筆標記 concurrency=8")
        summ = summarize(cres, wall_seconds=wall)
        _check(summ.throughput_tokens_per_s and summ.throughput_tokens_per_s > 0, "可算系統總吞吐")
        print_summary(summ)

        # --- GenericJSONAdapter（FR8/AC6）---
        print("[通用] GenericJSONAdapter -> /predict")
        gen = GenericJSONAdapter(url=f"{base}/predict",
                                 request_template='{"text": "{input}"}',
                                 response_path="output", timeout=30)
        gres = run_single(gen, ["客戶要查詢帳單"] * 3, scenario="it_generic",
                          run_label="MOCK", stream=False)
        _check(all(r.success for r in gres), "generic 全部成功")
        _check(gres[0].output_chars and gres[0].output_chars > 0, "generic 取到輸出文字")

        print("\n全部整合檢查通過 ✅")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
