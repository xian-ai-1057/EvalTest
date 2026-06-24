# -*- coding: utf-8 -*-
"""執行設定：把一次跑分的所有參數收進一個 dataclass，沿管線傳遞（取代一長串 kwargs）。

simple_bench.py 的「參數設定區」仍是唯一調整入口；那些 Python 變數在 main() 收進 BenchConfig
後往下傳。as_params() 集中產生報表第③頁「執行參數」的列，避免「設定變數」與「參數列表」兩份重複。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class BenchConfig:
    """一次跑分的完整設定；預設值對齊 simple_bench 參數設定區。"""
    base_url: str
    model: str
    api_path: str = "/v1/chat/completions"
    api_key: str = ""
    run_label: str = ""
    scenario: str = ""
    concurrency: int = 1
    max_tokens: int = 256
    temperature: float = 0.0
    stream: bool = True
    reasoning: bool = True
    timeout: float = 60.0
    body_builder: Optional[Callable] = None       # None＝用通用版 core.payload.build_body
    response_parser: Optional[Callable] = None     # None＝照 OpenAI 解（僅非串流可自訂）
    progress: bool = True
    # 輸入與輸出（供 build_pairs 與報表參數頁，不影響單筆請求）
    n_requests: int = 0
    input_len: int = 0
    dataset: str = ""
    output_dir: str = "results"

    def as_params(self, *, actual_requests, ts) -> list:
        """本次執行參數（給報表第③頁；API_KEY 遮罩、builder/parser 存名稱字串以便序列化）。"""
        def _name(fn, default):
            return fn.__name__ if fn else default
        return [
            ("BASE_URL", self.base_url),
            ("API_PATH", self.api_path),
            ("MODEL", self.model),
            ("API_KEY", "***" if self.api_key else ""),
            ("RUN_LABEL", self.run_label),
            ("N_REQUESTS", self.n_requests),
            ("實際請求數", actual_requests),
            ("CONCURRENCY", self.concurrency),
            ("MAX_TOKENS", self.max_tokens),
            ("TEMPERATURE", self.temperature),
            ("STREAM", self.stream),
            ("REASONING", self.reasoning),
            ("BODY_BUILDER", _name(self.body_builder, "(通用版 build_body)")),
            ("RESPONSE_PARSER", _name(self.response_parser, "(OpenAI 預設)")),
            ("INPUT_LEN", self.input_len),
            ("DATASET", self.dataset or "(合成輸入)"),
            ("OUTPUT_DIR", self.output_dir),
            ("REQUEST_TIMEOUT", self.timeout),
            ("執行時間", ts),
        ]
