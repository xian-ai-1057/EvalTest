"""模型呼叫接口（adapter）。

唯一接縫：所有 adapter 都實作 `call(payload, *, max_tokens, temperature, stream) -> RequestResult`，
runner / metrics / reporter 只認得這個契約，不在意底層 HTTP 細節。

提供：
  - OpenAIChatAdapter：OpenAI 相容 /v1/chat/completions，支援 SSE 串流量 TTFT/TPOT。
  - GenericJSONAdapter：可設定的單純 JSON 服務（情境 ④/⑥ 或既有自訂服務），純設定即可接。
  - make_adapter(config)：依 config.ADAPTER 選用（openai_chat / generic_json / custom）。
"""
from __future__ import annotations

import json
import time
from typing import Optional

import requests

from core.metrics import RequestResult


def _rate(n: Optional[int], seconds: Optional[float]) -> Optional[float]:
    if not n or not seconds or seconds <= 0:
        return None
    return n / seconds


class OpenAIChatAdapter:
    """OpenAI 相容 chat。串流模式下可量 TTFT/TPOT；非串流只量端到端。"""

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout: float = 60.0):
        self.url = base_url.rstrip("/") + "/v1/chat/completions"
        self.model = model
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    def _body(self, payload, max_tokens: int, temperature: float, stream: bool) -> dict:
        if isinstance(payload, list):           # 已是 messages
            messages = payload
        else:
            messages = [{"role": "user", "content": str(payload)}]
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if stream:
            # 要求伺服器在末包附 usage（vLLM/TGI 支援），用以取得精確 token 數
            body["stream_options"] = {"include_usage": True}
        return body

    def call(self, payload, *, max_tokens: int = 256, temperature: float = 0.0,
             stream: bool = True) -> RequestResult:
        r = RequestResult(ts=time.time())
        t0 = time.perf_counter()
        try:
            if stream:
                self._call_stream(payload, max_tokens, temperature, t0, r)
            else:
                self._call_once(payload, max_tokens, temperature, t0, r)
        except requests.RequestException as exc:
            r.success = False
            r.error = f"{type(exc).__name__}: {exc}"
        return r

    def _call_stream(self, payload, max_tokens, temperature, t0, r: RequestResult):
        body = self._body(payload, max_tokens, temperature, stream=True)
        text_parts = []
        t_first = None
        t_last = None
        n_chunks = 0
        usage_tokens = None
        with requests.post(self.url, json=body, headers=self.headers,
                           timeout=self.timeout, stream=True) as resp:
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
                # usage 可能單獨出現在末包（choices 為空）
                if chunk.get("usage"):
                    usage_tokens = chunk["usage"].get("completion_tokens")
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    now = time.perf_counter()
                    if t_first is None:
                        t_first = now
                    t_last = now
                    n_chunks += 1
                    text_parts.append(piece)
        t_end = time.perf_counter()

        text = "".join(text_parts)
        r.success = True
        r.e2e_s = t_end - t0
        r.output_chars = len(text)
        r.output_tokens = usage_tokens if usage_tokens is not None else n_chunks
        if t_first is not None:
            r.ttft_ms = (t_first - t0) * 1000.0
            if n_chunks > 1 and t_last > t_first:
                r.tpot_ms = (t_last - t_first) / (n_chunks - 1) * 1000.0
        # 單請求輸出速率：以解碼階段（扣掉首字延遲）計算，對應 AC2
        decode_s = None
        if r.ttft_ms is not None and r.e2e_s is not None:
            decode_s = r.e2e_s - r.ttft_ms / 1000.0
        decode_s = decode_s if (decode_s and decode_s > 0) else r.e2e_s
        r.tokens_per_s = _rate(r.output_tokens, decode_s)
        r.chars_per_s = _rate(r.output_chars, decode_s)

    def _call_once(self, payload, max_tokens, temperature, t0, r: RequestResult):
        body = self._body(payload, max_tokens, temperature, stream=False)
        resp = requests.post(self.url, json=body, headers=self.headers, timeout=self.timeout)
        r.status_code = resp.status_code
        resp.raise_for_status()
        t_end = time.perf_counter()
        obj = resp.json()
        text = ""
        choices = obj.get("choices") or []
        if choices:
            text = (choices[0].get("message") or {}).get("content", "") or ""
        usage = obj.get("usage") or {}
        r.success = True
        r.e2e_s = t_end - t0
        r.output_chars = len(text)
        r.output_tokens = usage.get("completion_tokens")
        # 非串流無法量首字/逐字延遲
        r.tokens_per_s = _rate(r.output_tokens, r.e2e_s)
        r.chars_per_s = _rate(r.output_chars, r.e2e_s)


