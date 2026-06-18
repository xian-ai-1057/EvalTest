# Plan — 架構與介面契約

> 對應 `spec.md`。重點在 **G2：模組正確串接** —— 先把模組之間的「契約」釘死，各模組即可獨立實作而保證能組裝。

## 目錄結構
```
EvalTest/
├── .env.example          # 設定範本（端點、模型、RUN_LABEL、長度、併發、SLA…）
├── .gitignore            # 忽略 .env、__pycache__、results/
├── requirements.txt      # 僅 requests
├── README.md             # 安裝、各情境執行、結果判讀、接入既有服務三種接法
├── config.py             # 手寫 .env parser → 型別化 Config(dataclass)
├── core/
│   ├── __init__.py
│   ├── client.py                # make_adapter()工廠 + OpenAIChatAdapter(串流) + GenericJSONAdapter
│   ├── custom_adapter_example.py# 自訂服務 adapter 範本（對號入座）
│   ├── runner.py                # run_single / run_concurrent（ThreadPoolExecutor，可選 Poisson）
│   ├── metrics.py               # RequestResult + summarize()
│   ├── reporter.py              # write_csv() / write_json() / write_outputs() + print_summary()
│   └── gpu.py                   # GpuSampler（背景 nvidia-smi 取樣）
├── scenarios/
│   ├── s1_interactive.py / s2_concurrency.py / s3_batch.py / s4_bert.py / s6_stt.py
├── data/ prompts.sample.txt
└── tests/ mock_server.py（stdlib 假 OpenAI SSE）+ test_integration.py（全鏈路串接測試）
```

## 模組介面契約（串接的接縫）
1. **資料契約 `RequestResult`（metrics.py，dataclass）** — 全鏈路流通的唯一單筆資料：
   `scenario, run_label, index, ts, concurrency, success, status_code, ttft_ms, tpot_ms,
    e2e_s, output_tokens, output_chars, tokens_per_s, chars_per_s, error`；
   另含原文（供人工檢視、不參與統計）：`input_text`（輸入原文）、`reasoning_text`（思考內容
   reasoning_content）、`output_text`（輸出內容）寫進 CSV；`raw_response`（完整原始回應 JSON 字串、
   標記 csv=False）只寫進 JSON 明細。
2. **呼叫契約 `Adapter`（client.py）** — 所有 adapter 一致實作：
   `call(payload, *, max_tokens, temperature, stream) -> RequestResult`
   runner 只依賴此協定，不認識底層 HTTP。`make_adapter(config)` 依 `ADAPTER` 名稱選用
   （`openai_chat` / `generic_json` / 自訂名）。
3. **執行契約（runner.py）**：
   `run_single(adapter, inputs, **kw) -> list[RequestResult]`
   `run_concurrent(adapter, inputs, concurrency, arrival, **kw) -> (list[RequestResult], wall_seconds)`
4. **彙總契約（metrics.py）**：`summarize(results) -> Summary`
   （count、success_rate、各延遲 mean/p50/p90/p95/p99、總吞吐＝Σtokens÷wall、平均 tokens/s 與 chars/s）
5. **輸出契約（reporter.py）**：`write_outputs(results, csv_path) -> (csv_path, json_path)`
   （內部呼叫 `write_csv` 寫可讀明細 + `write_json` 寫含 `raw_response` 的完整明細）、
   `print_summary(summary, gpu_stats=None)`
6. **GPU 契約（gpu.py）**：`GpuSampler(interval).start()`；`.stop() -> GpuStats|None`（util/mem 的 mean/max；無 nvidia-smi → None）
7. **設定契約（config.py）**：`Config` 提供
   `ADAPTER, BASE_URL, API_KEY, MODEL, RUN_LABEL, REQUEST_TIMEOUT, INPUT_LEN, MAX_TOKENS, TEMPERATURE,
    CONCURRENCY_LEVELS, N_REQUESTS, SLA_TTFT_MS, SLA_P95_MS, GPU_MONITOR, GPU_SAMPLE_INTERVAL,
    OUTPUT_DIR, COUNT_UNIT`；通用 adapter 另有
    `GENERIC_URL, GENERIC_REQUEST_TEMPLATE, GENERIC_RESPONSE_PATH, GENERIC_HEADERS`

## 關鍵量測邏輯
SSE 逐行解析：送出前記 `t0` → 首個內容 chunk = **TTFT**；累積各 token 時間戳，
`TPOT=(末token−首token)/(n−1)`、`e2e=結束−t0`；輸出量優先取末包 `usage`
（送 `stream_options.include_usage=true`），無則以 chunk 數 / 字元數回退；同時記 `output_chars`
對應 Excel 的「每秒字數、毫秒/字」。
另一併擷取原文：`delta.content` 累積為 `output_text`、`delta.reasoning_content`（相容 `reasoning`）
累積為 `reasoning_text`、輸入存 `input_text`、整段串流原始 chunk 存 `raw_response`；非串流則取
`message.content` / `message.reasoning_content` 與整個回應物件。
推理模型的 reasoning 與 content 都視為「已生成輸出」：TTFT 取第一個 token（不分思考/內容）、
TPOT 與 tokens_per_s/chars_per_s 及 `output_tokens`/`output_chars` 皆涵蓋兩者，與
`usage.completion_tokens`（含 reasoning）一致（`output_text` 仍只放最終內容）。

## 接入既有服務（三種接法，對應 FR9）
唯一接縫在 **adapter**；以下三者 runner / metrics / reporter / 情境腳本完全不變：
- **(a) OpenAI 相容 → 零程式**：`ADAPTER=openai_chat` + `BASE_URL/MODEL/API_KEY`。
- **(b) 單純自訂 JSON → 純設定**：`ADAPTER=generic_json` + `GENERIC_URL` + `GENERIC_REQUEST_TEMPLATE`（含 `{input}`）+ `GENERIC_RESPONSE_PATH`。
- **(c) 複雜/串流/特殊驗證 → 小幅自訂**：照 `core/custom_adapter_example.py` 實作 `call()->RequestResult`，`ADAPTER=自訂名`。
- 不串流的服務：TTFT/TPOT 退化為以端到端延遲為準、TPOT 不適用，其餘照常。

## 情境腳本（薄層，流程一致）
讀 Config → `make_adapter` →（可選）`gpu.start()` → runner 單發/並發 → `gpu.stop()` → `summarize` → `write_csv`+`print_summary`。
