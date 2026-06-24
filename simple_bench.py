#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最簡化版 OpenAI 相容 chat 跑分（單檔、用 function 分功能，非模組化）。

直接用 requests 打 /v1/chat/completions，量 TTFT/TPOT/端到端 e2e、支援並發呼叫，
輸出沿用既有 core：一個雙頁 Excel（第①頁統計摘要、第②頁每筆明細）＋同名 JSON 明細
＋主控台平均/百分位摘要，格式與其他情境一致。

參數全在下方「參數設定區」用 Python 變數調整，直接執行即可（不使用指令列 args、不依賴 .env）：

    python simple_bench.py

要算準確率時，把帶正解的資料集（CSV：question + answer）填到設定區的 DATASET，
跑完每筆的正解會一併寫進輸出明細與 JSON 的 answer 欄，再用 accuracy.py 比對。
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# 讓 `python simple_bench.py` 直接執行時能 import 到專案根目錄的 core
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.metrics import RequestResult, summarize          # noqa: E402
from core.reporter import print_summary, write_outputs     # noqa: E402


# ===== 參數設定區（改這裡就好；執行：python simple_bench.py）=====
BASE_URL = "http://127.0.0.1:8000"    # 伺服器根；自動補 /v1/chat/completions
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
# 自訂請求 body：None＝用內建通用版 _build_body（OpenAI 相容，現有設計）。
# 打 body 結構不同的服務時，指定自己的函式，簽章需與 _build_body 相同：
#   def my_body(prompt, *, model, max_tokens, temperature, stream, reasoning) -> dict
# 回傳整包 dict（含 messages / model…）。注意：是否串流由設定區 STREAM 決定，
# 你的 body["stream"] 請跟著傳入的 stream 參數設，否則回應解析（串流/非串流）會對不上。
BODY_BUILDER = None
# ===============================================================

SCENARIO = "simple_bench"

# 合成輸入用的基底句；CSV 表頭辨識（小寫比對）
_BASE_SENTENCE = "請以繁體中文回答以下問題並盡量延伸說明，涵蓋背景、原因與實務影響。"
_PROMPT_COLUMNS = ("prompt", "input", "text", "question", "問題", "輸入")
_ANSWER_COLUMNS = ("answer", "答案", "正解", "label", "標籤", "ground_truth")


# --------------------------- 輸入 ---------------------------

def build_prompt(char_len: int) -> str:
    """構造固定字元長度的合成輸入，確保跨卡比較時輸入長度一致。"""
    if char_len <= 0:
        return _BASE_SENTENCE
    reps = (char_len // len(_BASE_SENTENCE)) + 1
    return (_BASE_SENTENCE * reps)[:char_len]


def load_dataset(path: str) -> list:
    """讀資料集，回傳 [(prompt, answer), ...]。

    .csv：以 csv.reader 讀（utf-8-sig 相容 Excel 匯出的 BOM）。首列若含可辨識欄名，
      prompt 取 prompt/input/text/question/問題/輸入，answer 取 answer/答案/正解/label/標籤/
      ground_truth（找不到 answer 欄則留空）；無可辨識欄名則取第一欄為 prompt、無正解。
    其他（.txt 等）：一行一個 prompt、answer 留空。
    皆去除前後空白、略過空白列。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到資料集檔案：{path}")
    if p.suffix.lower() == ".csv":
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
        if not rows:
            return []
        header = [c.strip().lower() for c in rows[0]]
        p_col = next((i for i, h in enumerate(header) if h in _PROMPT_COLUMNS), None)
        a_col = next((i for i, h in enumerate(header) if h in _ANSWER_COLUMNS), None)
        if p_col is None:                         # 無可辨識表頭 → 第一欄當 prompt、整份都算資料
            return [(r[0].strip(), "") for r in rows if r and r[0].strip()]
        out = []
        for r in rows[1:]:                        # 認得的表頭 → 跳過表頭列
            prompt = r[p_col].strip() if p_col < len(r) else ""
            answer = r[a_col].strip() if (a_col is not None and a_col < len(r)) else ""
            if prompt:
                out.append((prompt, answer))
        return out
    lines = [ln.strip() for ln in p.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
    return [(ln, "") for ln in lines]


def build_pairs() -> list:
    """依設定區決定輸入：有 DATASET 讀檔（依 N_REQUESTS 對齊：不足循環補滿、過多截斷），
    否則造 N_REQUESTS 筆合成輸入。回傳 [(prompt, answer), ...]。"""
    if DATASET:
        pairs = load_dataset(DATASET)
        if not pairs:
            raise ValueError(f"資料集沒有可用內容：{DATASET}")
        if N_REQUESTS and N_REQUESTS > 0:
            pairs = [pairs[i % len(pairs)] for i in range(N_REQUESTS)]
        return pairs
    return [(build_prompt(INPUT_LEN), "") for _ in range(N_REQUESTS)]


# --------------------------- 呼叫（核心）---------------------------

def _rate(n, seconds):
    if not n or not seconds or seconds <= 0:
        return None
    return n / seconds


def _build_body(prompt, *, model, max_tokens, temperature, stream, reasoning):
    """組 OpenAI 相容 /v1/chat/completions 請求 body。

    chat_template_kwargs.enable_thinking 控制推理模型是否輸出思考內容（vLLM / SGLang 慣例）；
    串流時另加 stream_options.include_usage 以取精確 token 數。
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": str(prompt)}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
        "chat_template_kwargs": {"enable_thinking": reasoning},   # 思考模式開關
    }
    if stream:
        body["stream_options"] = {"include_usage": True}          # 要求末包附 usage，取精確 token 數
    return body


