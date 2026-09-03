"""回答校验：生成后验证引用是否真正支撑结论。

LLM 判断「回答中的关键结论能否由给定检索内容支撑」，输出 {"supported": true/false}。
"""

from __future__ import annotations

import logging

from app.rag.llm import LLMClient, parse_filter_json

logger = logging.getLogger(__name__)

_VERIFY_SYSTEM = (
    "你是回答校验员。判断给定的「回答」中的关键结论是否都能由「检索内容」支撑，"
    "禁止凭常识判断，只依据检索内容。"
    '只输出 JSON：{"supported": true 或 false, "reason": "一句话原因"}。不要输出其他文字。'
)


def verify(question: str, answer: str, contexts: list[dict], llm: LLMClient) -> bool:
    """校验回答是否由上下文支撑。异常/解析失败时保守返回 True（不阻断回答）。"""
    if not answer or not contexts:
        return True
    ctx_block = "\n".join(
        f"[{i}] {c.get('document', '')}" for i, c in enumerate(contexts, 1)
    )
    user = f"问题：{question}\n\n检索内容：\n{ctx_block}\n\n回答：\n{answer}"
    try:
        text = llm.complete(
            [
                {"role": "system", "content": _VERIFY_SYSTEM},
                {"role": "user", "content": user},
            ],
            json_mode=True,
        )
        data = parse_filter_json(text)
        supported = data.get("supported")
        if isinstance(supported, bool):
            return supported
        if isinstance(supported, str):
            return supported.strip().lower() in ("true", "yes", "是", "支撑")
    except Exception:
        logger.exception("回答校验失败，保守放行")
    return True
