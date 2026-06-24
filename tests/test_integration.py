"""煙霧測試（不需 GPU）：對假 OpenAI 伺服器跑重構後的 simple_bench 核心路徑與 accuracy。

驗證：
  - core.client.call 串流量到 TTFT/e2e、output_tokens，且 TTFT < e2e（body 先由 build_body 組好再傳入）；
  - core.runner.run_benchmark 並發成功、依序帶入 answer、逐筆 stamp concurrency/scenario/input_text；
  - 輸出為結構化 JSON dict（summary/params/detail）＋三頁 Excel（明細頁含 answer、不含 raw_response）；
  - build_body 思考模式開關與串流才帶 stream_options；自訂 body_builder / response_parser / api_path；
  - accuracy.compare_exact 完全相等比對（strip、跳過空正解），且能讀新的 dict JSON 明細。

直接執行：python tests/test_integration.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.client import call
from core.config import BenchConfig
from core.dataset import build_pairs
from core.metrics import summarize
from core.payload import build_body
from core.reporter import build_report, print_summary, write_outputs
from core.runner import run_benchmark
from tests.mock_server import start_in_thread


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def main():
    httpd, port = start_in_thread(0)
    base = f"http://127.0.0.1:{port}"
    print(f"mock server @ {base}")

    def _cfg(**kw):                                  # 補上每次都一樣的 base_url / model / 靜音進度
        kw.setdefault("base_url", base)
        kw.setdefault("model", "mock")
        kw.setdefault("progress", False)
        return BenchConfig(**kw)

    try:
        # --- client.call：body 先組好再傳入，純呼叫＋解碼 ---
        print("[重構版] core.client.call / core.runner.run_benchmark（answer 端到端）")
        url = base + "/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        body = build_body("hello", model="mock", max_tokens=12, temperature=0.0,
                          stream=True, reasoning=True)
        sb = call(url, headers, body, stream=True)
        _check(sb.success and sb.ttft_ms is not None and sb.e2e_s is not None,
               "client.call 串流量到 TTFT/e2e")
        _check(bool(sb.output_tokens) and sb.output_tokens > 0, "client.call 取到 output_tokens")
        _check(sb.ttft_ms / 1000.0 < sb.e2e_s, "client.call TTFT < e2e")

        # --- run_benchmark：並發、answer 依序、逐筆 stamp ---
        pairs = [("問一", "甲"), ("問二", "乙"), ("問三", "丙")]
        sres, swall = run_benchmark(pairs, _cfg(max_tokens=12, stream=True, concurrency=2,
                                                scenario="it_simple", run_label="MOCK"))
        _check(len(sres) == 3 and all(r.success for r in sres), "run_benchmark 並發 3 筆全部成功")
        _check([r.answer for r in sres] == ["甲", "乙", "丙"],
               "run_benchmark 把 answer 依序帶進每筆結果")
        _check(all(r.concurrency == 2 and r.scenario == "it_simple" for r in sres),
               "run_benchmark 每筆 stamp concurrency / scenario")
        _check(all(r.input_text for r in sres), "run_benchmark 逐筆 stamp input_text")
        ssumm = summarize(sres, wall_seconds=swall)
        print_summary(ssumm)

        # --- 輸出契約：JSON 為 dict（summary/params/detail）+ 三頁 Excel ---
        sx, sj = write_outputs(sres, ssumm, os.path.join(tempfile.mkdtemp(), "it_simple_MOCK.xlsx"))
        _check(sx.endswith(".xlsx") and os.path.exists(sx) and os.path.exists(sj),
               "write_outputs 產生 Excel 與 JSON")
        wb = openpyxl.load_workbook(sx)
        _check(wb.sheetnames[:2] == ["統計摘要", "明細"], "Excel 前兩頁為 統計摘要 / 明細")
        header = [c.value for c in wb["明細"][1]]
        _check("answer" in header, "明細頁含 answer 欄")
        _check("raw_response" not in header, "明細頁不含 raw_response（只進 JSON）")
        with open(sj, encoding="utf-8") as _f:
            report = json.load(_f)
        _check(isinstance(report, dict) and "summary" in report and "detail" in report,
               "JSON 為結構化 dict（含 summary / detail）")
        _check(report["detail"][0].get("answer") == "甲", "JSON detail[0] 含 answer")
        _check("raw_response" in report["detail"][0], "JSON detail 保留 raw_response（只在 JSON）")

        # --- build_body：思考模式開關 + 串流才帶 stream_options ---
        b_on = build_body("x", model="m", max_tokens=8, temperature=0.0, stream=True, reasoning=True)
        b_off = build_body("x", model="m", max_tokens=8, temperature=0.0, stream=False, reasoning=False)
        _check(b_on.get("chat_template_kwargs") == {"enable_thinking": True},
               "build_body reasoning=True → enable_thinking=True")
        _check(b_off.get("chat_template_kwargs") == {"enable_thinking": False},
               "build_body reasoning=False → enable_thinking=False")
        _check("stream_options" in b_on and "stream_options" not in b_off,
               "build_body 串流才帶 stream_options")
        rres, _rw = run_benchmark([("q", "a")], _cfg(max_tokens=8, stream=True, reasoning=False,
                                                     scenario="it_reason", run_label="MOCK"))
        _check(len(rres) == 1 and rres[0].success, "run_benchmark reasoning=False 端到端仍成功")

        # --- 自訂 body_builder：取代通用版、仍能解析回應（經 runner 組 body 階段套用）---
        seen = []
        def _cust(prompt, *, model, max_tokens, temperature, stream, reasoning):
            seen.append(prompt)
            b = build_body(prompt, model=model, max_tokens=max_tokens, temperature=temperature,
                           stream=stream, reasoning=reasoning)
            b.pop("chat_template_kwargs", None)   # 模擬嚴格服務：移除 vLLM 專屬欄位
            b["top_p"] = 0.9                       # 模擬新增欄位
            return b
        cres, _cw = run_benchmark([("q", "a")], _cfg(max_tokens=8, stream=True, body_builder=_cust))
        _check(len(cres) == 1 and cres[0].success and seen == ["q"],
               "run_benchmark 用自訂 body_builder（被呼叫且成功）")
        _check(build_body("z", model="m", max_tokens=8, temperature=0.0, stream=True, reasoning=True)
               .get("chat_template_kwargs") == {"enable_thinking": True},
               "通用版 build_body 不受影響（預設行為不變）")

        # --- 完全自訂服務：自訂端點 /predict + 自訂 body + 自訂回應解析（非串流、量 e2e）---
        def _gen_body(prompt, *, model, max_tokens, temperature, stream, reasoning):
            return {"pid": "X1", "skill_type": "A_Bank", "text": str(prompt)}   # 整包非 OpenAI 格式
        def _gen_parse(resp):
            obj = resp.json()                       # mock /predict 回 {"output": "label_A"}
            return {"output_text": str(obj.get("output", "")), "output_tokens": None}
        gres, _gw = run_benchmark([("客戶語音轉錄…", "label_A")],
                                  _cfg(model="x", stream=False, api_path="/predict",
                                       body_builder=_gen_body, response_parser=_gen_parse))
        _check(len(gres) == 1 and gres[0].success, "自訂端點+body+回應解析：請求成功")
        _check(gres[0].output_text == "label_A", "RESPONSE_PARSER 取到 output_text=label_A")
        _check(gres[0].e2e_s is not None and gres[0].ttft_ms is None,
               "自訂服務量到 e2e、TTFT 留空（非串流）")
        _check(gres[0].answer == "label_A", "answer 正確帶入（可供 accuracy 比對）")

        # --- build_report：直接驗 dict 結構 ---
        rep = build_report(sres, ssumm, params={"MODEL": "mock"})
        _check(set(rep.keys()) == {"summary", "params", "detail"} and len(rep["detail"]) == 3,
               "build_report 回 {summary, params, detail}")

        # --- build_pairs：合成輸入固定長度 ---
        bp = build_pairs(dataset="", n_requests=3, input_len=64)
        _check(len(bp) == 3 and all(len(p[0]) == 64 for p in bp),
               "build_pairs 合成 3 筆指定長度輸入")

        # --- 執行參數寫進 Excel 第③頁「執行參數」（params 為選用、向後相容）---
        px, _pj = write_outputs(sres, ssumm, os.path.join(tempfile.mkdtemp(), "it_params_MOCK.xlsx"),
                                params=[("MODEL", "mock"), ("CONCURRENCY", 2),
                                        ("STREAM", True), ("REASONING", False)])
        wbp = openpyxl.load_workbook(px)
        _check("執行參數" in wbp.sheetnames, "有 params 時產生第三頁「執行參數」")
        p_text = " ".join(str(c.value) for row in wbp["執行參數"].iter_rows()
                          for c in row if c.value is not None)
        _check("MODEL" in p_text and "CONCURRENCY" in p_text, "執行參數頁含參數名 MODEL / CONCURRENCY")
        _check("執行參數" not in openpyxl.load_workbook(sx).sheetnames,
               "不傳 params 時不產生第三頁（向後相容）")

        # --- 準確率 accuracy：完全相等比對（strip、跳過無正解）+ 能讀新 dict JSON ---
        print("[準確率] accuracy.compare_exact 完全相等 + 跳過空正解")
        from accuracy import compare_exact, list_fields, load_records
        recs = [
            {"index": 0, "answer": "A", "output_text": "A"},     # 對
            {"index": 1, "answer": "B", "output_text": "C"},     # 錯
            {"index": 2, "answer": "", "output_text": "D"},      # 無正解 → 跳過
            {"index": 3, "answer": " A ", "output_text": "A"},   # strip 後相等 → 對
        ]
        asum, arows = compare_exact(recs, "answer", "output_text")
        _check(asum["compared"] == 3 and asum["correct"] == 2,
               "準確率：可比對 3、正確 2（跳過空正解、strip 後比對）")
        _check(abs(asum["accuracy"] - 2.0 / 3.0) < 1e-9, "準確率＝2/3")
        _check(len(arows) == 3 and list_fields(recs) == ["index", "answer", "output_text"],
               "逐筆列數正確、list_fields 取得欄位名")
        loaded = load_records(sj)                     # accuracy 讀新版 dict JSON（取 detail）
        _check(len(loaded) == 3 and loaded[0].get("answer") == "甲",
               "accuracy.load_records 能讀新 dict JSON 的 detail")

        print("\n全部煙霧測試通過 ✅")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