# 自訂 body 範例：打嚴格服務（如真正 OpenAI API）—— 拿掉 vLLM 專屬欄位、加取樣參數。
# 要用就解除註解、把設定區的 BODY_BUILDER 設成 _example_body_builder。
# def _example_body_builder(prompt, *, model, max_tokens, temperature, stream, reasoning):
#     body = _build_body(prompt, model=model, max_tokens=max_tokens,
#                        temperature=temperature, stream=stream, reasoning=reasoning)
#     body.pop("chat_template_kwargs", None)   # 嚴格端點會拒絕未知欄位
#     body["top_p"] = 0.9                       # 視服務需要新增/覆寫
#     return body


def chat_once(prompt, *, base_url, model, api_key="", max_tokens=256,
              temperature=0.0, stream=True, reasoning=True, timeout=60.0,
              body_builder=None) -> RequestResult:
    """打一次 OpenAI 相容 /v1/chat/completions，回傳量測結果 RequestResult。

    串流（stream=True）量 TTFT/TPOT；非串流只量端到端 e2e。reasoning 控制思考模式
    （chat_template_kwargs.enable_thinking）。body_builder 指定時改用它整包組 body
    （取代通用版 _build_body，供 body 結構不同的服務）。思考內容 reasoning_content
    （相容 reasoning）與正式 content 都計入字數 / token 數 / 計時，與 usage.completion_tokens
    一致。任何連線 / HTTP / JSON 解析錯誤都記為 success=False、填 error，不拋出（不讓單筆拖垮整批）。
    """
    url = base_url.rstrip("/") + "/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    builder = body_builder or _build_body                 # None＝用內建通用版
    body = builder(prompt, model=model, max_tokens=max_tokens,
                   temperature=temperature, stream=stream, reasoning=reasoning)

    r = RequestResult(ts=time.time())
    r.input_text = str(prompt)
    t0 = time.perf_counter()
    try:
        if stream:
            _decode_stream(url, headers, body, t0, r, timeout)
        else:
            _decode_once(url, headers, body, t0, r, timeout)
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


def _decode_once(url, headers, body, t0, r: RequestResult, timeout):
    """非串流：單發取回完整回應，只量端到端 e2e（無法量 TTFT/TPOT）。"""
    resp = requests.post(url, json=body, headers=headers, timeout=timeout)
    r.status_code = resp.status_code
    resp.raise_for_status()
    t_end = time.perf_counter()
    obj = resp.json()
    text = reasoning = ""
    choices = obj.get("choices") or []
    if choices:
        msg = choices[0].get("message") or {}
        text = msg.get("content", "") or ""
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    usage = obj.get("usage") or {}
    r.success = True
    r.e2e_s = t_end - t0
    r.output_chars = len(reasoning) + len(text)
    r.output_tokens = usage.get("completion_tokens")
    r.reasoning_text = reasoning
    r.output_text = text
    r.raw_response = json.dumps(obj, ensure_ascii=False)
    r.tokens_per_s = _rate(r.output_tokens, r.e2e_s)
    r.chars_per_s = _rate(r.output_chars, r.e2e_s)


