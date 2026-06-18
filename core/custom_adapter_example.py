"""自訂服務 adapter 範本（對號入座）。

當既有服務不是 OpenAI 相容、也不是單純 JSON（例如自訂串流格式、多欄位回應、
特殊 header/簽章驗證）時，複製這支、改 3 個 TODO 即可接上整套測試框架——
runner / metrics / reporter / 情境腳本一行都不用動。

啟用方式：.env 設 `ADAPTER=custom`（make_adapter 會載入此處的 CustomAdapter）。
"""
from __future__ import annotations

import json
import time

import requests

from core.metrics import RequestResult


class CustomAdapter:
    """實作唯一契約：call(payload, *, max_tokens, temperature, stream) -> RequestResult。"""

    def __init__(self, config):
        # 需要哪些設定就從 config 取（可重用既有欄位或自行新增到 config.py / .env）
        self.url = config.GENERIC_URL or config.BASE_URL
        self.timeout = config.REQUEST_TIMEOUT
        self.headers = {"Content-Type": "application/json"}
        if config.API_KEY:
            # TODO(1): 換成你服務的驗證方式（Bearer / 自訂簽章 / API-Key header…）
            self.headers["Authorization"] = f"Bearer {config.API_KEY}"

    def call(self, payload, *, max_tokens: int = 256, temperature: float = 0.0,
             stream: bool = False) -> RequestResult:
        r = RequestResult(ts=time.time())
        t0 = time.perf_counter()
        try:
            # TODO(2): 依你服務的格式組請求
            body = {"input": str(payload), "max_tokens": max_tokens}

            resp = requests.post(self.url, json=body, headers=self.headers,
                                 timeout=self.timeout, stream=stream)
            r.status_code = resp.status_code
            resp.raise_for_status()

            # TODO(3): 依你服務的回應取出輸出文字；若為串流，於收到首個 chunk 時記 TTFT：
            #   first = time.perf_counter(); r.ttft_ms = (first - t0) * 1000
            obj = resp.json()
            text = obj.get("output", "")

            t_end = time.perf_counter()
            r.success = True
            r.e2e_s = t_end - t0
            r.output_chars = len(text)
            if r.e2e_s and r.e2e_s > 0:
                r.chars_per_s = r.output_chars / r.e2e_s
            # 原文（供人工檢視；前三者進 CSV，raw_response 只進 JSON 明細）：
            r.input_text = str(payload)
            r.reasoning_text = obj.get("reasoning_content", "")  # 若你的服務有「思考內容」欄位
            r.output_text = text
            r.raw_response = json.dumps(obj, ensure_ascii=False)
        except (requests.RequestException, ValueError) as exc:
            r.success = False
            r.error = f"{type(exc).__name__}: {exc}"
        return r
