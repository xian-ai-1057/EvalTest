# H100 vs RTX PRO 6000 通用測試程式

自製的推論／服務基準測試工具，用來評估 **RTX PRO 6000 (Blackwell) 能否取代目前跑在 H100 上的工作負載**。
它對推論端點發出請求、量測延遲與吞吐，輸出可直接判讀達標與否的報表。

每次執行產生三樣東西：

- **一個 Excel 報表**（`.xlsx`，純標準庫手寫、不依賴 openpyxl）：第①頁＝統計摘要、第②頁＝每筆明細。
- **一個 JSON 明細**（與 `.xlsx` 同檔名）：每筆所有欄位 ＋ 完整原始回應 `raw_response`。
- **主控台摘要**：平均／百分位／系統總吞吐（＋若開啟 GPU，使用率與記憶體）。

執行期間另有 **stderr 即時進度條**（非 TTY 時自動靜默）。對應的參考情境表為 `H100 vs PRO6000 通用測試情境表.xlsx`。

- 規格文件（SDD）：`specs/001-h100-pro6000/`（`spec.md` / `plan.md` / `tasks.md`）
- 設計原則：**唯一第三方相依 `requests`**，其餘只用 Python 標準庫；模型呼叫只走 HTTP（或 in-process 套件直呼）。
- 目標 **Python 3.12**（亦相容 3.11）。非生產專案——以「拿到數字、找出瓶頸」為目標，不追求完整容錯。

> 程式碼註解、docstring 與主控台字串一律使用**繁體中文**；修改時請沿用此慣例。

---

## 目錄

