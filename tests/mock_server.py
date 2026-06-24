"""假 OpenAI 相容伺服器（純標準庫），供無 GPU 時做端到端驗證。

支援：
  POST /v1/chat/completions  —— stream=true 時以 SSE 逐 token 串流（先吐 reasoning_content 思考內容、
                                再吐 content，含末包 usage）；否則回單一 JSON（含 reasoning_content）。
  POST /predict              —— 非 OpenAI 回應測試用，回 {"output": "..."}。
  POST /api/eval             —— 客服評分服務範例用（examples/call_eval_service.py）：吃
                                {pid,text,call_type,…}，依 call_type 每個代號回一筆評分
                                （Column1…Service_status），整包為 JSON 陣列。

可獨立執行：  python tests/mock_server.py --port 8000
亦可被測試以 start_in_thread() 在背景啟動。
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 人工延遲，讓 TTFT/TPOT 非零、可被量測
TTFT_DELAY = 0.05    # 首字前延遲（秒）
TPOT_DELAY = 0.01    # 每個 token 間延遲（秒）

_WORDS = "這 是 一 段 測 試 用 的 模 擬 回 應 內 容 ".split()
_REASON_WORDS = "讓 我 想 一 下 這 題 怎 麼 答 ".split()


def _gen_tokens(max_tokens: int) -> list:
    n = max(1, min(max_tokens, 64))
    return [_WORDS[i % len(_WORDS)] for i in range(n)]


def _gen_reasoning(n: int = 4) -> list:
    """模擬「思考內容」（reasoning_content）的逐塊輸出，讓鏈路可驗證原文擷取。"""
    return [_REASON_WORDS[i % len(_REASON_WORDS)] for i in range(max(0, n))]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 靜音，保持主控台乾淨
        pass

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_POST(self):
        if self.path.startswith("/v1/chat/completions"):
            self._chat()
        elif self.path.startswith("/predict"):
            self._predict()
        elif self.path.startswith("/api/eval"):
            self._eval()
        else:
            self.send_error(404, "not found")

    def _chat(self):
        body = self._read_json()
        max_tokens = int(body.get("max_tokens", 16))
        stream = bool(body.get("stream", False))
        want_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        tokens = _gen_tokens(max_tokens)
        # 以 model 名稱切換是否模擬推理模型：含 "plain" 視為無思考內容的一般模型
        with_reasoning = "plain" not in str(body.get("model", "")).lower()
        reason_tokens = _gen_reasoning() if with_reasoning else []
        # completion_tokens 含思考內容 token（模擬 vLLM 等推理模型的計數方式）
        n_comp = len(reason_tokens) + len(tokens)

        if not stream:
            text = "".join(tokens)
            message = {"role": "assistant", "content": text}
            if reason_tokens:                       # 一般模型不帶 reasoning_content 欄位
                message["reasoning_content"] = "".join(reason_tokens)
            payload = {
                "id": "mock-1", "object": "chat.completion",
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": n_comp,
                          "total_tokens": 8 + n_comp},
            }
            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # 串流（SSE）
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send(obj):
            self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
            self.wfile.flush()

        # 首包：角色（附回應層級中繼資料，模擬真實串流）
        send({"id": "mock-1", "object": "chat.completion.chunk", "created": 0, "model": "mock",
              "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
        time.sleep(TTFT_DELAY)
        # 思考內容（reasoning_content）先於正式內容串出，模擬推理模型
        for i, rtok in enumerate(reason_tokens):
            if i > 0:
                time.sleep(TPOT_DELAY)
            send({"choices": [{"index": 0, "delta": {"reasoning_content": rtok},
                               "finish_reason": None}]})
        for i, tok in enumerate(tokens):
            if i > 0:
                time.sleep(TPOT_DELAY)
            send({"choices": [{"index": 0, "delta": {"content": tok}, "finish_reason": None}]})
        # 結束包
        send({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if want_usage:
            send({"choices": [], "usage": {"prompt_tokens": 8,
                                           "completion_tokens": n_comp,
                                           "total_tokens": 8 + n_comp}})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _predict(self):
        _ = self._read_json()
        time.sleep(TTFT_DELAY)
        data = json.dumps({"output": "label_A"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _eval(self):
        """模擬客服評分服務：吃 {pid,text,call_type,…}，依 call_type（以 "^" 切）每個代號回一筆
        評分；整包以 JSON 陣列回傳。前面 sleep 讓端到端 e2e 非零、可被量測。"""
        body = self._read_json()
        codes = [c for c in str(body.get("call_type", "")).split("^") if c] or ["代號1"]
        time.sleep(TTFT_DELAY)
        arr = [{
            "Column1": code,
            "Column2": ["合格"] * 6,
            "Column3": ["開場白", "結尾語", "等候與轉接", "建立期望值", "抱怨應對", "客訴"],
            "Column4": [""] * 6,
            "Column5": ["XXX"] * 6,
            "Service_status": ["100"],
        } for code in codes]
        data = json.dumps(arr, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_in_thread(port: int = 0):
    """在背景啟動伺服器，回傳 (httpd, port)。port=0 由 OS 指派可用埠。"""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, httpd.server_address[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock OpenAI server on http://127.0.0.1:{args.port}  (Ctrl-C 結束)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
