"""量測資料契約與彙總。

`RequestResult` 是全鏈路（adapter → runner → reporter）流通的唯一單筆資料形狀；
`summarize()` 把多筆結果彙整成平均 / 百分位 / 總吞吐。只用標準庫 statistics。
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Optional


@dataclass
class RequestResult:
    """單一請求的量測結果。欄位宣告順序即 CSV 欄位順序。

    除了量測數值，亦保留「原文」供人工檢視：input_text（輸入原文）、reasoning_text
    （思考內容 / reasoning_content，無則空）、output_text（輸出內容）會寫進 CSV；
    raw_response（最後輸出結果的完整回應物件 JSON 字串；串流重組成與非串流一致的物件）因屬結構化內容、不適合表格，標記 csv=False，只寫進 JSON 明細檔。
    """
    scenario: str = ""
    run_label: str = ""
    index: int = 0
    ts: float = 0.0                      # 請求開始的 epoch 秒
    concurrency: int = 1
    success: bool = False
    status_code: int = 0
    ttft_ms: Optional[float] = None      # 首字回應延遲（毫秒）
    tpot_ms: Optional[float] = None      # 逐字生成延遲（毫秒/字）
    e2e_s: Optional[float] = None        # 端到端延遲（秒）
    output_tokens: Optional[int] = None
    output_chars: Optional[int] = None
    tokens_per_s: Optional[float] = None  # 單請求輸出速率（依解碼階段算，見 client）
    chars_per_s: Optional[float] = None
    error: str = ""
    # --- 原文（供人工檢視；前三者進 CSV，raw_response 只進 JSON 明細）---
    input_text: str = ""                 # 輸入原文
    reasoning_text: str = ""             # 思考內容（reasoning_content；不適用 / 無則空）
    output_text: str = ""                # 輸出內容（最終回覆文字）
    raw_response: str = field(default="", metadata={"csv": False})  # 完整原始回應（JSON 字串）


def field_names(include_raw: bool = False) -> list:
    """回傳 CSV 欄位名（依宣告順序）。

    預設略過標記 metadata={"csv": False} 的欄位（如完整原始回應 raw_response）——這類超長
    欄位只寫進 JSON 明細檔，避免 CSV 難以閱讀。include_raw=True 則回傳全部欄位。
    """
    return [f.name for f in fields(RequestResult)
            if include_raw or f.metadata.get("csv", True)]


def _percentile(values: list, q: float):
    """線性插值百分位。values 需為數值清單；空清單回 None。"""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * (q / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


@dataclass
class Stat:
    n: int = 0
    mean: Optional[float] = None
    p50: Optional[float] = None
    p90: Optional[float] = None
    p95: Optional[float] = None
    p99: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None


def _stat(values: list) -> Stat:
    vals = [v for v in values if v is not None]
    if not vals:
        return Stat()
    return Stat(
        n=len(vals),
        mean=sum(vals) / len(vals),
        p50=_percentile(vals, 50),
        p90=_percentile(vals, 90),
        p95=_percentile(vals, 95),
        p99=_percentile(vals, 99),
        minimum=min(vals),
        maximum=max(vals),
    )


@dataclass
class Summary:
    scenario: str = ""
    run_label: str = ""
    concurrency: Optional[int] = None
    count: int = 0
    success: int = 0
    failed: int = 0
    success_rate: float = 0.0
    wall_seconds: Optional[float] = None
    ttft_ms: Stat = field(default_factory=Stat)
    tpot_ms: Stat = field(default_factory=Stat)
    e2e_s: Stat = field(default_factory=Stat)
    tokens_per_s: Stat = field(default_factory=Stat)   # 單請求輸出速率
    chars_per_s: Stat = field(default_factory=Stat)
    total_output_tokens: int = 0
    total_output_chars: int = 0
    throughput_tokens_per_s: Optional[float] = None     # 系統總吞吐＝Σtokens ÷ wall
    throughput_chars_per_s: Optional[float] = None


def summarize(results: list, wall_seconds: Optional[float] = None) -> Summary:
    """把 RequestResult 清單彙整成 Summary。

    wall_seconds 由並發 runner 回傳；用來計算「系統總吞吐」。
    單發情境可不傳（總吞吐留空）。
    """
    ok = [r for r in results if r.success]
    concurrencies = {r.concurrency for r in results}
    total_tokens = sum(r.output_tokens or 0 for r in ok)
    total_chars = sum(r.output_chars or 0 for r in ok)

    summ = Summary(
        scenario=results[0].scenario if results else "",
        run_label=results[0].run_label if results else "",
        concurrency=(next(iter(concurrencies)) if len(concurrencies) == 1 else None),
        count=len(results),
        success=len(ok),
        failed=len(results) - len(ok),
        success_rate=(len(ok) / len(results)) if results else 0.0,
        wall_seconds=wall_seconds,
        ttft_ms=_stat([r.ttft_ms for r in ok]),
        tpot_ms=_stat([r.tpot_ms for r in ok]),
        e2e_s=_stat([r.e2e_s for r in ok]),
        tokens_per_s=_stat([r.tokens_per_s for r in ok]),
        chars_per_s=_stat([r.chars_per_s for r in ok]),
        total_output_tokens=total_tokens,
        total_output_chars=total_chars,
    )
    if wall_seconds and wall_seconds > 0:
        summ.throughput_tokens_per_s = total_tokens / wall_seconds
        summ.throughput_chars_per_s = total_chars / wall_seconds
    return summ