def _dig(obj, path: str):
    """依點路徑取值，支援數字索引。如 'data.output' 或 'choices.0.text'。"""
    if not path:
        return obj
    cur = obj
    for key in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(key)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return None
    return cur


class GenericJSONAdapter:
    """單純自訂 JSON 服務：送一段輸入、回一段文字/標籤，不串流。

    純設定即可接：
      - url：端點
      - request_template：JSON 文字，含 {input}（與選用的 {max_tokens}）佔位符
      - response_path：從回應取出輸出的點路徑
    僅量端到端延遲（TTFT/TPOT 不適用）。
    """

    def __init__(self, url: str, request_template: str = '{"input": "{input}"}',
                 response_path: str = "output", headers: Optional[dict] = None,
                 timeout: float = 60.0):
        self.url = url
        self.request_template = request_template
        self.response_path = response_path
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.timeout = timeout

    def _build_body(self, payload, max_tokens: int) -> dict:
        # 用 json.dumps 取得安全跳脫後的字串內容（去掉外層引號）再代入模板
        escaped = json.dumps(str(payload))[1:-1]
        text = self.request_template.replace("{input}", escaped)
        text = text.replace("{max_tokens}", str(max_tokens))
        return json.loads(text)

    def call(self, payload, *, max_tokens: int = 256, temperature: float = 0.0,
             stream: bool = False) -> RequestResult:
        r = RequestResult(ts=time.time())
        t0 = time.perf_counter()
        try:
            body = self._build_body(payload, max_tokens)
            resp = requests.post(self.url, json=body, headers=self.headers, timeout=self.timeout)
            r.status_code = resp.status_code
            resp.raise_for_status()
            t_end = time.perf_counter()
            out = _dig(resp.json(), self.response_path)
            text = "" if out is None else (out if isinstance(out, str) else json.dumps(out, ensure_ascii=False))
            r.success = True
            r.e2e_s = t_end - t0
            r.output_chars = len(text)
            r.chars_per_s = _rate(r.output_chars, r.e2e_s)
        except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
            r.success = False
            r.error = f"{type(exc).__name__}: {exc}"
        return r


def make_adapter(config):
    """依 config.ADAPTER 建立 adapter。新增自訂協定時，於此登記名稱即可。"""
    name = (config.ADAPTER or "openai_chat").strip().lower()
    if name == "openai_chat":
        return OpenAIChatAdapter(
            base_url=config.BASE_URL, model=config.MODEL,
            api_key=config.API_KEY, timeout=config.REQUEST_TIMEOUT,
        )
    if name == "generic_json":
        if not config.GENERIC_URL:
            raise ValueError("ADAPTER=generic_json 需設定 GENERIC_URL")
        return GenericJSONAdapter(
            url=config.GENERIC_URL,
            request_template=config.GENERIC_REQUEST_TEMPLATE,
            response_path=config.GENERIC_RESPONSE_PATH,
            headers=config.GENERIC_HEADERS,
            timeout=config.REQUEST_TIMEOUT,
        )
    if name == "custom":
        # 延遲匯入，避免未使用時也要求範本可載入
        from core.custom_adapter_example import CustomAdapter
        return CustomAdapter(config)
    raise ValueError(f"未知的 ADAPTER：{config.ADAPTER}")
