"""套件版 adapter 範本：模型已封裝成 Python 套件，直接在程式內呼叫（非 HTTP）。

把你的套件呼叫包成一個 callable `fn(image_path) -> 文本`（或回傳逐 token 的 generator），
交給 `core.client.CallableAdapter`。框架負責計時、收文字、算統計，runner/metrics/reporter 完全共用。

啟用：`.env` 設 `ADAPTER=package`（make_adapter 會呼叫此處的 build_callable）。
"""
from __future__ import annotations


def build_callable(config):
    """回傳 fn(payload)->文本。VLM 套件版的 payload 為圖片路徑（套件自行讀圖，免 base64）。"""
    prompt = config.VLM_PROMPT
    max_tokens = config.MAX_TOKENS

    # TODO(1): import 你的 VLM 套件，並在此載入一次模型（避免每次呼叫重載）
    #   from my_vlm import VLM
    #   model = VLM.load("/path/to/weights")

    def infer(image_path):
        # TODO(2): 呼叫你的套件，回傳辨識出的文本字串：
        #   return model.generate(image=image_path, prompt=prompt, max_tokens=max_tokens)
        #
        # 若套件是逐 token 產出，改回傳 generator，框架會量 TTFT/TPOT：
        #   def gen():
        #       for tok in model.stream(image=image_path, prompt=prompt):
        #           yield tok
        #   return gen()
        #
        # 以下為「尚未接上真實套件」的佔位回應，讓流程先跑通；接上後請刪除：
        return f"[demo] described {image_path} with prompt={prompt!r} (max_tokens={max_tokens})"

    return infer
