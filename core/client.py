"""模型呼叫接口（adapter）。

唯一接縫：所有 adapter 都實作 `call(payload, *, max_tokens, temperature, stream) -> RequestResult`，
runner / metrics / reporter 只認得這個契約，不在意底層 HTTP 細節。

提供：
  - OpenAIChatAdapter：OpenAI 相容 /v1/chat/completions，支援 SSE 串流量 TTFT/TPOT。
  - GenericJSONAdapter：可設定的單純 JSON 服務（情境 ④/⑥ 或既有自訂服務），純設定即可接。
  - make_adapter(config)：依 config.ADAPTER 選用（openai_chat / generic_json / custom）。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path
from typing import Optional

import requests

from core.metrics import RequestResult


def _rate(n: Optional[int], seconds: Optional[float]) -> Optional[float]:
    if not n or not seconds or seconds <= 0:
        return None
    return n / seconds


def _payload_text(payload) -> str:
    """把輸入轉成可讀「原文」：messages 串列 / dict 轉 JSON 字串，其餘直接字串化。"""
    if isinstance(payload, (list, dict)):
        return json.dumps(payload, ensure_ascii=False)
    return str(payload)


class OpenAIChatAdapter:
    """OpenAI 相容 chat。串流量 TTFT/TPOT；非串流無 TTFT，TPOT 改由 usage.completion_tokens 平均。"""

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
        reason_parts = []        # 思考內容（reasoning_content）逐塊累積
        t_first = None
        t_last = None
        n_chunks = 0
        usage_tokens = None
        usage = None             # 末包完整 usage（供重組最終回應物件）
        finish_reason = None     # 結束原因（stop / length…）
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
                    usage = chunk["usage"]
                    usage_tokens = usage.get("completion_tokens")
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                fr = choices[0].get("finish_reason")
                if fr is not None:
                    finish_reason = fr
                delta = choices[0].get("delta") or {}
                # 思考內容（reasoning_content / reasoning）與正式內容皆視為「已生成輸出」：
                # 任一種 token 都計入首字時間 / 逐字延遲 / token 數，使吞吐與 usage（含 reasoning）一致。
                reason_piece = delta.get("reasoning_content")
                if reason_piece is None:
                    reason_piece = delta.get("reasoning")
                piece = delta.get("content")
                if reason_piece:
                    reason_parts.append(reason_piece)
                if piece:
                    text_parts.append(piece)
                if reason_piece or piece:
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
        # 思考內容與正式內容都算「已生成輸出」：字數合計、token 數優先取 usage（含 reasoning）
        r.output_chars = len(reasoning) + len(text)
        r.output_tokens = usage_tokens if usage_tokens is not None else n_chunks
        # 原文：輸入、思考內容、輸出內容；raw_response 只存「最後輸出結果」——
        # 重組成與非串流一致的回應物件（含 message / finish_reason / usage），不再逐 chunk 保存。
        r.input_text = _payload_text(payload)
        r.reasoning_text = reasoning
        r.output_text = text
        message = {"role": "assistant", "content": text}
        if reasoning:                       # 與非串流一致：無思考內容則不帶此欄
            message["reasoning_content"] = reasoning
        final_obj = {"choices": [{"index": 0, "message": message,
                                  "finish_reason": finish_reason}]}
        if usage is not None:
            final_obj["usage"] = usage
        r.raw_response = json.dumps(final_obj, ensure_ascii=False)
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
        reasoning = ""
        choices = obj.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            text = msg.get("content", "") or ""
            reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
        usage = obj.get("usage") or {}
        r.success = True
        r.e2e_s = t_end - t0
        # 思考內容與正式內容都算「已生成輸出」：字數合計、token 數取 usage（含 reasoning）
        r.output_chars = len(reasoning) + len(text)
        ct = usage.get("completion_tokens")
        r.output_tokens = ct
        # 非串流無首字延遲；TPOT 改用 usage.completion_tokens 的 token 數算平均每字時間
        if ct:
            r.tpot_ms = r.e2e_s / ct * 1000.0
        r.tokens_per_s = _rate(r.output_tokens, r.e2e_s)
        r.chars_per_s = _rate(r.output_chars, r.e2e_s)
        # 原文：輸入、思考內容、輸出內容，以及完整原始回應
        r.input_text = _payload_text(payload)
        r.reasoning_text = reasoning
        r.output_text = text
        r.raw_response = json.dumps(obj, ensure_ascii=False)


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
            obj = resp.json()
            out = _dig(obj, self.response_path)
            text = "" if out is None else (out if isinstance(out, str) else json.dumps(out, ensure_ascii=False))
            r.success = True
            r.e2e_s = t_end - t0
            r.output_chars = len(text)
            r.chars_per_s = _rate(r.output_chars, r.e2e_s)
            # 原文：輸入、輸出內容與完整原始回應（單純 JSON 服務無思考內容）
            r.input_text = _payload_text(payload)
            r.output_text = text
            r.raw_response = json.dumps(obj, ensure_ascii=False)
        except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
            r.success = False
            r.error = f"{type(exc).__name__}: {exc}"
        return r


class VLMAdapter:
    """VLM（圖片→文本）HTTP 服務，自訂 JSON（base64）。

    payload＝圖片檔路徑；讀檔 → base64 → 套用 request_template（佔位符 {image_b64} / {prompt} /
    {max_tokens}）→ POST → 依 response_path 取出文本。僅量端到端延遲（無串流 → 無 TTFT/TPOT）。
    純設定即可接：見 .env 的 VLM_* 欄位。
    """

    def __init__(self, url: str,
                 request_template: str = '{"image": "{image_b64}", "prompt": "{prompt}"}',
                 response_path: str = "output", prompt: str = "請描述這張圖片的內容。",
                 headers: Optional[dict] = None, timeout: float = 60.0,
                 data_uri: bool = False):
        self.url = url
        self.request_template = request_template
        self.response_path = response_path
        self.prompt = prompt
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.timeout = timeout
        self.data_uri = data_uri

    def _encode_image(self, image_path) -> str:
        raw = Path(image_path).read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        if self.data_uri:
            mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
            return f"data:{mime};base64,{b64}"
        return b64

    def _build_body(self, image_path, max_tokens: int) -> dict:
        text = self.request_template.replace("{image_b64}", self._encode_image(image_path))
        # prompt 經 json.dumps 跳脫後去掉外層引號，安全代入模板字串內
        text = text.replace("{prompt}", json.dumps(self.prompt)[1:-1])
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
            obj = resp.json()
            out = _dig(obj, self.response_path)
            text = "" if out is None else (out if isinstance(out, str)
                                           else json.dumps(out, ensure_ascii=False))
            r.success = True
            r.e2e_s = t_end - t0
            r.output_chars = len(text)
            r.chars_per_s = _rate(r.output_chars, r.e2e_s)
            # 原文：輸入（圖片路徑）、輸出內容與完整原始回應
            r.input_text = _payload_text(payload)
            r.output_text = text
            r.raw_response = json.dumps(obj, ensure_ascii=False)
        except (requests.RequestException, json.JSONDecodeError, ValueError, OSError) as exc:
            r.success = False
            r.error = f"{type(exc).__name__}: {exc}"
        return r


class CallableAdapter:
    """in-process 呼叫：把任一 Python callable 包成 adapter（用於已封裝成套件的模型）。

    `fn(payload) -> 文本`：量端到端延遲。
    `fn(payload) -> generator/iterator`（逐 token 產出）：記首個 yield 為 TTFT、累積算 TPOT，
    與 HTTP 串流邏輯一致。

    並發注意：ThreadPoolExecutor 受 GIL 影響；多數推論套件於 GPU/C++ 推論時會釋放 GIL，
    threaded 並發仍能反映真實吞吐；純 Python CPU-bound 不釋放 GIL 時，並發數據僅供參考。
    """

    def __init__(self, fn):
        self.fn = fn

    def call(self, payload, *, max_tokens: int = 256, temperature: float = 0.0,
             stream: bool = True) -> RequestResult:
        r = RequestResult(ts=time.time())
        t0 = time.perf_counter()
        try:
            out = self.fn(payload)
            if hasattr(out, "__iter__") and not isinstance(out, (str, bytes, list, dict)):
                # 逐 token 產出（generator/iterator）
                parts = []
                t_first = t_last = None
                n = 0
                for piece in out:
                    now = time.perf_counter()
                    if t_first is None:
                        t_first = now
                    t_last = now
                    n += 1
                    parts.append(str(piece))
                t_end = time.perf_counter()
                text = "".join(parts)
                raw_obj = {"output": text}
                if t_first is not None:
                    r.ttft_ms = (t_first - t0) * 1000.0
                    if n > 1 and t_last > t_first:
                        r.tpot_ms = (t_last - t_first) / (n - 1) * 1000.0
            else:
                text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
                raw_obj = out
                t_end = time.perf_counter()
            r.success = True
            r.status_code = 200
            r.e2e_s = t_end - t0
            r.output_chars = len(text)
            decode_s = r.e2e_s
            if r.ttft_ms is not None and (r.e2e_s - r.ttft_ms / 1000.0) > 0:
                decode_s = r.e2e_s - r.ttft_ms / 1000.0
            r.chars_per_s = _rate(r.output_chars, decode_s)
            # 原文：輸入、輸出內容與完整原始回應（套件無獨立思考內容欄位）
            r.input_text = _payload_text(payload)
            r.output_text = text
            r.raw_response = json.dumps(raw_obj, ensure_ascii=False)
        except Exception as exc:  # 套件呼叫可能拋任意例外
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
    if name == "vlm":
        if not config.VLM_URL:
            raise ValueError("ADAPTER=vlm 需設定 VLM_URL")
        return VLMAdapter(
            url=config.VLM_URL,
            request_template=config.VLM_REQUEST_TEMPLATE,
            response_path=config.VLM_RESPONSE_PATH,
            prompt=config.VLM_PROMPT,
            headers=config.VLM_HEADERS,
            timeout=config.REQUEST_TIMEOUT,
            data_uri=config.VLM_IMAGE_DATA_URI,
        )
    if name == "package":
        # 已封裝成 Python 套件、直接呼叫（非 HTTP）。延遲匯入範本以取得 callable。
        from core.package_adapter_example import build_callable
        return CallableAdapter(build_callable(config))
    if name == "custom":
        # 延遲匯入，避免未使用時也要求範本可載入
        from core.custom_adapter_example import CustomAdapter
        return CustomAdapter(config)
    raise ValueError(f"未知的 ADAPTER：{config.ADAPTER}")
