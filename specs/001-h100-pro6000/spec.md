# Spec — H100 vs RTX PRO 6000 通用測試程式

## 1. WHY（為什麼）
專案要評估 **RTX PRO 6000 (Blackwell) 能否取代目前跑在 H100 上的推論/服務工作負載**。
`H100 vs PRO6000 通用測試情境表.xlsx` 已定義測試情境、方法與指標，但缺少實際可執行的量測程式。
本程式提供一套**自製、簡潔易懂**的測試工具（不依賴 vLLM bench / genai-perf 等官方黑箱），
對兩張卡上的服務發出請求、量測延遲與吞吐，輸出「每筆明細 + 最後平均」，據以判斷是否達標並找出瓶頸。

兩大專案目標（全程把關）：
- **G1 不偏離需求**：本 spec 的需求與驗收條件（AC）即驗收基準；見第 6 節追溯表。
- **G2 模組正確串接**：以介面契約（見 `plan.md`）固定模組接縫，並以整合測試跑通整條鏈路。

## 2. 範圍（Scope）
納入測試情境（對應 Excel 分頁一編號）：
- ① 互動式 LLM 生成（單請求延遲）
- ② LLM 高併發服務（吞吐 / 容量）
- ③ LLM 批次推論（離線吞吐）
- ④ 編碼器分類 / BERT 推論
- ⑥ 語音轉文字（STT，選測）

排除：⑤ 模型訓練（需訓練腳本，非本工具範圍）、⑦ 純品質回歸（屬各模型既有評測腳本）。

## 3. 功能需求（FR）
- **FR1** 以 `requests` 呼叫 OpenAI 相容 `chat/completions`，支援 `stream=true`。
- **FR2** 支援**單發**（併發=1）與**並發**（N worker，含併發掃描）兩種執行模式。
- **FR3** 量測並記錄：TTFT、TPOT、端到端延遲、輸出 token/字數、單請求速率；並發另記 P95 與系統總吞吐。
  另保留原文供人工檢視（不參與統計）：輸入原文、思考內容（reasoning_content）、輸出內容、完整原始回應。
- **FR4** 結果輸出：**每一筆**明細同時寫 CSV（含輸入/思考內容/輸出內容原文）+ JSON（額外含完整原始回應
  `raw_response`）+ 結束時主控台印**平均 / P50 / P95** 摘要。
- **FR5** 設定集中於 `.env`；精度/機器以 `RUN_LABEL` 標記寫入每筆。
- **FR6** GPU 使用率/記憶體：背景輪詢本機 `nvidia-smi` 並彙總（mean/max）；無 `nvidia-smi` 時靜默略過。
- **FR7** 涵蓋情境 ①②③④⑥，各為掛在共用核心上的薄腳本。
- **FR8** ④BERT / ⑥STT 非 chat 格式 → 用可設定的通用 JSON adapter，request/response 形狀留明確設定點。
- **FR9** **可插拔 adapter 工廠**：既有服務可零程式接入（OpenAI 相容只改 `.env`；單純 JSON 純設定）
  或小幅自訂（實作 `call()->RequestResult`），核心與情境腳本不變。
- **FR10** **VLM 圖片→文本**：支援自訂 JSON（base64）的 VLM HTTP 服務（`ADAPTER=vlm`），
  圖片來源為資料夾/glob + 共用提示詞，零程式純設定即可量延遲/吞吐。
- **FR11** **in-process 套件呼叫**：模型若已封裝成 Python 套件，可直接呼叫（非 HTTP，`ADAPTER=package`），
  與 HTTP 共用同一 `RequestResult` 與統計/輸出；套件逐 token 產出時亦可量 TTFT/TPOT。

## 4. 非功能需求（NFR）
- 簡潔易懂、模組可重用；唯一第三方相依 `requests`，其餘標準庫。
- 目標 Python 3.12（程式碼亦相容 3.11 以便本機驗證）。
- 非上線專案：求「拿到結果、找出問題」，不追求完備容錯。

## 5. 驗收條件（AC）
- **AC1**：對假伺服器跑 `s1 --n 10` → 產生 10 列 CSV，摘要含 TTFT/TPOT/e2e 的平均與 P95。
- **AC2**：數值合理 — `TTFT < e2e`；`tokens_per_s ≈ output_tokens ÷ (e2e − ttft)`。
- **AC3**：`s2 --concurrency 1,8,16` → 三個併發級別各有吞吐/P95，CSV 含 `concurrency` 欄；併發↑時總吞吐↑、單請求延遲變差。
- **AC4**：`GPU_MONITOR=true` 且有 `nvidia-smi` 時摘要含 GPU util/mem mean/max；無 `nvidia-smi` 時程式照常完成。
- **AC5**：切換 `RUN_LABEL`（H100-FP8 / PRO6000-FP4）兩輪輸出兩份可並排比較的 CSV。
- **AC6**：既有 OpenAI 相容服務只改 `.env`（`ADAPTER=openai_chat`）即可跑；單純 JSON 服務以 `ADAPTER=generic_json` 純設定可跑；自訂 adapter 範本能被工廠依名載入。
- **AC7**：`ADAPTER=vlm` 對 mock `/predict` 跑通，CSV 每張圖一列且含 e2e/chars；`s_vlm.py` 可由資料夾/glob 取圖。
- **AC8**：`CallableAdapter` 對假函式量到 e2e；對 generator（逐 token）量到 TTFT/TPOT。

## 6. 需求 ↔ 實作 ↔ 驗證 追溯表
| 使用者原始需求 | FR | 實作位置 | 驗證 |
|---|---|---|---|
| py3.12 / 不用官方套件 / 簡潔 | NFR | 全專案、`requirements.txt` 僅 requests | 程式碼審閱 |
| requests 呼叫 | FR1 | `core/client.py` | AC1/AC2 |
| 參數放 .env/config | FR5 | `config.py`,`.env.example` | 程式碼審閱 |
| 通用呼叫/計算接口 | FR1/FR7/FR8/FR9 | `core/`（共用核心）+ `scenarios/`（薄腳本） | AC1/AC3/AC6 |
| 並發與單發 | FR2 | `core/runner.py` | AC1/AC3 |
| 每筆 + 平均 | FR3/FR4 | `core/metrics.py`,`core/reporter.py` | AC1/AC3 |
| 情境② GPU 欄位 | FR6 | `core/gpu.py` | AC4 |
| 情境 ①②③④⑥ | FR7/FR8 | `scenarios/*` | AC1/AC3 |
| 接入既有服務 | FR9 | `core/client.py`（工廠）, `core/custom_adapter_example.py` | AC6 |

## 7. 後續待填（不阻擋本次）
- ④BERT / ⑥STT 的確切 request/response 形狀與 ⑥ 音檔時長來源。
- 各情境的 H100 基準值與驗收門檻（Excel 橘色欄），由 PM/業務提供後填入 `.env` 的 SLA 設定。
