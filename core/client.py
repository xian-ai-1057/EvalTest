# -*- coding: utf-8 -*-
"""HTTP 呼叫層：拿到「已組好的 body」就發請求並解碼，回 RequestResult。

職責單純——只負責「呼叫 ＋ 解碼」：不組 body、不拼 URL/headers、不 stamp 輸入
（URL/headers/body 由 runner 準備，index/answer/input_text 由 runner stamp）。
串流（stream=True）量 TTFT/TPOT；非串流只量端到端 e2e，並可用 response_parser 解析非 OpenAI 回應。
"""
from __future__ import annotations

import json
import time

import requests

from core.metrics import RequestResult


def _rate(n, seconds):
    if not n or not seconds or seconds <= 0:
        return None
    return n / seconds


def call(url, headers, body, *, stream=True, response_parser=None, timeout=60.0) -> RequestResult:
    """對 url 發一次請求（body 已由呼叫端組好），回量測結果 RequestResult。

    串流量 TTFT/TPOT；非串流只量端到端 e2e（可帶 response_parser 解析非 OpenAI 格式）。
    任何連線 / HTTP / JSON 解析錯誤都記為 success=False、填 error，不拋出（不讓單筆拖垮整批）。
    注意：input_text 由 runner stamp，這裡不設。
    """
    r = RequestResult(ts=time.time())
    t0 = time.perf_counter()
    try:
        if stream:
            _decode_stream(url, headers, body, t0, r, timeout)
        else:
            _decode_once(url, headers, body, t0, r, timeout, response_parser=response_parser)
    except (requests.RequestException, ValueError) as exc:
        r.success = False
        r.error = f"{type(exc).__name__}: {exc}"
    return r


def _decode_stream(url, headers, body, t0, r: RequestResult, timeout):
    """SSE 串流：逐塊累積思考 / 內容，量首字（TTFT）與逐字（TPOT）延遲。"""
    text_parts, reason_parts = [], []
    meta, usage_obj, finish_reason = {}, None, None
    t_first = t_last = None
    n_chunks = 0
    usage_tokens = None
    with requests.post(url, json=body, headers=headers, timeout=timeout, stream=True) as resp:
        r.status_code = resp.status_code
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            for k in ("id", "created", "model", "system_fingerprint"):
                if k not in meta and chunk.get(k) is not None:
                    meta[k] = chunk[k]
            if chunk.get("usage"):                       # usage 可能單獨出現在末包
                usage_obj = chunk["usage"]
                usage_tokens = usage_obj.get("completion_tokens")
            choices = chunk.get("choices") or []
            if not choices:
                continue
            if choices[0].get("finish_reason"):
                finish_reason = choices[0]["finish_reason"]
            delta = choices[0].get("delta") or {}
            reason_piece = delta.get("reasoning_content")
            if reason_piece is None:
                reason_piece = delta.get("reasoning")
            piece = delta.get("content")
            if reason_piece:
                reason_parts.append(reason_piece)
            if piece:
                text_parts.append(piece)
            if reason_piece or piece:                    # 思考與內容皆算「已生成輸出」
                now = time.perf_counter()
                if t_first is None:
                    t_first = now
                t_last = now
                n_chunks += 1
    t_end = time.perf_counter()

    reasoning = "".join(reason_parts)
    text = "".join(text_parts)
    r.success = True
    r.e2e_s = t_end - t0
    r.output_chars = len(reasoning) + len(text)
    r.output_tokens = usage_tokens if usage_tokens is not None else n_chunks
    r.reasoning_text = reasoning
    r.output_text = text
    message = {"role": "assistant", "content": text}
    if reasoning:
        message["reasoning_content"] = reasoning
    final = dict(meta)
    final["object"] = "chat.completion"
    final["choices"] = [{"index": 0, "message": message, "finish_reason": finish_reason}]
    if usage_obj is not None:
        final["usage"] = usage_obj
    r.raw_response = json.dumps(final, ensure_ascii=False)
    if t_first is not None:
        r.ttft_ms = (t_first - t0) * 1000.0
        if n_chunks > 1 and t_last > t_first:            # 多數後端 1 token/chunk，近似逐字延遲
            r.tpot_ms = (t_last - t_first) / (n_chunks - 1) * 1000.0
    decode_s = None                                      # 單請求速率以解碼階段（扣首字）計
    if r.ttft_ms is not None and r.e2e_s is not None:
        decode_s = r.e2e_s - r.ttft_ms / 1000.0
    decode_s = decode_s if (decode_s and decode_s > 0) else r.e2e_s
    r.tokens_per_s = _rate(r.output_tokens, decode_s)
    r.chars_per_s = _rate(r.output_chars, decode_s)


def _decode_once(url, headers, body, t0, r: RequestResult, timeout, response_parser=None):
    """非串流：單發取回完整回應，只量端到端 e2e（無法量 TTFT/TPOT）。

    response_parser=None 照 OpenAI 解（choices[].message.content、usage）；指定時改用它解析
    回應非 OpenAI 格式的服務 —— 收到原始 requests.Response，回傳 dict（至少 output_text，
    選填 output_tokens / reasoning_text）。
    """
    resp = requests.post(url, json=body, headers=headers, timeout=timeout)
    r.status_code = resp.status_code
    resp.raise_for_status()
    t_end = time.perf_counter()
    if response_parser is not None:                      # 自訂服務：整包交給使用者解析
        parsed = response_parser(resp) or {}
        text = str(parsed.get("output_text", "") or "")
        reasoning = str(parsed.get("reasoning_text", "") or "")
        r.output_tokens = parsed.get("output_tokens")
        r.raw_response = resp.text
    else:                                                # 通用版：OpenAI 格式
        obj = resp.json()
        text = reasoning = ""
        choices = obj.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            text = msg.get("content", "") or ""
            reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
        r.output_tokens = (obj.get("usage") or {}).get("completion_tokens")
        r.raw_response = json.dumps(obj, ensure_ascii=False)
    r.success = True
    r.e2e_s = t_end - t0
    r.output_chars = len(reasoning) + len(text)
    r.reasoning_text = reasoning
    r.output_text = text
    r.tokens_per_s = _rate(r.output_tokens, r.e2e_s)
    r.chars_per_s = _rate(r.output_chars, r.e2e_s)
