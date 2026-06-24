# -*- coding: utf-8 -*-
"""請求 body：通用版 OpenAI 相容 body builder。

預設由這支組 body；打 body 結構不同的服務時，在設定區把 BODY_BUILDER 指到自己的函式
（簽章需與 build_body 相同），由 runner 在組 body 階段改用它。
"""
from __future__ import annotations


def build_body(prompt, *, model, max_tokens, temperature, stream, reasoning):
    """組 OpenAI 相容 /v1/chat/completions 請求 body。

    chat_template_kwargs.enable_thinking 控制推理模型是否輸出思考內容（vLLM / SGLang 慣例）；
    串流時另加 stream_options.include_usage 以取精確 token 數。
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": str(prompt)}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
        "chat_template_kwargs": {"enable_thinking": reasoning},   # 思考模式開關
    }
    if stream:
        body["stream_options"] = {"include_usage": True}          # 要求末包附 usage，取精確 token 數
    return body


# 自訂 body 範例：打嚴格服務（如真正 OpenAI API）—— 拿掉 vLLM 專屬欄位、加取樣參數。
# 要用就解除註解、把設定區的 BODY_BUILDER 設成 _example_body_builder。
# def _example_body_builder(prompt, *, model, max_tokens, temperature, stream, reasoning):
#     body = build_body(prompt, model=model, max_tokens=max_tokens,
#                       temperature=temperature, stream=stream, reasoning=reasoning)
#     body.pop("chat_template_kwargs", None)   # 嚴格端點會拒絕未知欄位
#     body["top_p"] = 0.9                       # 視服務需要新增/覆寫
#     return body
