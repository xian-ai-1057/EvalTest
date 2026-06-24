# H100 vs RTX PRO 6000 跑分（最簡化版）

自製的推論跑分小工具，用來評估 **RTX PRO 6000 (Blackwell) 能否取代目前跑在 H100 上的推論工作負載**。
直接用 `requests` 對 OpenAI 相容（或自訂）的 chat 端點發請求，量測延遲與吞吐，輸出可直接判讀達標與否的報表。

專案只有兩支工具：

- **`simple_bench.py`**：對端點跑分（計時、並發），輸出 Excel ＋ JSON ＋ 主控台摘要。
- **`accuracy.py`**：把跑分產出與「正解」做完全相等比對，算準確率。

每次跑 `simple_bench.py` 產生：

- **一個 Excel 報表**（`.xlsx`，用 pandas + openpyxl 寫）：第①頁＝統計摘要、第②頁＝每筆明細（pandas 轉換）、第③頁＝本次執行參數。
- **一個 JSON**（與 `.xlsx` 同檔名）：一個結構化 dict —— `{summary, params, detail}`；`detail` 是每筆所有欄位 ＋ 完整原始回應 `raw_response`，也是 `accuracy.py` 讀的對象。
- **主控台摘要**：平均／百分位／系統總吞吐。

設計原則：第三方相依只有 **`requests` ＋ `pandas` ＋ `openpyxl`**（pandas/openpyxl 僅用於輸出）；其餘只用 Python 標準庫（metrics 自算百分位、`accuracy.py` 的 `.xlsx` 仍由 `core/xlsx.py` 手寫、測試為純斷言腳本）；目標 **Python 3.12**（亦相容 3.11）。非生產專案——以「拿到數字、找出瓶頸」為目標。

> 程式碼註解、docstring 與主控台字串一律使用**繁體中文**；修改時請沿用此慣例。

---

## 安裝

```bash
pip install -r requirements.txt        # requests + pandas + openpyxl
```

無需 `.env`、無需設定檔——所有參數都在各檔案頂部的「參數設定區」用 Python 變數調整。

---

## `simple_bench.py` — 對端點跑分

編輯檔案頂部「參數設定區」後直接執行：

```bash
python simple_bench.py
```

| 參數 | 預設 | 說明 |
|------|------|------|
| `BASE_URL` | `http://127.0.0.1:8000` | 伺服器根；自動接 `API_PATH` |
| `API_PATH` | `/v1/chat/completions` | 端點路徑（接在 `BASE_URL` 後）；打非 OpenAI 服務時改這個 |
| `MODEL` | `test-model` | 模型名稱 |
| `API_KEY` | （空） | 需要時填，會以 `Authorization: Bearer` 帶上 |
| `RUN_LABEL` | `H100-FP8` | 報表標籤，跨卡比較用（如 `H100-FP8` / `PRO6000-FP4`） |
| `N_REQUESTS` | `20` | 請求筆數 |
| `CONCURRENCY` | `4` | 並發數（`1`＝單發） |
| `MAX_TOKENS` | `256` | 輸出長度上限 |
| `TEMPERATURE` | `0.0` | 取樣溫度 |
| `STREAM` | `True` | `True` 量 TTFT/TPOT；`False` 只量端到端 e2e |
| `REASONING` | `True` | 思考模式開關（帶進請求 body 的 `chat_template_kwargs.enable_thinking`） |
| `INPUT_LEN` | `512` | 合成輸入字元長度（`DATASET` 留空時用） |
| `DATASET` | （空） | 留空＝合成輸入；或填 CSV/TXT 路徑（見下） |
| `OUTPUT_DIR` | `results` | 報表輸出資料夾 |
| `REQUEST_TIMEOUT` | `60.0` | 單請求逾時（秒） |

### 用自己的資料集（`DATASET`）

把 `DATASET` 指到檔案即可：

- **`.csv`**：以 `utf-8-sig` 讀（相容 Excel 匯出的 BOM）。表頭認 prompt 欄（`prompt`/`input`/`text`/`question`/`問題`/`輸入`）與 answer 欄（`answer`/`答案`/`正解`/`label`/`標籤`/`ground_truth`）；無可辨識表頭則取**第一欄**為 prompt、無正解。
- **`.txt`**（及其他副檔名）：**一行一個 prompt**，略過空白行、無正解。

`N_REQUESTS` 決定總筆數：資料集**不足會循環補滿、過多會截斷**。帶 `answer` 的每筆，正解會一併寫進輸出明細與 JSON 的 `answer` 欄，供 `accuracy.py` 比對。

---

## `accuracy.py` — 算準確率

讀一份同時含「正解」與「模型回覆」的資料（`simple_bench` 產出的 JSON，或自備 CSV），逐筆 `strip()` 後**完全相等**比對（正解為空者跳過），印出準確率並輸出雙頁 Excel（準確率摘要 / 逐筆比對）。

編輯參數設定區後執行：

```bash
python accuracy.py
```

| 參數 | 預設 | 說明 |
|------|------|------|
| `INPUT_PATH` | （範例字串） | `simple_bench` 產出的 `.json`，或自備 `.csv` |
| `ANSWER_FIELD` | `answer` | 要比對的「正解」欄位名 |
| `REPLY_FIELD` | `output_text` | 要比對的「模型回覆」欄位名 |
| `OUT_PATH` | （空） | 留空＝輸入檔同名 `_accuracy.xlsx` |

指定的欄名若不存在，程式會印出檔案裡可用的欄位清單，請回設定區改成正確欄名再執行。

