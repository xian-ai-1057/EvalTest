# Tasks — 有序任務（每項附完成判準）

> 實作時逐項勾選。對應 `spec.md` 的 AC 與 `plan.md` 的契約。全部完成並通過驗證。

- [x] **T1 專案骨架**：`.gitignore`、`requirements.txt`、`.env.example`、`config.py`（讀 `.env`）。
      ✅ `python config.py` 正確印出設定。
- [x] **T2 metrics**：`RequestResult` + `summarize()`。 ✅ 整合測試算出正確平均/P95。
- [x] **T3 client**：`make_adapter()` 工廠 + `OpenAIChatAdapter`（串流量 TTFT/TPOT）+ `GenericJSONAdapter`
      + `custom_adapter_example.py` 範本。 ✅ 三種接法皆滿足 Adapter 協定、可被工廠依名載入。
- [x] **T4 runner**：`run_single` / `run_concurrent`。 ✅ 回傳型別符合執行契約。
- [x] **T5 reporter**：`write_csv` + `print_summary`。 ✅ CSV 欄位與 RequestResult 一致。
- [x] **T6 gpu**：`GpuSampler`。 ✅ 無 nvidia-smi 時回 None 不崩（AC4 已驗）。
- [x] **T7 mock server**：`tests/mock_server.py`（stdlib 假 OpenAI SSE，小延遲逐 token + usage）。 ✅ 可串流回應。
- [x] **T8 整合（G2 把關）**：`tests/test_integration.py` 對假伺服器跑 adapter→runner→metrics→reporter 全鏈路。 ✅ AC1/AC2 通過。
- [x] **T9 s1 互動式**：`scenarios/s1_interactive.py`（argparse）。 ✅ AC1/AC2。
- [x] **T10 s2 高併發**：`scenarios/s2_concurrency.py`（含 GPU）。 ✅ AC3/AC4。
- [x] **T11 s3 批次**：`scenarios/s3_batch.py`。 ✅ 產出筆/小時與整批時間。
- [x] **T12 s4/s6**：`scenarios/s4_bert.py`、`s6_stt.py`（通用 adapter + 明確 TODO 設定點）。 ✅ 可對假/自訂端點跑通。
- [x] **T13 README**：各情境執行指令、結果判讀、接入既有服務三種接法 (a)(b)(c)。 ✅ 依 README 可重現 AC1/AC3。

## 驗證紀錄（對假伺服器，無 GPU）
- AC1/AC2：`test_integration.py` 全通過；TTFT<e2e、tokens_per_s ≈ tokens÷(e2e−ttft)。
- AC3：`s2 --concurrency 1,4,8` 總吞吐 77→303→450 tok/s，CSV 含 concurrency 欄。
- AC4：`GPU_MONITOR=true` 於無 nvidia-smi 主機 exit=0、照常完成。
- AC5：`H100-FP8` 與 `PRO6000-FP4` 兩標籤各產出可並排比較的 CSV。
- AC6：`generic_json`、`custom` 兩種 adapter 僅以環境變數覆寫即接上 /predict 端點。