1. [安裝與快速開始](#安裝與快速開始)
2. [不需 GPU 的端到端驗證](#不需-gpu-的端到端驗證)
3. [核心概念（先看這裡）](#核心概念先看這裡)
4. [情境總覽](#情境總覽)
5. [各情境詳解與參數](#各情境詳解與參數)
6. [用自己的資料集（`--dataset`）](#用自己的資料集--dataset)
7. [設定參考（.env 全欄位）](#設定參考env-全欄位)
8. [接入既有服務（選 adapter）](#接入既有服務選-adapter)
9. [輸出與判讀](#輸出與判讀)
10. [比較 H100 vs PRO 6000](#比較-h100-vs-pro-6000)
11. [如何設計新情境腳本](#如何設計新情境腳本)
12. [架構地圖](#架構地圖)
13. [範圍與待補](#範圍與待補)

---

## 安裝與快速開始

```bash
pip install -r requirements.txt        # 只有 requests
cp .env.example .env                   # 改成你的端點/標籤
python config.py                       # 檢查 .env 是否正確載入（印出型別化 Config，遮罩 API_KEY）
```

`python config.py` 會把所有設定逐項印出，方便確認端點、模型、標籤是否如預期；其中 `API_KEY` 一律以 `***` 遮罩。

---

## 不需 GPU 的端到端驗證

不必有 GPU 也能把整條鏈路（adapter → runner → metrics → reporter）跑通：

```bash
# 方式 A：手動起假伺服器，再跑情境
python tests/mock_server.py --port 8000        # 內建假 OpenAI SSE / /predict 伺服器
# 另開一個終端，.env 設 BASE_URL=http://127.0.0.1:8000，再跑：
python scenarios/s1_interactive.py --n 10

# 方式 B：一鍵整合測試（自動起假伺服器、檢查整條鏈路）
python tests/test_integration.py
```

`test_integration.py` 是**單一斷言腳本**（無 pytest、無測試框架、無單一測項選擇）。要「跑某個測試」就跑整支腳本、看它輸出的檢查清單。新增行為時，往這支檔案再加一個 `_check(...)` 區塊。

---

## 核心概念（先看這裡）

整個系統建立在**兩個契約**上，動手改任何東西前請先理解這兩者：

### 1. `RequestResult`（`core/metrics.py`）

貫穿全鏈路（adapter → runner → metrics → reporter）的**唯一單筆資料形狀**。
**欄位宣告順序 == Excel 第②頁明細的欄序**（`reporter.py` 用 `field_names()` 建表）。
其中 `raw_response` 標記 `metadata={"csv": False}`，**只進 JSON、不進 Excel 明細**（內容過長）。

### 2. adapter 的 `call()` 契約（`core/client.py`）

每個 adapter 都實作**同一個方法**：

```python
call(payload, *, max_tokens, temperature, stream) -> RequestResult
```

runner 只認得這個契約，完全不知道底層 HTTP 細節。**`make_adapter(config)` 是註冊表**：依 `.env` 的 `ADAPTER` 值對應到一個 class。
支援新後端＝寫一個有 `call()` 的 class、在 `make_adapter()` 加一個名稱分支、（必要時）補設定欄位即可——**runner / metrics / reporter / 所有情境腳本都不用動**。

### 量測規則（重要）

- **TTFT / TPOT 需要串流。** 只有 `openai_chat`（SSE）與 `package`（generator）能產出這兩個指標；其餘 adapter 自動退化為只量端到端延遲（e2e）。
- **單請求 `tokens_per_s`** 以解碼階段（`e2e − ttft`）計算，不是用牆鐘；**系統總吞吐**＝`Σtokens ÷ wall_seconds`（由並發 runner 回傳）。
- **推理模型**：reasoning 與 content 都算「已生成輸出」——任一種 token 都計入 TTFT、TPOT、`output_tokens`、`output_chars`，與 `usage.completion_tokens`（含 reasoning）一致，避免吞吐被灌水。`output_text` 只保留最終回覆。

---

## 情境總覽

| 腳本 | 對應情境 | 量什麼 | 預設 adapter | 串流 |
|------|---------|--------|-------------|------|
| `scenarios/s1_interactive.py` | ① 互動式生成（延遲，併發=1） | TTFT、TPOT、e2e、單請求速率 | `openai_chat` | ✅ |
| `scenarios/s2_concurrency.py` | ② 高併發服務（吞吐 vs SLA） | 各併發的 P95、總吞吐、GPU；SLA 下最大併發 | `openai_chat` | ✅ |
| `scenarios/s3_batch.py` | ③ 批次推論（離線吞吐） | 筆/小時、整批完成時間、單筆延遲 | `openai_chat` | ✅ |
| `scenarios/s4_bert.py` | ④ BERT 分類推論 | 單筆延遲 / 批次吞吐（QPS） | `generic_json`（建議） | ❌ |
| `scenarios/s6_stt.py` | ⑥ STT 語音轉文字 | 即時率 RTF、並發路數 | `generic_json`（建議） | ❌ |
| `scenarios/s_vlm.py` | VLM 圖片→文本 | 單張延遲 / 吞吐（張/秒）；走 chat 時另量 TTFT/TPOT | `openai_chat` / `vlm` / `package` | 視 adapter |

> 所有腳本都「直接以路徑執行」（`python scenarios/foo.py ...`），不要改成 package-relative import。
> 腳本刻意設計得很薄、流程一致：`Config.load()` → `make_adapter()` →（選用 `GpuSampler.start()`）→ `run_single`/`run_concurrent` →（`GpuSampler.stop()`）→ `summarize()` → `print_summary()` + `write_outputs()`。

---

## 各情境詳解與參數

所有情境共通：未在指令列指定的旗標，預設值取自 `.env`（再退回程式內建預設）。`--label` 會寫進每一筆，用來區分卡別/精度。

### ① `s1_interactive.py` — 互動式生成（延遲導向，併發=1）

量單一請求的逐字體感：TTFT / TPOT / e2e / 單請求輸出速率。

| 旗標 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `--n` | int | `N_REQUESTS`(20) | 請求筆數 |
| `--input-len` | int | `INPUT_LEN`(512) | 合成輸入的字元長度（給 `--dataset` 時忽略） |
| `--max-tokens` | int | `MAX_TOKENS`(256) | 輸出長度上限 |
| `--label` | str | `RUN_LABEL` | 執行標籤，如 `H100-FP8` |
| `--dataset` | path | （無） | 從檔案讀 prompt 當輸入（見[資料集](#用自己的資料集--dataset)） |
| `--no-stream` | flag | 關 | 關閉串流（將**無法**量 TTFT/TPOT） |

```bash
python scenarios/s1_interactive.py --n 20 --max-tokens 256 --label H100-FP8
```

### ② `s2_concurrency.py` — 高併發服務（吞吐導向 + SLA 判定）

掃描多個併發級別，每級記錄 P95 延遲、系統總吞吐與 GPU；在延遲不破 SLA 的前提下，找出單卡可承載的最大併發。

| 旗標 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `--concurrency` | str | `CONCURRENCY_LEVELS` | 併發級別清單，如 `1,8,16,32` |
| `--n` | int | `N_REQUESTS`(20) | **每個**併發級別的請求筆數 |
| `--input-len` | int | `INPUT_LEN`(512) | 合成輸入字元長度（給 `--dataset` 時忽略） |
| `--max-tokens` | int | `MAX_TOKENS`(256) | 輸出長度上限 |
| `--label` | str | `RUN_LABEL` | 執行標籤 |
| `--dataset` | path | （無） | 外部資料集（每級各取 `--n` 筆） |
| `--arrival` | `closed`/`poisson` | `closed` | `closed`＝閉環（一次最多 N 筆在跑，量容量）；`poisson`＝以指數間隔送出，模擬隨機到達 |
| `--rate` | float | （無） | `poisson` 模式每秒請求數 |

```bash
python scenarios/s2_concurrency.py --concurrency 1,8,16,32 --n 50
python scenarios/s2_concurrency.py --concurrency 16 --n 200 --arrival poisson --rate 20
```

跑完會印**併發掃描總表**（每級的成功率、TTFT_p95、e2e_p95、總吞吐、達標 ✓/✗）與 **SLA 下最大可承載併發**。

**SLA 達標判定（`_meets_sla`）**——三條同時成立才算過：
1. `TTFT p95` **有值且** ≤ `SLA_TTFT_MS`（量不到 TTFT 視為未達標，避免假性通過）；
2. `e2e p95`（毫秒）≤ `SLA_P95_MS`；
3. 該級別 `failed == 0`。

### ③ `s3_batch.py` — 批次推論（離線吞吐）

模擬日批量（個資脫敏、文本要素辨識），量整批吞吐與完成時間。

| 旗標 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `--n` | int | `max(N_REQUESTS, 200)` | 批次總筆數 |
| `--concurrency` | int | `32` | 批次並發度（盡量拉高） |
| `--input-len` | int | `2000` | 單筆合成輸入字元數（脫敏約 2000 字/筆；給 `--dataset` 時忽略） |
| `--max-tokens` | int | `MAX_TOKENS`(256) | 輸出長度上限 |
| `--label` | str | `RUN_LABEL` | 執行標籤 |
| `--dataset` | path | （無） | 外部資料集 |

```bash
python scenarios/s3_batch.py --n 500 --concurrency 32 --input-len 2000
```

末尾印：**批次吞吐量（筆/小時）**、整批完成時間、單筆延遲 p95（門檻：單筆 ≤ 1 秒；整批對 D+1 由業務判定）。

### ④ `s4_bert.py` — BERT 分類推論（短序列）

| 旗標 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `--mode` | `single`/`batch` | `single` | `single`＝即時(batch=1) 看單筆延遲；`batch`＝拉大並發測吞吐 |
| `--n` | int | `N_REQUESTS`(20) | 請求筆數 |
| `--concurrency` | int | `32` | `batch` 模式的並發度 |
| `--seq-len` | int | `128` | 固定序列長度（字元） |
| `--label` | str | `RUN_LABEL` | 執行標籤 |

```bash
python scenarios/s4_bert.py --mode batch --concurrency 32
```

`batch` 模式末尾印 **QPS（筆/秒）** 與整批時間。BERT 服務多半不是 OpenAI chat 格式，**實務上建議在 `.env` 設 `ADAPTER=generic_json`** 並依端點調整 request/response（程式內以 TODO 標示）。此情境不串流（只量 e2e）。

### ⑥ `s6_stt.py` — 語音轉文字（選測）

| 旗標 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `--n` | int | `N_REQUESTS`(20) | 音檔筆數 |
| `--concurrency` | int | `1` | 並發路數（>1 測單卡可服務路數） |
| `--audio-seconds` | float | `30.0` | 每個音檔長度（秒），用於計算 RTF |
| `--label` | str | `RUN_LABEL` | 執行標籤 |

```bash
python scenarios/s6_stt.py --n 20 --concurrency 4 --audio-seconds 30
```

末尾印 **即時率 RTF**（＝端到端處理時間 ÷ 音檔長度，`< 1` 才能即時）的 mean/max 與並發路數。
目前輸入是**佔位字串**（`audio_sample_i`）讓流程可跑通；實務上請改為音檔路徑/識別碼清單，並在 `.env` 設 `generic_json` 依端點調整（程式內 TODO）。字錯率屬品質回歸（情境⑦），不在此量。

### VLM `s_vlm.py` — 圖片→文本

| 旗標 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `--images` | glob/dir | `VLM_IMAGE_GLOB`(`data/images/*`) | 圖片資料夾或 glob |
| `--n` | int | =圖片數 | 總筆數（不足循環補滿、過多截斷） |
| `--concurrency` | int | `1` | `1`＝單張延遲；`>1`＝吞吐/並發路數 |
| `--prompt` | str | `VLM_PROMPT` | 所有圖片共用的提示詞 |
| `--max-tokens` | int | `MAX_TOKENS`(256) | 輸出長度上限 |
| `--label` | str | `RUN_LABEL` | 執行標籤 |

```bash
python scenarios/s_vlm.py --images "data/images/*.jpg" --concurrency 1   # 單張延遲
python scenarios/s_vlm.py --images "data/images/*.jpg" --concurrency 8   # 吞吐 / 並發路數
```

同一支腳本支援**三種呼叫形態**，差別只在 `.env` 的 `ADAPTER`：

| `ADAPTER` | 你的 VLM 是 | 量到的指標 |
|-----------|------------|-----------|
| `openai_chat` | **OpenAI 相容 chat completion（vision）** | 圖片自動組成 vision messages、走 SSE 串流 → **TTFT/TPOT + e2e** |
| `vlm` | HTTP 自訂 JSON（base64，見 `VLM_*`） | 只量 e2e |
| `package` | Python 套件（直接呼叫，非 HTTP） | 套件回 generator 則量 TTFT/TPOT，否則 e2e |

**你的 VLM 若能用 chat completion 呼叫**，直接設 `ADAPTER=openai_chat` 並指好 `BASE_URL`/`MODEL`（提示詞用 `--prompt` 或 `.env` 的 `VLM_PROMPT`）：

```ini
ADAPTER=openai_chat
BASE_URL=http://你的VLM服務:port
MODEL=你的VLM模型名
```

```bash
python scenarios/s_vlm.py --images "data/images/*.jpg" --concurrency 1 --prompt "請描述這張圖片"
```

腳本會把每張圖片讀成 base64 `data:<mime>;base64,…`，包成 OpenAI vision 的 `messages`
（`[{"role":"user","content":[{"type":"text",...},{"type":"image_url",...}]}]`）後串流送出——
因此**比純 HTTP 的 `vlm` adapter 多量得到首字/逐字延遲**。明細的 `input_text` 會自動省略圖片 base64，只留前綴標記，保持可讀。`--concurrency>1` 末尾另印**吞吐（張/秒）**。

---

## 用自己的資料集（`--dataset`）

情境 ①②③ 預設用「固定字元長度的合成 prompt」（比較時輸入長度一致、可控）。
若要改用自己的題庫，加 `--dataset <檔案>`：

- **`.csv`**：取可辨識的 prompt 欄（欄名為 `prompt`/`input`/`text`/`question`/`問題`/`輸入` 任一，大小寫不拘）；找不到欄名就取**第一欄**。可含 `id,prompt,...` 多欄，其餘欄忽略。以 `utf-8-sig` 讀檔，相容 Excel 匯出的 BOM。
- **`.txt`**（及其他副檔名）：**一行一個 prompt**，略過空白行。

`--n` 仍決定總筆數：資料集**不足會循環補滿、過多會截斷**（語意同 VLM 的 `--images`）。**給了 `--dataset` 時 `--input-len` 失效。**

```bash
python scenarios/s1_interactive.py --dataset data/prompts.sample.txt --n 20 --label H100-FP8
python scenarios/s2_concurrency.py --dataset mydata.csv --concurrency 1,8,16,32 --n 50
```

> ④BERT、⑥STT、VLM 走不同輸入型態（序列長度／音檔／圖片資料夾），**不吃** `--dataset`。

---

## 設定參考（.env 全欄位）

所有參數集中在 `.env`（複製自 `.env.example`）。**真實環境變數（`os.environ`）優先於 `.env` 檔**，方便 CI 或臨時覆寫。`.env` 不進版控。

### adapter / 連線

| 欄位 | 預設 | 說明 |
|------|------|------|
| `ADAPTER` | `openai_chat` | `openai_chat` / `generic_json` / `vlm` / `package` / `custom` |
| `BASE_URL` | `http://127.0.0.1:8000` | 伺服器根；`openai_chat` 會自動接 `/v1/chat/completions` |
| `API_KEY` | （空） | 若端點需要，會以 `Authorization: Bearer` 帶上 |
| `MODEL` | `test-model` | 模型名 |
| `REQUEST_TIMEOUT` | `60` | 單請求逾時（秒） |

### 執行標籤 / 請求內容 / 執行規模

| 欄位 | 預設 | 說明 |
|------|------|------|
| `RUN_LABEL` | `unlabeled` | 寫進每一筆，用來區分卡別與精度，如 `H100-FP8`、`PRO6000-FP4` |
| `INPUT_LEN` | `512` | 構造合成輸入 prompt 的字元長度 |
| `MAX_TOKENS` | `256` | 期望輸出長度 |
| `TEMPERATURE` | `0.0` | 取樣溫度 |
| `N_REQUESTS` | `20` | 預設請求筆數 |
| `CONCURRENCY_LEVELS` | `1,8,16,32,64,128` | 情境② 併發掃描級別 |

### SLA 門檻（由 PM/業務填，情境② 用）

| 欄位 | 預設 | 說明 |
|------|------|------|
| `SLA_TTFT_MS` | `1000` | TTFT p95 上限（毫秒） |
| `SLA_P95_MS` | `5000` | 端到端 P95 上限（毫秒） |

### GPU 監控 / 輸出

| 欄位 | 預設 | 說明 |
|------|------|------|
| `GPU_MONITOR` | `false` | `true` 時背景輪詢本機 `nvidia-smi`（無 `nvidia-smi` 自動略過） |
| `GPU_SAMPLE_INTERVAL` | `0.5` | GPU 取樣間隔（秒） |
| `OUTPUT_DIR` | `results` | 報表輸出資料夾 |
| `COUNT_UNIT` | `token` | `token` / `char`，決定摘要主指標（**兩者都會記錄**） |

### 通用 JSON adapter（`ADAPTER=generic_json`）

| 欄位 | 預設 | 說明 |
|------|------|------|
| `GENERIC_URL` | （空） | 端點 URL（必填） |
| `GENERIC_REQUEST_TEMPLATE` | `{"input": "{input}"}` | 請求 JSON 模板；`{input}` 為輸入佔位符，可選 `{max_tokens}` |
| `GENERIC_RESPONSE_PATH` | `output` | 從回應取文字的點路徑，如 `data.output`、`choices.0.text` |
| `GENERIC_HEADERS` | `{}` | 額外標頭（JSON 物件） |

### VLM 圖片→文本（`ADAPTER=vlm`）

| 欄位 | 預設 | 說明 |
|------|------|------|
| `VLM_URL` | （空） | 端點 URL（必填） |
| `VLM_PROMPT` | `請描述這張圖片的內容。` | 所有圖片共用提示詞 |
| `VLM_REQUEST_TEMPLATE` | `{"image": "{image_b64}", "prompt": "{prompt}"}` | 請求模板；`{image_b64}` 為圖片 base64、`{prompt}` 為提示詞、可選 `{max_tokens}` |
| `VLM_RESPONSE_PATH` | `output` | 從回應取文本的點路徑 |
| `VLM_HEADERS` | `{}` | 額外標頭（JSON 物件） |
| `VLM_IMAGE_GLOB` | `data/images/*` | `s_vlm.py` 預設圖片來源 |
| `VLM_IMAGE_DATA_URI` | `false` | `true` 時 base64 前綴 `data:<mime>;base64,` |

---

## 接入既有服務（選 adapter）

**唯一接縫在 adapter**：`runner` / `metrics` / `reporter` / 情境腳本完全不用改。呼叫模型有兩種形態：用 `requests` 打 HTTP 端點（a–d），或模型已封裝成 Python 套件、在程式內直接呼叫（e）。兩種都回傳同一個 `RequestResult`。

**步驟**：① 辨識服務協定 → ② 選 adapter（設 `.env` 的 `ADAPTER`）→ ③ 填對應設定 → ④ 準備輸入（文字/圖片）→ ⑤ 跑對應情境 → ⑥ 看 Excel 明細與主控台摘要。

### (a) OpenAI 相容 chat → 零程式（`ADAPTER=openai_chat`）

```ini
ADAPTER=openai_chat
BASE_URL=http://你的服務:port
MODEL=你的模型名
API_KEY=（若需要）
```

SSE 串流量 TTFT/TPOT。直接跑情境①②③。

### (b) 單純自訂 JSON（送輸入、回文字，不串流）→ 純設定（`ADAPTER=generic_json`）

```ini
ADAPTER=generic_json
GENERIC_URL=http://你的服務/predict
GENERIC_REQUEST_TEMPLATE={"text": "{input}"}   # {input} 是輸入佔位符；可選 {max_tokens}
GENERIC_RESPONSE_PATH=data.output               # 從回應取輸出文字的點路徑
GENERIC_HEADERS={"X-Api-Key": "..."}            # 選填
```

情境④BERT、⑥STT 多走此路（僅量端到端延遲；TTFT/TPOT 不適用）。

### (c) 複雜／串流／特殊驗證 → 小幅自訂（`ADAPTER=custom`）

複製 `core/custom_adapter_example.py`，改 3 個 TODO，實作
`call(payload, *, max_tokens, temperature, stream) -> RequestResult`（記 `t0` / 首 chunk=TTFT / 結束=e2e），再於 `.env` 設 `ADAPTER=custom`（`core/client.py` 會延遲匯入）。

### (d) VLM 圖片→文本 → 純設定

三種形態（見 [VLM 情境詳解](#vlm-s_vlmpy--圖片文本)）：
- **OpenAI 相容 vision** → `ADAPTER=openai_chat`（**零程式**，`s_vlm.py` 會自動把圖片包成 vision `messages` 並串流，量得 TTFT/TPOT）。
- **HTTP 自訂 JSON（base64）** → `ADAPTER=vlm`，填上方 `VLM_*` 設定。
- **multipart 上傳圖檔** → 照 (c) 複製 `custom_adapter_example.py` 實作上傳。

把圖片放進資料夾後跑 `python scenarios/s_vlm.py --images "..."`。

### (e) 已封裝成 Python 套件（直接呼叫，非 HTTP）→（`ADAPTER=package`）

編輯 `core/package_adapter_example.py` 的 TODO（import 套件、把「載入→呼叫→取文本」包成 `fn(payload)->文本`），再設 `ADAPTER=package`。跑法與 (d) 相同，免起 HTTP 伺服器。

- 套件若**逐 token 產出**（回 generator），框架會量 TTFT/TPOT；一次回完整文字則量端到端。
- **並發注意**：走 ThreadPoolExecutor，受 GIL 影響。多數推論套件於 GPU/C++ 推論時會釋放 GIL，threaded 並發仍能反映真實吞吐；純 Python CPU-bound 不釋放 GIL 時，並發數據僅供參考。

> **串流注意**：TTFT/TPOT 需要串流。不串流的服務，這兩個指標自動退化為以端到端延遲為準、TPOT 不適用，其餘照常。

---

## 輸出與判讀

### 檔名規則

```
results/<情境>_<標籤>_<時間戳>.xlsx     # 例：results/s1_interactive_H100-FP8_20260619-021451.xlsx
results/<情境>_<標籤>_<時間戳>.json     # 同檔名、不同副檔名
```

時間戳為 `YYYYMMDD-HHMMSS`；`results/` 已被 gitignore。

### Excel 第①頁「統計摘要」

版面對齊主控台那張表，**每個 Summary 一個區塊**（情境②的多併發級別 → 每級各成一個區塊，區塊間空一列）。每區塊依序為：

1. `情境 / 標籤 / 併發`
2. `請求數 / 成功 / 失敗 / 成功率(%) / 牆鐘(s)`
3. **指標表**：列＝指標，欄＝`n / mean / p50 / p90 / p95 / p99 / min / max`
   - `TTFT 首字(ms)`、`TPOT 逐字(ms/字)`、`端到端 e2e(s)`、`單請求 tok/s`、`單請求 字/s`
4. `系統總吞吐`：`tokens/s`、`chars/s`、`Σtokens`、`Σchars`（單發情境無牆鐘時留空）
5. `GPU`（若開啟）：使用率 mean/max(%)、記憶體 mean/max(MB)、samples

> 摘要頁數值依主控台精度四捨五入；**完整精度保留在第②頁明細與 JSON**。

### Excel 第②頁「明細」／ JSON

每筆一列，欄序＝`RequestResult` 宣告順序：

| 欄位 | 意義 |
|------|------|
| `scenario` | 情境代號（如 `s1_interactive`） |
| `run_label` | 執行標籤 |
| `index` | 該批內序號 |
| `ts` | 請求開始的 epoch 秒 |
| `concurrency` | 此筆所在的併發級別 |
| `success` | 是否成功 |
| `status_code` | HTTP 狀態碼 |
| `ttft_ms` | 首字延遲（毫秒，串流才有） |
| `tpot_ms` | 逐字（逐 chunk）延遲（毫秒/字，串流才有） |
| `e2e_s` | 端到端延遲（秒） |
| `output_tokens` | 輸出 token 數（優先取 `usage.completion_tokens`） |
| `output_chars` | 輸出字數（思考＋內容合計） |
| `tokens_per_s` | 單請求輸出速率（以解碼階段 `e2e − ttft` 計） |
| `chars_per_s` | 單請求字元速率 |
| `error` | 失敗原因（成功則空） |
| `input_text` | 輸入原文 |
| `reasoning_text` | 思考內容（reasoning_content；無則空） |
| `output_text` | 輸出內容（最終回覆文字） |

**`raw_response`（完整原始回應）只進 JSON、不進 Excel 明細**（標記 `metadata={"csv": False}`）：串流為所有 chunk 清單、非串流為回應物件；JSON 會試著還原成巢狀物件，便於檢視思考/輸出/usage。

### 指標怎麼判讀

- **TTFT（首字延遲）**：使用者送出後多久看到第一個字。互動體感的關鍵；只有串流量得到。
- **TPOT（逐字延遲）**：實為「逐 chunk 延遲」。多數 OpenAI 相容後端 1 token/chunk，故近似逐字；若後端單 chunk 含多 token，TPOT 會偏高。
- **e2e（端到端）**：整個請求從送出到收完的秒數。批次/即時類情境的主指標。
- **單請求 `tokens_per_s`**：以**解碼階段**（`e2e − ttft`）計，反映「開始吐字後的生成速度」，不含排隊/首字等待。
- **系統總吞吐**：`Σtokens ÷ wall_seconds`，由並發 runner 的牆鐘算出，代表整卡在該併發下的真實產能。
- **成功率／失敗隔離**：單筆例外（連線錯誤、HTTP 4xx/5xx、回應非 JSON）記為 `success=False` 並保住 `status_code`，**不會拖垮整批**；統計只納入成功筆。

### 推理模型的計量

- 串流取 `delta.reasoning_content`（相容 `reasoning`）、非串流取 `message.reasoning_content`（相容 `reasoning`）；模型無此欄位則 `reasoning_text` 留空。
- **reasoning 與 content 都算「已生成輸出」**：TTFT 取第一個 token（不分思考/內容），TPOT、`tokens_per_s`、`chars_per_s`、`output_tokens`、`output_chars` 皆涵蓋兩者，與 `usage.completion_tokens`（含 reasoning）一致，避免吞吐被灌水。
- `output_text` 只放最終回覆；`output_chars` 為「思考＋內容」字數合計。原文字串本身僅供檢視、不另參與計算。
- 後端未回 `usage` 時，`output_tokens` 退化為「有內容的 SSE chunk 數」當代理值——chunk 數 ≠ token 數，此時 `tokens_per_s` 與系統總吞吐都是近似值，**跨後端比較請以有回 usage 者為準**。

---

## 比較 H100 vs PRO 6000

1. 把 `BASE_URL`/`MODEL` 指向各自端點；
2. 設好可區分的 `RUN_LABEL`（如 `H100-FP8` vs `PRO6000-FP4`）；
3. 每張卡各跑一次同樣的情境腳本；
4. 並排比較 `results/` 中產出的 `.xlsx`（第①頁統計摘要）與 `.json`。

---

## 如何設計新情境腳本

情境腳本（`scenarios/`）刻意設計得**很薄、很一致**：它們只負責「把輸入塑形 → 餵給 runner → 印一個情境特有的衍生指標」。所有重活（HTTP、量測、統計、報表）都在 `core/`，新情境不該碰。

### 黃金守則

> **情境層只做「輸入塑形」與「衍生指標」；傳輸與量測一律交給 adapter / runner / metrics / reporter。**

別把 HTTP、重試、token 計算、Excel 等邏輯寫進情境腳本——那會破壞「換一張卡只需改 `.env`、跑同一支腳本」的設計。

### 每支情境都長一樣（固定流程）

```
Config.load()                       # 讀 .env
  → make_adapter(cfg)               # 依 ADAPTER 取得 adapter（情境不在意是哪一種）
  → （選用）GpuSampler.start()
  → run_single / run_concurrent     # 餵入 inputs，拿回 list[RequestResult]（併發版另回 wall）
  → （選用）GpuSampler.stop()
  → summarize(results, wall)        # 彙整成 Summary（平均/百分位/總吞吐）
  → print_summary(summ, gpu)        # 主控台摘要
  → write_outputs(results, summ, output_path(cfg, "<情境名>"))   # Excel 雙頁 + JSON
```

各情境**唯一的差別**只有三點：① 單發或並發；② `stream` 開或關；③ 最後印一個情境特有的衍生指標（s2 的 SLA 最大併發、s3 的筆/小時、s4 的 QPS、s6 的 RTF、VLM 的張/秒）。新情境請套同一個模子，不要把邏輯加進 `core/`。

### 最小範本

```python
"""情境X 一句話說明（量什麼、為什麼）。"""
from __future__ import annotations

import argparse
import os
import sys

# 讓 `python scenarios/sX.py` 直接執行時能 import 到 config / core（固定樣板，照抄）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scenarios._common import make_inputs, output_path           # 依需求挑工具
from config import Config
from core.client import make_adapter
from core.metrics import summarize
from core.reporter import print_summary, write_outputs
from core.runner import run_concurrent, run_single


def main():
    cfg = Config.load()
    ap = argparse.ArgumentParser(description="情境X …")
    ap.add_argument("--n", type=int, default=cfg.N_REQUESTS)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=cfg.MAX_TOKENS)
    ap.add_argument("--label", default=cfg.RUN_LABEL)        # 一律提供 --label
    args = ap.parse_args()
    cfg.RUN_LABEL = args.label

    adapter = make_adapter(cfg)
    inputs = make_inputs(args.n, cfg.INPUT_LEN)              # ← 輸入塑形（情境的重點）
    print(f"情境X | n={args.n} 併發={args.concurrency} 標籤={cfg.RUN_LABEL}")

    if args.concurrency <= 1:
        results = run_single(adapter, inputs, scenario="sX", run_label=cfg.RUN_LABEL,
                             max_tokens=args.max_tokens, temperature=cfg.TEMPERATURE, stream=True)
        wall = None
    else:
        results, wall = run_concurrent(adapter, inputs, args.concurrency, scenario="sX",
                                       run_label=cfg.RUN_LABEL, max_tokens=args.max_tokens,
                                       temperature=cfg.TEMPERATURE, stream=True)

    summ = summarize(results, wall_seconds=wall)
    print_summary(summ)
    xlsx_path, json_path = write_outputs(results, summ, output_path(cfg, "sX"))

    # ← 情境特有的衍生指標（從 summ / results / wall 算，不入 core）
    if wall:
        print(f"  我的衍生指標：{summ.success / wall:,.1f} 筆/秒")
    print(f"Excel 報表：{xlsx_path}")
    print(f"JSON 明細：{json_path}")


if __name__ == "__main__":
    main()
```

### 設計步驟

1. **決定輸入型態，在情境層把它塑形成 `inputs`。** `inputs` 是一個清單，每個元素就是要交給 `adapter.call()` 的一筆 `payload`。可用 `scenarios/_common.py` 既有工具：
   - `make_inputs(n, char_len)`：固定字元長度的合成 prompt（跨卡比較時輸入長度一致）。
   - `load_dataset_inputs(path, n)`：吃 `.csv`/`.txt` 題庫（不足循環補滿、過多截斷）。
   - `load_image_paths(glob_or_dir, n)`：圖片路徑清單。
   - `build_vision_messages(image_path, prompt)`：把圖片組成 OpenAI vision `messages`（見下方 VLM 範例）。
2. **挑單發或並發。** 延遲導向用 `run_single`（併發=1）；吞吐/容量導向用 `run_concurrent`（回傳 `wall_seconds`，`summarize` 才算得出系統總吞吐）。
3. **決定 `stream`。** 要 TTFT/TPOT 就 `stream=True`，且 adapter 要支援串流（`openai_chat` SSE / `package` generator）；不支援串流的服務設 `stream=False`，自動退化為只量 e2e。
4. **一律提供 `--label`** 並 `cfg.RUN_LABEL = args.label`——這是跨卡/跨精度比較的依據，會寫進每一筆。
5. **用 `output_path(cfg, "<情境名>")` 產報表路徑**，再交給 `write_outputs()`。情境名會成為輸出檔名前綴與每筆的 `scenario` 欄。
6. **衍生指標只在情境腳本最後算、印出來即可**（QPS、RTF、張/秒…），用 `summ`／`results`／`wall` 現成資料，不要動 `core/`。

### 什麼時候該改 adapter、而不是寫新情境？

- **「同一種量測、不同輸入型態」→ 寫新情境**（例如不同序列長度、不同到達模型）。
- **「換一種後端協定」→ 寫新 adapter**（見[接入既有服務](#接入既有服務選-adapter)），情境完全不用改。
  判準：你要改的是「**送什麼**」還是「**怎麼送/怎麼解析回應**」？前者在情境，後者在 adapter。

### 加新指標時的注意

- 若新指標需要**每筆都記一個新欄位**：在 `RequestResult`（`core/metrics.py`）加欄位。記得**欄位順序＝Excel 明細欄序**；超長、不適合進 Excel 的欄位標 `metadata={"csv": False}`（只進 JSON，如 `raw_response`）。動了欄位請同步更新 `tests/test_integration.py` 的斷言。
- 若新指標是**整批彙總**（平均/百分位/總量）：在 `Summary` + `summarize()` 加，並視需要在 `print_summary` / 第①頁摘要列出。
- 若只是**單一情境的衍生數字**（不需要進通用報表）：留在情境腳本印出來就好。

### 慣例

- 註解、docstring、主控台字串一律**繁體中文**。
- **標準庫 only（唯一例外 `requests`）**：不要為了新情境加相依。
- 用路徑直接執行（`python scenarios/sX.py`），保留檔頭的 `sys.path.insert(...)` 樣板，**不要**改成 package-relative import。

### worked example：VLM 走 OpenAI chat completion（vision）

這正是「**輸入塑形留在情境層、傳輸交給既有 adapter**」的範例——一行程式碼都沒加進 `core/` 的 adapter，就讓 `s_vlm.py` 多支援一種後端：

```python
# scenarios/s_vlm.py（節錄）
vision_chat = cfg.ADAPTER.strip().lower() == "openai_chat"
if vision_chat:
    # 把每張圖片塑形成 OpenAI vision messages（base64 data URI）
    inputs = [build_vision_messages(p, cfg.VLM_PROMPT, data_uri=True) for p in images]
else:
    inputs = images                      # vlm/package：直接傳圖片路徑
stream = vision_chat                      # chat 走串流 → 量得 TTFT/TPOT
```

關鍵：`OpenAIChatAdapter._body` 看到 `payload` 是 `list` 就**當成現成 `messages`**，所以情境只要把圖片組成 vision `messages` 清單即可，adapter 不需任何改動。`build_vision_messages()` 內嵌的 base64 在明細的 `input_text` 會被自動省略成前綴標記（`core/client.py` 的 `_payload_text`），報表保持可讀。

---

## 架構地圖

```
config.py                       # .env → 型別化 Config（手寫 .env parser；os.environ 優先）
core/
  client.py                     # make_adapter() 註冊表 + 五個 adapter：
                                #   OpenAIChatAdapter(SSE 串流) / GenericJSONAdapter
                                #   VLMAdapter / CallableAdapter(套件直呼) / custom（延遲匯入）
  runner.py                     # run_single / run_concurrent（ThreadPoolExecutor）＋執行期進度條
  metrics.py                    # RequestResult（單筆契約）+ Summary + summarize()
  reporter.py                   # write_outputs()（Excel 雙頁 + JSON）/ write_json() / print_summary()
  xlsx.py                       # 純標準庫手寫 .xlsx（zipfile + XML，不依賴 openpyxl）
  progress.py                   # 執行期即時進度條（stderr，非 TTY 自動靜默）
  gpu.py                        # GpuSampler（背景輪詢 nvidia-smi；無則 no-op）
  custom_adapter_example.py     # (c) 自訂 adapter 範本（3 個 TODO）
  package_adapter_example.py    # (e) 套件直呼範本
scenarios/                      # s1_interactive / s2_concurrency / s3_batch / s4_bert / s6_stt / s_vlm
  _common.py                    # 輸入塑形（合成 prompt / 資料集 / 圖片 / vision messages）、輸出檔名
tests/                          # mock_server.py（假 OpenAI SSE）+ test_integration.py（整鏈路斷言）
data/                           # prompts.sample.txt、images/
specs/001-h100-pro6000/         # spec.md / plan.md / tasks.md（FR/AC 來源）
```

---

## 範圍與待補

- 納入情境 **①②③④⑥ + VLM**；排除 ⑤訓練、⑦純品質回歸（字錯率等）。
- ④BERT / ⑥STT 的確切 request/response 形狀、⑥音檔時長來源：**依實際端點調整**（程式中以 TODO 標示；目前 STT 以佔位字串讓流程可跑通）。
- 各情境的 H100 基準值與 SLA 驗收門檻：由 PM/業務提供後填入 `.env`（`SLA_TTFT_MS` / `SLA_P95_MS`）。
