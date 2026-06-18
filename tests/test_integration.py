"""整合測試（G2 把關）：對假伺服器跑完整鏈路 adapter → runner → metrics → reporter。

驗證 AC1/AC2：
  - 產生對應筆數、全數成功；
  - 數值合理：TTFT < e2e、tokens_per_s ≈ output_tokens ÷ (e2e − ttft)；
  - 並發回傳 wall_seconds、總吞吐可算。
直接執行：python tests/test_integration.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.client import OpenAIChatAdapter, GenericJSONAdapter, VLMAdapter, CallableAdapter
from core.metrics import summarize
from core.reporter import print_summary
from core.runner import run_concurrent, run_single
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
        adapter = OpenAIChatAdapter(base_url=base, model="mock", timeout=30)

        # --- 單發（AC1/AC2）---
        print("[單發] run_single n=10")
        results = run_single(adapter, ["hello"] * 10, scenario="it_single",
                             run_label="MOCK", max_tokens=12, stream=True)
        _check(len(results) == 10, "產生 10 筆")
        _check(all(r.success for r in results), "全部成功")
        r0 = results[0]
        _check(r0.ttft_ms is not None and r0.e2e_s is not None, "有 TTFT 與 e2e")
        _check(r0.ttft_ms / 1000.0 < r0.e2e_s, "TTFT < e2e")
        _check(r0.output_tokens and r0.output_tokens > 0, "有輸出 token 數（來自 usage）")
        # tokens_per_s ≈ output_tokens / (e2e - ttft)
        decode_s = r0.e2e_s - r0.ttft_ms / 1000.0
        expect = r0.output_tokens / decode_s
        _check(abs(r0.tokens_per_s - expect) < 1e-6, "tokens_per_s ≈ tokens ÷ (e2e − ttft)")
        print_summary(summarize(results))

        # --- 原文擷取：輸入 / 思考內容 / 輸出內容 / 完整原始回應 ---
        print("[原文] 擷取輸入、思考內容、輸出內容與完整 JSON")
        import json as _json
        _check(bool(r0.input_text), "擷取到輸入原文 input_text")
        _check(bool(r0.output_text) and r0.output_chars == len(r0.output_text),
               "擷取到輸出內容 output_text（字數與 output_chars 一致）")
        _check(bool(r0.reasoning_text), "擷取到思考內容 reasoning_text（reasoning_content）")
        chunks = _json.loads(r0.raw_response)
        _check(isinstance(chunks, list) and len(chunks) > 0,
               "raw_response 為可解析的完整串流 JSON（保留所有 chunk）")

        # --- 欄位契約：CSV 含原文三欄、但排除超長的 raw_response（只進 JSON）---
        from core.metrics import field_names
        cols = field_names()
        _check(all(c in cols for c in ("input_text", "reasoning_text", "output_text")),
               "CSV 欄位含 input_text / reasoning_text / output_text")
        _check("raw_response" not in cols, "CSV 欄位不含 raw_response（只寫進 JSON）")
        _check("raw_response" in field_names(include_raw=True),
               "field_names(include_raw=True) 含 raw_response")

        # --- 輸出契約：write_outputs 同時產生 CSV + JSON 明細 ---
        import tempfile as _tmp
        from core.reporter import write_outputs
        csv_p, json_p = write_outputs(results, os.path.join(_tmp.mkdtemp(), "it_single_MOCK.csv"))
        _check(os.path.exists(csv_p) and os.path.exists(json_p),
               "write_outputs 同時產生 CSV 與 JSON 明細")
        with open(json_p, encoding="utf-8") as _f:
            recs = _json.load(_f)
        _check(len(recs) == len(results) and bool(recs[0].get("output_text")),
               "JSON 明細每筆含 output_text")
        _check(isinstance(recs[0].get("raw_response"), list),
               "JSON 明細的 raw_response 已還原為巢狀物件")

        # --- 並發（AC3 雛形）---
        print("[並發] run_concurrent concurrency=8 n=24")
        cres, wall = run_concurrent(adapter, ["hi"] * 24, concurrency=8,
                                    scenario="it_concurrent", run_label="MOCK",
                                    max_tokens=12, stream=True)
        _check(len(cres) == 24 and all(r.success for r in cres), "並發 24 筆全部成功")
        _check(wall > 0, "回傳 wall_seconds")
        _check(all(r.concurrency == 8 for r in cres), "每筆標記 concurrency=8")
        summ = summarize(cres, wall_seconds=wall)
        _check(summ.throughput_tokens_per_s and summ.throughput_tokens_per_s > 0, "可算系統總吞吐")
        print_summary(summ)

        # --- GenericJSONAdapter（FR8/AC6）---
        print("[通用] GenericJSONAdapter -> /predict")
        gen = GenericJSONAdapter(url=f"{base}/predict",
                                 request_template='{"text": "{input}"}',
                                 response_path="output", timeout=30)
        gres = run_single(gen, ["客戶要查詢帳單"] * 3, scenario="it_generic",
                          run_label="MOCK", stream=False)
        _check(all(r.success for r in gres), "generic 全部成功")
        _check(gres[0].output_chars and gres[0].output_chars > 0, "generic 取到輸出文字")

        # --- VLMAdapter 圖片→文本（FR10/AC7）---
        print("[VLM] VLMAdapter（圖片→base64→/predict）")
        import base64
        import tempfile
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tf.write(png)
            img_path = tf.name
        vlm = VLMAdapter(url=f"{base}/predict",
                         request_template='{"image": "{image_b64}", "prompt": "{prompt}"}',
                         response_path="output", prompt="描述這張圖片", timeout=30)
        vres = run_single(vlm, [img_path] * 3, scenario="it_vlm", run_label="MOCK", stream=False)
        _check(all(r.success for r in vres), "VLM 全部成功")
        _check(vres[0].e2e_s is not None and (vres[0].output_chars or 0) > 0,
               "VLM 量到 e2e 與輸出文字")

        # --- CallableAdapter 套件版（FR11/AC8）---
        print("[套件] CallableAdapter：回完整字串 / generator")
        c1 = run_single(CallableAdapter(lambda p: "這是一段辨識結果文字"),
                        ["x"] * 3, scenario="it_callable", run_label="MOCK")
        _check(all(r.success for r in c1) and c1[0].e2e_s is not None, "callable 回字串量到 e2e")

        def _gen(_p):
            import time as _t
            for ch in "逐字產出測試":
                _t.sleep(0.005)
                yield ch
        c2 = run_single(CallableAdapter(_gen), ["x"] * 3,
                        scenario="it_callable_stream", run_label="MOCK")
        _check(c2[0].ttft_ms is not None and c2[0].tpot_ms is not None,
               "callable generator 量到 TTFT/TPOT")

        # --- 資料集輸入：CSV / TXT 讀檔 + 依 n 對齊 ---
        print("[資料集] load_prompts_file / load_dataset_inputs（CSV、TXT、n 對齊）")
        import tempfile as _tf
        from scenarios._common import load_dataset_inputs, load_prompts_file
        d = _tf.mkdtemp()
        # 帶 BOM 表頭的 CSV：應取 prompt 欄、跳過表頭、忽略其他欄
        csv_hdr = os.path.join(d, "with_header.csv")
        with open(csv_hdr, "w", encoding="utf-8-sig", newline="") as f:
            f.write("id,prompt,note\n1,請問記憶體頻寬是什麼,a\n2,FP8 與 FP4 差異,b\n")
        p1 = load_prompts_file(csv_hdr)
        _check(p1 == ["請問記憶體頻寬是什麼", "FP8 與 FP4 差異"], "CSV 依 prompt 欄取值、跳過表頭")
        # 無可辨識表頭的 CSV：取第一欄、整份都算資料
        csv_nohdr = os.path.join(d, "no_header.csv")
        with open(csv_nohdr, "w", encoding="utf-8", newline="") as f:
            f.write("第一句,x\n第二句,y\n")
        _check(load_prompts_file(csv_nohdr) == ["第一句", "第二句"], "無表頭 CSV 取第一欄")
        # TXT：一行一個、略過空白行
        txt = os.path.join(d, "prompts.txt")
        with open(txt, "w", encoding="utf-8") as f:
            f.write("第一個提示\n\n  第二個提示  \n")
        _check(load_prompts_file(txt) == ["第一個提示", "第二個提示"], "TXT 一行一個、略過空白行")
        # 依 n 對齊：不足循環補滿、過多截斷
        _check(len(load_dataset_inputs(csv_hdr, 5)) == 5, "n=5 由 2 筆循環補滿")
        _check(load_dataset_inputs(csv_hdr, 5)[2] == p1[0], "循環補滿順序正確（第3筆回到第1筆）")
        _check(len(load_dataset_inputs(txt, 1)) == 1, "n=1 截斷至 1 筆")

        print("\n全部整合檢查通過 ✅")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
