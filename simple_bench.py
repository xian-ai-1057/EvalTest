#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最簡化版 OpenAI 相容 chat 跑分（薄入口：只放參數設定區與線性主流程）。

機制拆在 core/ 各模組：dataset（輸入）、payload（組 body）、client（呼叫）、runner（並發）、
metrics（指標）、reporter（輸出）。直接執行即可（不使用指令列 args、不依賴 .env）：

    python simple_bench.py

要算準確率時，把帶正解的資料集（CSV：question + answer）填到設定區的 DATASET，跑完每筆的
正解會一併寫進輸出（JSON 的 detail / Excel 明細頁的 answer 欄），再用 accuracy.py 比對。
"""
from __future__ import annotations

import os
import sys
import time

# 讓 `python simple_bench.py` 直接執行時能 import 到專案根目錄的 core
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.config import BenchConfig                          # noqa: E402
from core.dataset import build_pairs                         # noqa: E402
from core.metrics import summarize                           # noqa: E402
from core.reporter import print_summary, write_outputs       # noqa: E402
from core.runner import run_benchmark                         # noqa: E402


# ===== 參數設定區（改這裡就好；執行：python simple_bench.py）=====
BASE_URL = "http://127.0.0.1:8000"    # 伺服器根；自動接 API_PATH
API_PATH = "/v1/chat/completions"     # 端點路徑（接在 BASE_URL 後）；打非 OpenAI 服務時改這個，如 /api/classify
MODEL = "test-model"                  # 模型名稱
API_KEY = ""                          # 需要時填 Bearer token
RUN_LABEL = "H100-FP8"                # 報表標籤（跨卡比較用，如 H100-FP8 / PRO6000-FP4）
N_REQUESTS = 20                       # 請求數
CONCURRENCY = 4                       # 並發數（1＝單發）
MAX_TOKENS = 256                      # 輸出長度上限
TEMPERATURE = 0.0                     # 取樣溫度
STREAM = True                         # True 量 TTFT/TPOT；False 只量端到端 e2e
REASONING = True                      # 思考模式開關 → 請求 body 的 chat_template_kwargs.enable_thinking；False 關閉思考
INPUT_LEN = 512                       # 合成輸入字元長度（DATASET 留空時用）
DATASET = ""                          # 留空＝合成輸入；或填 CSV/TXT 路徑（CSV 可含 question+answer）
OUTPUT_DIR = "results"                # 報表輸出資料夾
REQUEST_TIMEOUT = 60.0                # 單請求逾時（秒）
# 自訂請求 body：None＝用內建通用版 core.payload.build_body（OpenAI 相容）。
# 打 body 結構不同的服務時，指定自己的函式，簽章需與 build_body 相同：
#   def my_body(prompt, *, model, max_tokens, temperature, stream, reasoning) -> dict
# 是否串流由 STREAM 決定；你的 body["stream"] 請跟著傳入的 stream 參數設。
BODY_BUILDER = None
# 自訂回應解析：None＝照 OpenAI 解（choices[].message.content、usage）。打回應非 OpenAI 格式的
# 服務時指定函式：收到原始 requests.Response，回傳 dict（至少 output_text，選填 output_tokens /
# reasoning_text）。僅適用非串流（STREAM=False），量端到端 e2e（TTFT/TPOT 留空）。
RESPONSE_PARSER = None
# ===============================================================

SCENARIO = "simple_bench"


def main():
    cfg = BenchConfig(
        base_url=BASE_URL, api_path=API_PATH, model=MODEL, api_key=API_KEY,
        run_label=RUN_LABEL, scenario=SCENARIO, concurrency=CONCURRENCY,
        max_tokens=MAX_TOKENS, temperature=TEMPERATURE, stream=STREAM,
        reasoning=REASONING, timeout=REQUEST_TIMEOUT,
        body_builder=BODY_BUILDER, response_parser=RESPONSE_PARSER,
        n_requests=N_REQUESTS, input_len=INPUT_LEN, dataset=DATASET, output_dir=OUTPUT_DIR,
    )
    os.makedirs(cfg.output_dir, exist_ok=True)

    # 管線：建立輸入 → 並發跑分 → 統計 → 主控台摘要 → 寫報表（JSON dict + Excel）
    pairs = build_pairs(dataset=cfg.dataset, n_requests=cfg.n_requests, input_len=cfg.input_len)
    print(f"對 {cfg.base_url}{cfg.api_path} 發 {len(pairs)} 筆請求"
          f"（並發 {cfg.concurrency}，{'串流' if cfg.stream else '非串流'}，"
          f"思考 {'開' if cfg.reasoning else '關'}，模型 {cfg.model}）…")

    results, wall = run_benchmark(pairs, cfg)

    summary = summarize(results, wall_seconds=wall)
    print_summary(summary)

    ts = time.strftime("%Y%m%d-%H%M%S")
    label = (cfg.run_label or "run").replace("/", "_").replace(" ", "")
    base_path = os.path.join(cfg.output_dir, f"simple_{label}_{ts}.xlsx")
    params = cfg.as_params(actual_requests=len(pairs), ts=ts)
    xlsx_path, json_path = write_outputs(results, summary, base_path, params=params)
    print(f"已輸出：\n  Excel：{xlsx_path}\n  JSON ：{json_path}")


if __name__ == "__main__":
    main()
