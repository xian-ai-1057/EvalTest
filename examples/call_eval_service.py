#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""呼叫範例：打「非 OpenAI」的客服評分服務（自訂端點 + 自訂 body + 自訂回應解析）。

這個服務吃的請求與回應都跟 OpenAI 完全不同：
  - 請求 body：{"pid", "text", "outgoing_call", "call_type", "develop_mode"}（text＝要評分的逐字稿）
  - 回應    ：一個 JSON 陣列，每個代號一筆評分（Column1=代號、Column2=合格判定、Column3=項目、
              Service_status…）

用 simple_bench 重構後的三個掛勾打它（全在 BenchConfig 帶下去）：
  - api_path        → 服務端點路徑（★ 改成你的真實路徑）
  - body_builder    → build_eval_body   （整包換成這個服務的 body）
  - response_parser → parse_eval_response（把陣列回應抽成量測欄位）

非串流，只量端到端 e2e（這類服務量不到 TTFT/TPOT）。output_tokens 留空 → 吞吐以 chars/s 為主。
直接執行即可（會用下方設定區的 BASE_URL / API_PATH）：

    python examples/call_eval_service.py
"""
from __future__ import annotations

import json
import os
import sys
import time

# 讓 `python examples/call_eval_service.py` 直接執行時 import 得到專案根的 core
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.config import BenchConfig                          # noqa: E402
from core.metrics import summarize                           # noqa: E402
from core.reporter import print_summary, write_outputs       # noqa: E402
from core.runner import run_benchmark                         # noqa: E402


# ===== 參數設定區（改這裡就好；執行：python examples/call_eval_service.py）=====
BASE_URL = "http://127.0.0.1:8000"   # 服務根；自動接 API_PATH
API_PATH = "/api/eval"               # ★ 改成你的真實端點路徑（這裡用 mock_server 的測試端點）
RUN_LABEL = "EVAL-SVC"               # 報表標籤
N_REQUESTS = 10                      # 請求數
CONCURRENCY = 4                      # 並發數
REQUEST_TIMEOUT = 60.0               # 單請求逾時（秒）
OUTPUT_DIR = "results"               # 報表輸出資料夾

# 這個服務 body 的固定欄位（text 由每筆 prompt 帶入；下列為常數，視需要改）
PID = "XXXXXX"
OUTGOING_CALL = "否"
CALL_TYPE = "代號1^代號2"             # 多個代號以 "^" 串接；回應會一個代號一筆
DEVELOP_MODE = "False"               # 註：原始範例的 key 寫成 "develop_mode "（含尾端空白），這裡用乾淨的 develop_mode
# ============================================================================


def build_eval_body(prompt, *, model, max_tokens, temperature, stream, reasoning):
    """組這個評分服務的請求 body（整包非 OpenAI 格式）。text＝每筆要評分的逐字稿。

    簽章需與 core.payload.build_body 一致（runner 會用這些 kwargs 呼叫），但這個服務只用得到
    prompt；model / max_tokens / temperature / stream / reasoning 都用不到，忽略即可。
    """
    return {
        "pid": PID,
        "text": str(prompt),
        "outgoing_call": OUTGOING_CALL,
        "call_type": CALL_TYPE,
        "develop_mode": DEVELOP_MODE,
    }


def parse_eval_response(resp):
    """解析評分服務的回應（一個 JSON 陣列）。收到原始 requests.Response，回傳量測欄位 dict。

    output_text 存整包陣列的 JSON 字串（lossless、可在明細/JSON 直接檢視；完整原文另由 client
    保留在 raw_response）。服務不回 token 數，故 output_tokens=None（吞吐以字元數為準）。
    """
    arr = resp.json()                                       # 預期為 list[dict]，每個代號一筆
    return {
        "output_text": json.dumps(arr, ensure_ascii=False),
        "output_tokens": None,
    }


def _sample_pairs(n):
    """造 n 筆合成逐字稿當輸入（answer 留空）。要打真資料就改用 dataset：把 CSV 的 text 欄
    讀進來（見 core.dataset.load_dataset），或在這裡換成自己的 list。"""
    return [(f"客戶 0.0 4.83 XXXXXX坐席: 1.0 5.0 樣本{i:03d}", "") for i in range(n)]


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    cfg = BenchConfig(
        base_url=BASE_URL, api_path=API_PATH, model="(n/a)",
        run_label=RUN_LABEL, scenario="call_eval_service",
        concurrency=CONCURRENCY, timeout=REQUEST_TIMEOUT,
        stream=False,                                       # 自訂回應只量端到端 e2e
        body_builder=build_eval_body, response_parser=parse_eval_response,
        n_requests=N_REQUESTS, output_dir=OUTPUT_DIR,
    )

    pairs = _sample_pairs(N_REQUESTS)
    print(f"對 {cfg.base_url}{cfg.api_path} 發 {len(pairs)} 筆評分請求（非串流，量 e2e）…")
    results, wall = run_benchmark(pairs, cfg)

    summary = summarize(results, wall_seconds=wall)
    print_summary(summary)

    ts = time.strftime("%Y%m%d-%H%M%S")
    base_path = os.path.join(OUTPUT_DIR, f"eval_{RUN_LABEL}_{ts}.xlsx")
    params = cfg.as_params(actual_requests=len(pairs), ts=ts)
    xlsx_path, json_path = write_outputs(results, summary, base_path, params=params)
    print(f"已輸出：\n  Excel：{xlsx_path}\n  JSON ：{json_path}")


if __name__ == "__main__":
    main()
