# H100 vs RTX PRO 6000 通用測試程式

評估 **RTX PRO 6000 (Blackwell) 能否取代目前跑在 H100 上的工作負載**。
對推論/服務端點發出請求，量測延遲與吞吐，輸出**每一筆明細（CSV）+ 主控台平均/百分位摘要**，
據以判斷是否達標並找出瓶頸。對應 `H100 vs PRO6000 通用測試情境表.xlsx`。

- 規格文件（SDD）：`specs/001-h100-pro6000/`（`spec.md` / `plan.md` / `tasks.md`）
- 設計原則：自製、簡潔、唯一第三方相依 `requests`；模型呼叫只走 HTTP。
- 目標 Python 3.12（亦相容 3.11）。

## 安裝
```bash
pip install -r requirements.txt        # 只有 requests
cp .env.example .env                   # 修改成你的端點/標籤
```

## 設定（.env）
所有參數集中在 `.env`（見 `.env.example` 註解）。常用：
- `ADAPTER`：`openai_chat`（情境①②③）/ `generic_json`（情境④⑥或既有服務）/ `custom`
- `BASE_URL`、`MODEL`、`API_KEY`：模型端點（`BASE_URL` 給伺服器根，程式自動接 `/v1/chat/completions`）
- `RUN_LABEL`：寫進每一筆，用來區分卡別與精度，例如 `H100-FP8`、`PRO6000-FP4`
- `INPUT_LEN`、`MAX_TOKENS`、`N_REQUESTS`、`CONCURRENCY_LEVELS`
- `SLA_TTFT_MS`、`SLA_P95_MS`：情境② 判定達標用（由 PM/業務提供）
- `GPU_MONITOR`：`true` 時背景輪詢本機 `nvidia-smi`（無則自動略過）

快速檢查設定載入：`python config.py`

## 不需 GPU 的端到端驗證
```bash
# 1) 起內建假 OpenAI 伺服器
python tests/mock_server.py --port 8000
# 2) 另開一個終端，.env 設 BASE_URL=http://127.0.0.1:8000，跑：
python scenarios/s1_interactive.py --n 10
# 或直接跑整合測試（自動起假伺服器、檢查整條鏈路）
python tests/test_integration.py
```

## 各情境執行
| 指令 | 對應 | 量什麼 |
|------|------|--------|
| `python scenarios/s1_interactive.py --n 20 --max-tokens 256 --label H100-FP8` | ① 互動式生成 | TTFT、TPOT、端到端、單請求速率（併發=1）|
| `python scenarios/s2_concurrency.py --concurrency 1,8,16,32 --n 50` | ② 高併發 | 各併發的 P95、總吞吐、GPU；SLA 下最大併發 |
| `python scenarios/s3_batch.py --n 500 --concurrency 32 --input-len 2000` | ③ 批次推論 | 筆/小時、整批完成時間、單筆延遲 |
| `python scenarios/s4_bert.py --mode batch --concurrency 32` | ④ BERT 推論 | 單筆延遲 / 批次吞吐（QPS）|
| `python scenarios/s6_stt.py --n 20 --concurrency 4 --audio-seconds 30` | ⑥ STT | 即時率 RTF、並發路數 |

> 比較 H100 vs PRO 6000：把 `BASE_URL`/`MODEL` 指向各自端點，`RUN_LABEL` 標好（如 `H100-FP8`、`PRO6000-FP4`），
> 各跑一輪，再並排比較產出的 CSV 與摘要。

## 輸出
- 每筆明細：`results/<情境>_<標籤>_<時間>.csv`（欄位含 TTFT/TPOT/e2e/tokens/併發/標籤…）
- 主控台摘要：平均、P50/P95/P99、系統總吞吐，以及（若開啟）GPU 使用率/記憶體。

## 接入既有服務（三種接法）
**唯一接縫在 adapter**：`runner` / `metrics` / `reporter` / 情境腳本完全不用改。

**(a) 服務是 OpenAI 相容 chat → 零程式**
```ini
ADAPTER=openai_chat
BASE_URL=http://你的服務:port
MODEL=你的模型名
API_KEY=（若需要）
```
直接跑情境腳本。

**(b) 單純自訂 JSON（送輸入、回文字，不串流）→ 零程式，純設定**
```ini
ADAPTER=generic_json
GENERIC_URL=http://你的服務/predict
GENERIC_REQUEST_TEMPLATE={"text": "{input}"}   # {input} 是輸入佔位符；可選 {max_tokens}
GENERIC_RESPONSE_PATH=data.output               # 從回應取輸出文字的點路徑
GENERIC_HEADERS={"X-Api-Key": "..."}            # 選填
```
情境④BERT、⑥STT 多走此路（僅量端到端延遲；TTFT/TPOT 不適用）。

**(c) 複雜／串流／特殊驗證 → 小幅自訂（約 30–50 行）**
複製 `core/custom_adapter_example.py`，改 3 個 TODO，實作
`call(payload, *, max_tokens, temperature, stream) -> RequestResult`（記 `t0`／首 chunk=TTFT／結束=e2e），
再於 `.env` 設 `ADAPTER=custom`。

> 串流注意：TTFT/TPOT 需要串流；不串流的服務，這兩個指標會自動退化為以端到端延遲為準、TPOT 不適用，其餘照常。

## 架構
```
config.py              # .env → 型別化 Config
core/
  client.py            # make_adapter() 工廠 + OpenAIChatAdapter(串流) + GenericJSONAdapter
  custom_adapter_example.py
  runner.py            # run_single / run_concurrent（ThreadPoolExecutor）
  metrics.py           # RequestResult + summarize()
  reporter.py          # write_csv() + print_summary()
  gpu.py               # GpuSampler（背景 nvidia-smi）
scenarios/             # s1_interactive / s2_concurrency / s3_batch / s4_bert / s6_stt
tests/                 # mock_server.py（假 OpenAI SSE）+ test_integration.py
```

## 範圍與待補
- 納入情境 ①②③④⑥；排除 ⑤訓練、⑦純品質回歸。
- ④BERT / ⑥STT 的確切 request/response 形狀、⑥音檔時長來源：依實際端點調整（程式中以 TODO 標示）。
- 各情境 H100 基準值與驗收門檻（Excel 橘色欄）：由 PM/業務提供後填入 `.env` 的 SLA。
