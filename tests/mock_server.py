"""假 OpenAI 相容伺服器（純標準庫），供無 GPU 時做端到端驗證。

支援：
  POST /v1/chat/completions  —— stream=true 時以 SSE 逐 token 串流（先吐 reasoning_content 思考內容、
                                再吐 content，含末包 usage）；否則回單一 JSON（含 reasoning_content）。
  POST /predict              —— 給 GenericJSONAdapter 測試用，回 {"output": "..."}。

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
        else:
            self.send_error(404, "not found")

    def _chat(self):
        body = self._read_json()
        max_tokens = int(body.get("max_tokens", 16))
        stream = bool(body.get("stream", False))
        want_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        tokens = _gen_tokens(max_tokens)

        if not stream:
            text = "".join(tokens)
            reasoning = "".join(_gen_reasoning())
            payload = {
                "id": "mock-1", "object": "chat.completion",
                "choices": [{"index": 0,
                             "message": {"role": "assistant",
                                         "reasoning_content": reasoning, "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": len(tokens),
                          "total_tokens": 8 + len(tokens)},
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

        # 首包：角色
        send({"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
        time.sleep(TTFT_DELAY)
        # 思考內容（reasoning_content）先於正式內容串出，模擬推理模型
        for i, rtok in enumerate(_gen_reasoning()):
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
                                           "completion_tokens": len(tokens),
                                           "total_tokens": 8 + len(tokens)}})
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
