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
| `python scenarios/s_vlm.py --images "data/images/*" --concurrency 1` | VLM 圖片→文本 | 單張延遲 / 吞吐（張/秒）|

> 比較 H100 vs PRO 6000：把 `BASE_URL`/`MODEL` 指向各自端點，`RUN_LABEL` 標好（如 `H100-FP8`、`PRO6000-FP4`），
> 各跑一輪，再並排比較產出的 CSV 與摘要。

### 用自己的測試資料集當輸入（`--dataset`）
情境 ①②③ 預設用「固定字元長度的合成 prompt」（比較時輸入長度一致、可控）。
若要改用自己的題庫，加 `--dataset <檔案>`：
- `.csv`：取 `prompt` 欄（找不到欄名 prompt/input/text… 就取第一欄；可含 `id,prompt,...` 多欄，其餘欄忽略）。相容 Excel 匯出的 BOM。
- `.txt`：一行一個 prompt（略過空白行）。

`--n` 仍決定總筆數：資料集不足會循環補滿、過多會截斷（語意同圖片的 `--images`）；給了 `--dataset` 時 `--input-len` 失效。
```bash
python scenarios/s1_interactive.py --dataset data/prompts.sample.txt --n 20 --label H100-FP8
python scenarios/s2_concurrency.py --dataset mydata.csv --concurrency 1,8,16,32 --n 50
```
> ④BERT、⑥STT、VLM 走不同輸入型態（序列長度／音檔／圖片資料夾），不吃 `--dataset`。

## 輸出
每次執行同時產生兩份明細（同檔名、不同副檔名）＋主控台摘要：
- 每筆明細 **CSV**：`results/<情境>_<標籤>_<時間>.csv`
  欄位含 TTFT/TPOT/e2e/tokens/字數/併發/標籤…，以及**原文**：`input_text`（輸入原文）、
  `reasoning_text`（思考內容）、`output_text`（輸出內容），方便用試算表快速檢視。
- 每筆明細 **JSON**：`results/<情境>_<標籤>_<時間>.json`
  保存每筆所有欄位，並額外含 `raw_response`（**完整原始回應**：串流為所有 chunk 清單、非串流為回應物件）。
- 主控台摘要：平均、P50/P95/P99、系統總吞吐，以及（若開啟）GPU 使用率/記憶體。

> 思考內容（reasoning）：串流取 `delta.reasoning_content`（相容 `reasoning`）、非串流取
> `message.reasoning_content`（相容 `reasoning`）；模型無此欄位則 `reasoning_text` 留空。
> 推理模型的 reasoning 與 content 都算「已生成輸出」：**TTFT 取第一個 token（不分思考/內容）**，
> TPOT、`tokens_per_s`、`chars_per_s` 與 `output_tokens`/`output_chars` 皆涵蓋兩者，與
> `usage.completion_tokens`（含 reasoning）一致，避免推理模型的吞吐被灌水。
> 其中 `output_text` 只放最終回覆、`output_chars` 則為「思考＋內容」字數合計；原文字串本身僅供檢視、不另參與計算。

## 接入既有服務（測試你自己的程式）
**唯一接縫在 adapter**：`runner` / `metrics` / `reporter` / 情境腳本完全不用改。

呼叫模型有兩種形態：
- **HTTP 服務**：用 `requests` 打端點 →（a）（b）（c）（d）。
- **已封裝成 Python 套件**：在程式內直接呼叫、不經 HTTP →（e）。

兩種形態都回傳同一個 `RequestResult`，後續統計與 CSV/摘要完全共用。

**步驟**：① 辨識你的服務協定 → ② 選 adapter（設 `.env` 的 `ADAPTER`）→ ③ 填對應設定 →
④ 準備輸入（文字 prompt 或圖片資料夾）→ ⑤ 跑對應情境腳本 → ⑥ 看 CSV 明細與主控台摘要。

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

**(d) 多模態 / 圖片輸入（VLM：圖片→文本）→ 零程式，純設定**
適用「輸入圖片、輸出文本」的自訂 JSON（base64）HTTP 服務：
```ini
ADAPTER=vlm
VLM_URL=http://你的VLM服務/predict
VLM_PROMPT=請描述這張圖片的內容。               # 所有圖片共用的提示詞
VLM_REQUEST_TEMPLATE={"image": "{image_b64}", "prompt": "{prompt}"}  # {image_b64} 為圖片 base64
VLM_RESPONSE_PATH=output                          # 從回應取文本的點路徑
VLM_IMAGE_DATA_URI=false                          # 若你的服務要 data:image/...;base64, 前綴則設 true
```
把圖片放進資料夾，跑：
```bash
python scenarios/s_vlm.py --images "data/images/*.jpg" --concurrency 1   # 單張延遲
python scenarios/s_vlm.py --images "data/images/*.jpg" --concurrency 8   # 吞吐 / 並發路數
```
> 其他 VLM 形態：若服務是 **OpenAI 相容 vision**，可改用 `ADAPTER=openai_chat` 並在訊息放 `image_url`（需小幅自訂 `_body`）；
> 若是 **multipart 上傳圖檔**，照 (c) 複製 `custom_adapter_example.py` 實作上傳即可。

**(e) 已封裝成 Python 套件（直接呼叫，非 HTTP）**
模型若已包成套件、直接 import 呼叫拿結果：編輯 `core/package_adapter_example.py` 的 2 個 TODO
（import 套件、把「載入圖片→呼叫→取文本」包成 `fn(payload)->文本`），再於 `.env` 設 `ADAPTER=package`。
跑法與 (d) 相同（`python scenarios/s_vlm.py ...`），免起 HTTP 伺服器。
- 套件若**逐 token 產出**（回 generator），框架會量 TTFT/TPOT；一次回完整文字則量端到端。
- 並發注意：走 ThreadPoolExecutor，受 GIL 影響；多數推論套件於 GPU/C++ 推論時會釋放 GIL，threaded 並發仍能反映真實吞吐；
  純 Python CPU-bound 不釋放 GIL 時，並發數據僅供參考。

> 串流注意：TTFT/TPOT 需要串流；不串流的服務，這兩個指標會自動退化為以端到端延遲為準、TPOT 不適用，其餘照常。

## 架構
```
config.py              # .env → 型別化 Config
core/
  client.py            # make_adapter() 工廠 + OpenAIChatAdapter(串流) + GenericJSONAdapter
  custom_adapter_example.py
  runner.py            # run_single / run_concurrent（ThreadPoolExecutor）
  metrics.py           # RequestResult + summarize()
  reporter.py          # write_csv() / write_json() / write_outputs() + print_summary()
  gpu.py               # GpuSampler（背景 nvidia-smi）
scenarios/             # s1_interactive / s2_concurrency / s3_batch / s4_bert / s6_stt
tests/                 # mock_server.py（假 OpenAI SSE）+ test_integration.py
```

## 範圍與待補
- 納入情境 ①②③④⑥；排除 ⑤訓練、⑦純品質回歸。
- ④BERT / ⑥STT 的確切 request/response 形狀、⑥音檔時長來源：依實際端點調整（程式中以 TODO 標示）。
- 各情境 H100 基準值與驗收門檻（Excel 橘色欄）：由 PM/業務提供後填入 `.env` 的 SLA。