# --------------------------- 並發 ---------------------------

def run_concurrent_simple(pairs, concurrency, *, scenario="", run_label="",
                          base_url, model, api_key="", max_tokens=256,
                          temperature=0.0, stream=True, reasoning=True, timeout=60.0,
                          body_builder=None, progress=True):
    """並發跑一批 (prompt, answer)。回傳 (results, wall_seconds)。

    closed-loop：固定 concurrency 個 worker 同時在飛（ThreadPoolExecutor）。逐筆 stamp
    index / concurrency / scenario / run_label，並把對應 answer 寫進 r.answer（供準確率比對）。
    wall_seconds 量整段牆鐘，交給 summarize 算系統總吞吐。
    """
    results = [None] * len(pairs)
    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        fut_map = {
            ex.submit(chat_once, prompt, base_url=base_url, model=model, api_key=api_key,
                      max_tokens=max_tokens, temperature=temperature,
                      stream=stream, reasoning=reasoning, timeout=timeout,
                      body_builder=body_builder): (i, answer)
            for i, (prompt, answer) in enumerate(pairs)
        }
        done = 0
        for fut in as_completed(fut_map):
            i, answer = fut_map[fut]
            try:
                r = fut.result()
            except Exception as exc:                     # 理論上 chat_once 不拋；保險起見
                r = RequestResult(ts=time.time(), success=False,
                                  error=f"{type(exc).__name__}: {exc}")
            r.scenario = scenario
            r.run_label = run_label
            r.index = i
            r.concurrency = concurrency
            r.answer = answer
            results[i] = r
            done += 1
            if progress and sys.stderr.isatty():
                print(f"\r  進度 {done}/{len(pairs)}", end="", file=sys.stderr, flush=True)
    if progress and pairs and sys.stderr.isatty():
        print("", file=sys.stderr)                       # 換行收尾
    return results, time.perf_counter() - wall_start


# --------------------------- 主流程 ---------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pairs = build_pairs()
    print(f"對 {BASE_URL} 發 {len(pairs)} 筆請求"
          f"（並發 {CONCURRENCY}，{'串流' if STREAM else '非串流'}，"
          f"思考 {'開' if REASONING else '關'}，模型 {MODEL}）…")

    results, wall = run_concurrent_simple(
        pairs, CONCURRENCY, scenario=SCENARIO, run_label=RUN_LABEL,
        base_url=BASE_URL, model=MODEL, api_key=API_KEY, max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE, stream=STREAM, reasoning=REASONING, timeout=REQUEST_TIMEOUT,
        body_builder=BODY_BUILDER,
    )

    summ = summarize(results, wall_seconds=wall)
    print_summary(summ)

    ts = time.strftime("%Y%m%d-%H%M%S")
    label = (RUN_LABEL or "run").replace("/", "_").replace(" ", "")
    base_path = os.path.join(OUTPUT_DIR, f"simple_{label}_{ts}.xlsx")
    # 本次執行參數（寫進報表第③頁；API_KEY 遮罩）
    params = [
        ("BASE_URL", BASE_URL),
        ("MODEL", MODEL),
        ("API_KEY", "***" if API_KEY else ""),
        ("RUN_LABEL", RUN_LABEL),
        ("N_REQUESTS", N_REQUESTS),
        ("實際請求數", len(pairs)),
        ("CONCURRENCY", CONCURRENCY),
        ("MAX_TOKENS", MAX_TOKENS),
        ("TEMPERATURE", TEMPERATURE),
        ("STREAM", STREAM),
        ("REASONING", REASONING),
        ("BODY_BUILDER", BODY_BUILDER.__name__ if BODY_BUILDER else "(通用版 _build_body)"),
        ("INPUT_LEN", INPUT_LEN),
        ("DATASET", DATASET or "(合成輸入)"),
        ("OUTPUT_DIR", OUTPUT_DIR),
        ("REQUEST_TIMEOUT", REQUEST_TIMEOUT),
        ("執行時間", ts),
    ]
    xlsx_path, json_path = write_outputs(results, summ, base_path, params=params)
    print(f"已輸出：\n  Excel：{xlsx_path}\n  JSON ：{json_path}")


if __name__ == "__main__":
    main()