> **典型流程**：編輯 `simple_bench.py` 設定區（含帶 `answer` 的 `DATASET`）→ `python simple_bench.py` → 把 `accuracy.py` 的 `INPUT_PATH` 指到產出的 `.json` → `python accuracy.py`。

---

## 不需 GPU 的驗證

不必有 GPU 也能把 `simple_bench` / `accuracy` 的核心路徑跑通：

```bash
# 一鍵煙霧測試（自動起假 OpenAI 伺服器、檢查 simple_bench + accuracy）
python tests/test_integration.py

# 或手動起假伺服器，再把 simple_bench.py 的 BASE_URL 指過去自己跑一輪
python tests/mock_server.py --port 8000
# 另開終端：BASE_URL=http://127.0.0.1:8000、MODEL=mock，python simple_bench.py
```

`test_integration.py` 是**單一斷言腳本**（無 pytest、無測試框架）。要「跑測試」就跑整支腳本、看它輸出的檢查清單；新增行為時往這支檔案再加一個 `_check(...)` 區塊。

---

## 輸出與判讀

### 檔名規則

```
results/simple_<標籤>_<時間戳>.xlsx     # 例：results/simple_H100-FP8_20260619-021451.xlsx
results/simple_<標籤>_<時間戳>.json     # 同檔名、不同副檔名
```

時間戳為 `YYYYMMDD-HHMMSS`；`results/` 已被 gitignore。

### JSON 結構 ／ Excel 第②頁「明細」

JSON 最外層是一個 dict：`{summary, params, detail}`（`summary`＝統計、`params`＝執行參數、`detail`＝每筆明細陣列）；`accuracy.py` 會自動取其 `detail`。下表為 `detail` 每筆（＝Excel 第②頁每列）的欄位，欄序＝`RequestResult`（`core/metrics.py`）的**欄位宣告順序**：

| 欄位 | 意義 |
|------|------|
| `scenario` | 情境代號（固定 `simple_bench`） |
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
| `reasoning_text` | 思考內容（`reasoning_content`；無則空） |
| `output_text` | 輸出內容（最終回覆文字） |
| `answer` | 正解（`DATASET` 帶答案時填入，供 `accuracy.py` 比對） |

**`raw_response`（完整原始回應）只進 JSON 的 `detail`、不進 Excel 明細**（標記 `metadata={"csv": False}`）：JSON 會試著還原成巢狀物件，便於檢視思考/輸出/usage。

### 指標怎麼判讀

- **TTFT（首字延遲）**：使用者送出後多久看到第一個字。互動體感的關鍵；只有串流量得到。
- **TPOT（逐字延遲）**：實為「逐 chunk 延遲」。多數 OpenAI 相容後端 1 token/chunk，故近似逐字。
- **e2e（端到端）**：整個請求從送出到收完的秒數。
- **單請求 `tokens_per_s`**：以**解碼階段**（`e2e − ttft`）計，反映「開始吐字後的生成速度」，不含排隊/首字等待。
- **系統總吞吐**：`Σtokens ÷ wall_seconds`，由並發牆鐘算出，代表整卡在該併發下的真實產能。
- **成功率／失敗隔離**：單筆例外（連線錯誤、HTTP 4xx/5xx、回應非 JSON）記為 `success=False` 並保住 `status_code`，**不會拖垮整批**；統計只納入成功筆。

### 推理模型的計量

`reasoning_content`（相容 `reasoning`）與正式 `content` **都算「已生成輸出」**：TTFT 取第一個 token（不分思考/內容），TPOT、`output_tokens`、`output_chars` 皆涵蓋兩者，與 `usage.completion_tokens`（含 reasoning）一致，避免吞吐被灌水。`output_text` 只保留最終回覆；後端未回 `usage` 時，`output_tokens` 退化為「有內容的 SSE chunk 數」當代理值，跨後端比較請以有回 usage 者為準。

---

## 比較 H100 vs PRO 6000

1. 把 `BASE_URL`/`MODEL` 改成各自端點；
2. 設好可區分的 `RUN_LABEL`（如 `H100-FP8` vs `PRO6000-FP4`）；
3. 每張卡各跑一次 `python simple_bench.py`；
4. 並排比較 `results/` 中產出的 `.xlsx`（第①頁統計摘要）與 `.json`。

---

## 架構地圖

```
simple_bench.py     # 薄入口：參數設定區 + 線性 main() 管線（串接 core/ 各模組）
accuracy.py         # 伴隨工具：讀產出 JSON 的 detail 做完全相等比對、算準確率（用 core/xlsx.py）
core/
  config.py         # BenchConfig（設定 dataclass）+ as_params()
  dataset.py        # build_prompt / load_dataset / build_pairs（組輸入）
  payload.py        # build_body（OpenAI 相容 body；可被 BODY_BUILDER 取代）
  client.py         # call()：純 HTTP 呼叫 + SSE/單發解碼（不組 body）
  runner.py         # run_benchmark()：組 body → client.call → 並發 + stamp
  metrics.py        # RequestResult（單筆契約）+ Summary + summarize()
  reporter.py       # build_report()→dict / write_outputs()（pandas→Excel + JSON）/ print_summary()
  xlsx.py           # 純標準庫手寫 .xlsx（zipfile + XML）—— 現只給 accuracy.py 用
tests/
  mock_server.py    # 假 OpenAI SSE 伺服器（純標準庫），無 GPU 也能驗證
  test_integration.py  # 重構後管線 + accuracy 煙霧測試（單一斷言腳本）
```
