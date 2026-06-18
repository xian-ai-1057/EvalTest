"""集中設定：從 .env 讀取並轉成型別化的 Config。

刻意零外部相依（不使用 python-dotenv），手寫極簡 .env parser，保持「簡潔易懂」。
真實環境變數（os.environ）優先於 .env 檔，方便 CI 或臨時覆寫。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent / ".env"


def _load_env_file(path: Path) -> dict:
    """解析 KEY=VALUE 形式的 .env；忽略空行與 # 註解，去掉外層引號。"""
    data: dict = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        data[key] = val
    return data


_FILE_ENV = _load_env_file(_ENV_PATH)


def _raw(key: str, default: str = "") -> str:
    env_val = os.environ.get(key)
    if env_val:  # 非空字串才視為覆寫
        return env_val
    return _FILE_ENV.get(key, default)


def _as_int(key: str, default: int) -> int:
    try:
        return int(_raw(key, str(default)))
    except ValueError:
        return default


def _as_float(key: str, default: float) -> float:
    try:
        return float(_raw(key, str(default)))
    except ValueError:
        return default


def _as_bool(key: str, default: bool) -> bool:
    val = _raw(key, str(default)).strip().lower()
    return val in ("1", "true", "yes", "y", "on")


def _as_int_list(key: str, default: list) -> list:
    raw = _raw(key, "")
    if not raw:
        return list(default)
    out = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                pass
    return out or list(default)


def _as_json(key: str, default):
    raw = _raw(key, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


@dataclass
class Config:
    # --- adapter / 連線 ---
    ADAPTER: str = "openai_chat"          # openai_chat | generic_json | custom
    BASE_URL: str = "http://127.0.0.1:8000"  # 伺服器根，程式會自動接 /v1/chat/completions
    API_KEY: str = ""
    MODEL: str = "test-model"
    REQUEST_TIMEOUT: float = 60.0

    # --- 執行標籤（寫進每一筆，用來區分 H100/PRO6000 與精度）---
    RUN_LABEL: str = "unlabeled"          # 例：H100-FP8 / PRO6000-FP4

    # --- 請求內容 ---
    INPUT_LEN: int = 512                  # 構造輸入 prompt 的字元長度
    MAX_TOKENS: int = 256                 # 期望輸出長度
    TEMPERATURE: float = 0.0

    # --- 執行規模 ---
    N_REQUESTS: int = 20
    CONCURRENCY_LEVELS: list = field(default_factory=lambda: [1, 8, 16, 32, 64, 128])

    # --- SLA 門檻（由 PM/業務填，用於判斷併發是否達標）---
    SLA_TTFT_MS: float = 1000.0
    SLA_P95_MS: float = 5000.0            # 端到端 P95 上限（毫秒）

    # --- GPU 監控 ---
    GPU_MONITOR: bool = False
    GPU_SAMPLE_INTERVAL: float = 0.5

    # --- 輸出 ---
    OUTPUT_DIR: str = "results"
    COUNT_UNIT: str = "token"             # token | char（決定摘要主指標，兩者都會記錄）

    # --- 通用 JSON adapter（情境 ④/⑥ 或既有自訂服務）---
    GENERIC_URL: str = ""
    GENERIC_REQUEST_TEMPLATE: str = '{"input": "{input}"}'
    GENERIC_RESPONSE_PATH: str = "output"  # 點路徑，如 data.output 或 choices.0.text
    GENERIC_HEADERS: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        return cls(
            ADAPTER=_raw("ADAPTER", cls.ADAPTER),
            BASE_URL=_raw("BASE_URL", cls.BASE_URL),
            API_KEY=_raw("API_KEY", cls.API_KEY),
            MODEL=_raw("MODEL", cls.MODEL),
            REQUEST_TIMEOUT=_as_float("REQUEST_TIMEOUT", cls.REQUEST_TIMEOUT),
            RUN_LABEL=_raw("RUN_LABEL", cls.RUN_LABEL),
            INPUT_LEN=_as_int("INPUT_LEN", cls.INPUT_LEN),
            MAX_TOKENS=_as_int("MAX_TOKENS", cls.MAX_TOKENS),
            TEMPERATURE=_as_float("TEMPERATURE", cls.TEMPERATURE),
            N_REQUESTS=_as_int("N_REQUESTS", cls.N_REQUESTS),
            CONCURRENCY_LEVELS=_as_int_list("CONCURRENCY_LEVELS", [1, 8, 16, 32, 64, 128]),
            SLA_TTFT_MS=_as_float("SLA_TTFT_MS", cls.SLA_TTFT_MS),
            SLA_P95_MS=_as_float("SLA_P95_MS", cls.SLA_P95_MS),
            GPU_MONITOR=_as_bool("GPU_MONITOR", cls.GPU_MONITOR),
            GPU_SAMPLE_INTERVAL=_as_float("GPU_SAMPLE_INTERVAL", cls.GPU_SAMPLE_INTERVAL),
            OUTPUT_DIR=_raw("OUTPUT_DIR", cls.OUTPUT_DIR),
            COUNT_UNIT=_raw("COUNT_UNIT", cls.COUNT_UNIT),
            GENERIC_URL=_raw("GENERIC_URL", cls.GENERIC_URL),
            GENERIC_REQUEST_TEMPLATE=_raw("GENERIC_REQUEST_TEMPLATE", cls.GENERIC_REQUEST_TEMPLATE),
            GENERIC_RESPONSE_PATH=_raw("GENERIC_RESPONSE_PATH", cls.GENERIC_RESPONSE_PATH),
            GENERIC_HEADERS=_as_json("GENERIC_HEADERS", {}),
        )


if __name__ == "__main__":
    # 方便快速檢查設定是否正確載入
    cfg = Config.load()
    for k, v in cfg.__dict__.items():
        shown = v
        if k == "API_KEY" and v:
            shown = "***"
        print(f"{k:24} = {shown}")
