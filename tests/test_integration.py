"""煙霧測試（不需 GPU）：對假 OpenAI 伺服器跑 simple_bench 與 accuracy 的核心路徑。

驗證：
  - simple_bench.chat_once 串流量到 TTFT/e2e、output_tokens，且 TTFT < e2e；
  - run_concurrent_simple 並發成功、依序帶入 answer、逐筆 stamp concurrency / scenario；
  - 輸出沿用 core：雙頁 Excel（明細頁含 answer 欄）＋同名 JSON；給 params 時多一頁「執行參數」；
  - _build_body 的思考模式開關（chat_template_kwargs.enable_thinking）與串流才帶 stream_options；
  - accuracy.compare_exact 完全相等比對（strip 後比較、跳過空正解）。

直接執行：python tests/test_integration.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.metrics import summarize
from core.reporter import print_summary, write_outputs
from tests.mock_server import start_in_thread


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def main():
    httpd, port = start_in_thread(0)
    base = f"http://127.0.0.1:{port}"
    print(f"mock server @ {base}")
    try:
        # --- simple_bench：requests 內聯呼叫 + 並發 + answer 端到端 ---
        print("[簡化版] simple_bench.chat_once / run_concurrent_simple（answer 端到端）")
        from simple_bench import chat_once, run_concurrent_simple
        sb = chat_once("hello", base_url=base, model="mock", max_tokens=12, stream=True)
        _check(sb.success and sb.ttft_ms is not None and sb.e2e_s is not None,
               "simple_bench.chat_once 串流量到 TTFT/e2e")
        _check(bool(sb.output_tokens) and sb.output_tokens > 0, "simple_bench 取到 output_tokens")
        _check(sb.ttft_ms / 1000.0 < sb.e2e_s, "simple_bench TTFT < e2e")

        pairs = [("問一", "甲"), ("問二", "乙"), ("問三", "丙")]
        sres, swall = run_concurrent_simple(pairs, 2, scenario="it_simple", run_label="MOCK",
                                            base_url=base, model="mock", max_tokens=12,
                                            stream=True, progress=False)
        _check(len(sres) == 3 and all(r.success for r in sres), "simple_bench 並發 3 筆全部成功")
        _check([r.answer for r in sres] == ["甲", "乙", "丙"],
               "simple_bench 把 answer 依序帶進每筆結果")
        _check(all(r.concurrency == 2 and r.scenario == "it_simple" for r in sres),
               "simple_bench 每筆 stamp concurrency / scenario")
        ssumm = summarize(sres, wall_seconds=swall)
        print_summary(ssumm)

        # --- 輸出契約：write_outputs 產生 Excel（明細頁含 answer）+ JSON 明細 ---
        sx, sj = write_outputs(sres, ssumm, os.path.join(tempfile.mkdtemp(), "it_simple_MOCK.xlsx"))
        _check(sx.endswith(".xlsx") and os.path.exists(sx) and os.path.exists(sj),
               "write_outputs 產生 Excel 與 JSON")
        with zipfile.ZipFile(sx) as z:
            s2 = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
            _check("answer" in s2, "simple_bench 輸出明細頁含 answer 欄")
            _check("raw_response" not in s2, "明細頁不含 raw_response（只進 JSON）")
        with open(sj, encoding="utf-8") as _f:
            srecs = json.load(_f)
        _check(srecs[0].get("answer") == "甲", "simple_bench JSON 明細含 answer")

        # --- _build_body：思考模式開關 + 串流才帶 stream_options ---
        from simple_bench import _build_body
        b_on = _build_body("x", model="m", max_tokens=8, temperature=0.0, stream=True, reasoning=True)
        b_off = _build_body("x", model="m", max_tokens=8, temperature=0.0, stream=False, reasoning=False)
        _check(b_on.get("chat_template_kwargs") == {"enable_thinking": True},
               "_build_body reasoning=True → enable_thinking=True")
        _check(b_off.get("chat_template_kwargs") == {"enable_thinking": False},
               "_build_body reasoning=False → enable_thinking=False")
        _check("stream_options" in b_on and "stream_options" not in b_off,
               "_build_body 串流才帶 stream_options")
        rres, _rw = run_concurrent_simple([("q", "a")], 1, scenario="it_reason", run_label="MOCK",
                                          base_url=base, model="mock", max_tokens=8,
                                          stream=True, reasoning=False, progress=False)
        _check(len(rres) == 1 and rres[0].success, "simple_bench reasoning=False 端到端仍成功")

        # --- 自訂 body_builder：取代通用版、仍能解析回應；並經 run_concurrent_simple 串接 ---
        seen = []
        def _cust(prompt, *, model, max_tokens, temperature, stream, reasoning):
            seen.append(prompt)
            b = _build_body(prompt, model=model, max_tokens=max_tokens, temperature=temperature,
                            stream=stream, reasoning=reasoning)
            b.pop("chat_template_kwargs", None)   # 模擬嚴格服務：移除 vLLM 專屬欄位
            b["top_p"] = 0.9                       # 模擬新增欄位
            return b
        cb = chat_once("hi", base_url=base, model="mock", max_tokens=8, stream=True, body_builder=_cust)
        _check(cb.success and seen == ["hi"], "chat_once 用自訂 body_builder（被呼叫且成功）")
        cres, _cw = run_concurrent_simple([("q", "a")], 1, base_url=base, model="mock",
                                          max_tokens=8, stream=True, body_builder=_cust, progress=False)
        _check(len(cres) == 1 and cres[0].success, "run_concurrent_simple 傳遞 body_builder 仍成功")
        _check(_build_body("z", model="m", max_tokens=8, temperature=0.0, stream=True, reasoning=True)
               .get("chat_template_kwargs") == {"enable_thinking": True},
               "通用版 _build_body 不受影響（預設行為不變）")

        # --- 完全自訂服務：自訂端點 /predict + 自訂 body + 自訂回應解析（非串流、量 e2e）---
        def _gen_body(prompt, *, model, max_tokens, temperature, stream, reasoning):
            return {"pid": "X1", "skill_type": "A_Bank", "text": str(prompt)}   # 整包非 OpenAI 格式
        def _gen_parse(resp):
            obj = resp.json()                       # mock /predict 回 {"output": "label_A"}
            return {"output_text": str(obj.get("output", "")), "output_tokens": None}
        gres, _gw = run_concurrent_simple([("客戶語音轉錄…", "label_A")], 1,
                                          base_url=base, model="x", stream=False,
                                          api_path="/predict", body_builder=_gen_body,
                                          response_parser=_gen_parse, progress=False)
        _check(len(gres) == 1 and gres[0].success, "自訂端點+body+回應解析：請求成功")
        _check(gres[0].output_text == "label_A", "RESPONSE_PARSER 取到 output_text=label_A")
        _check(gres[0].e2e_s is not None and gres[0].ttft_ms is None,
               "自訂服務量到 e2e、TTFT 留空（非串流）")
        _check(gres[0].answer == "label_A", "answer 正確帶入（可供 accuracy 比對）")

        # --- 執行參數寫進 Excel 第③頁「執行參數」（params 為選用、向後相容）---
        px, _pj = write_outputs(sres, ssumm, os.path.join(tempfile.mkdtemp(), "it_params_MOCK.xlsx"),
                                params=[("MODEL", "mock"), ("CONCURRENCY", 2),
                                        ("STREAM", True), ("REASONING", False)])
        with zipfile.ZipFile(px) as z:
            _check("xl/worksheets/sheet3.xml" in z.namelist(), "有 params 時產生第三個工作表")
            _check("執行參數" in z.read("xl/workbook.xml").decode("utf-8"), "第三頁名稱為 執行參數")
            s3 = z.read("xl/worksheets/sheet3.xml").decode("utf-8")
            _check("MODEL" in s3 and "CONCURRENCY" in s3, "執行參數頁含參數名 MODEL / CONCURRENCY")
        with zipfile.ZipFile(sx) as z:
            _check("xl/worksheets/sheet3.xml" not in z.namelist(),
                   "不傳 params 時不產生第三頁（向後相容）")

        # --- 準確率 accuracy：完全相等比對（strip、跳過無正解）---
        print("[準確率] accuracy.compare_exact 完全相等 + 跳過空正解")
        from accuracy import compare_exact, list_fields
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

        print("\n全部煙霧測試通過 ✅")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
