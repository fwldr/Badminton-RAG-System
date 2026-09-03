"""百炼 DashScope（OpenAI 兼容）LLM 客户端：抽取过滤条件 JSON + 生成回答。"""

from __future__ import annotations

import json
import logging
import re

from openai import OpenAI

from app.rag.filters import FILTERABLE_FIELDS
from app.rag.retriever import resolve_source

logger = logging.getLogger(__name__)

FALLBACK_ANSWER = "知识库中暂无相关信息"


def parse_filter_json(text: str) -> dict:
    """从 LLM 输出中解析过滤条件 JSON；失败返回空 dict。

    兼容 ```json 围栏、前后杂文本；取第一个 { 到最后一个 }。
    """
    if not text:
        return {}
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            data = json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def parse_answer_result(text: str) -> dict:
    """解析 generate_answer 的结构化返回 JSON → {"answer": str, "used": list[int]}。

    兼容 ```json 围栏、前后杂文本；解析失败时回退为原文本（used 为空数组），
    保证非结构化输出也能被服务端兜底处理。
    """
    if not text:
        return {"answer": "", "used": []}
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    data = None
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                data = None
    if isinstance(data, dict) and isinstance(data.get("answer"), str):
        used: list[int] = []
        raw = data.get("used", [])
        if isinstance(raw, list):
            for u in raw:
                if isinstance(u, bool):
                    continue
                if isinstance(u, int):
                    used.append(u)
                elif isinstance(u, str) and u.strip().lstrip("-").isdigit():
                    used.append(int(u))
        return {"answer": data["answer"].strip(), "used": used}
    return {"answer": s, "used": []}


class LLMClient:
    """OpenAI 兼容接口的 LLM 客户端（百炼 DashScope / DeepSeek 等）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        usage_hook=None,
    ) -> None:
        """usage_hook：每次 complete 返回 usage 时回调（旁路，异常不影响主链路）；默认 None 零变化。"""
        self.model = model
        self.usage_hook = usage_hook
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    def _emit_usage(self, resp) -> None:
        """把响应的 token 用量回调给 usage_hook（旁路：任何异常只记日志）。"""
        hook = self.usage_hook
        if hook is None:
            return
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        try:
            hook(
                {
                    "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                    "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
                }
            )
        except Exception:
            logger.exception("usage_hook 调用失败（旁路，忽略）")

    def complete(self, messages: list[dict], *, json_mode: bool = False) -> str:
        """发起一次对话补全，返回首个消息文本。"""
        kwargs: dict = {"model": self.model, "messages": messages}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        # 千问 3.x 默认思考链：completion tokens 涨约 12 倍，且长思考下更易判"不确定"而整答兜底（A/B 0/4 → 2/4）。
        kwargs["extra_body"] = {"enable_thinking": False}
        resp = self._client.chat.completions.create(**kwargs)
        self._emit_usage(resp)
        return resp.choices[0].message.content or ""

    def extract_filters(self, question: str) -> dict:
        """根据问题抽取可选的属性过滤条件 JSON；失败返回 {}。"""
        fields = "、".join(sorted(FILTERABLE_FIELDS))
        system = (
            "你是羽毛球装备检索助手。根据用户问题，抽取可用于属性过滤的 JSON 条件对象。\n"
            f"可用的过滤字段有：{fields}。\n"
            "规则：\n"
            "1. 只输出一个 JSON 对象，不要输出任何其他文字；\n"
            "2. 仅当问题明确包含可过滤的规格属性（如 拍身重量(U)、最高磅数、平衡点类别、打法类型、"
            "适合水平、品牌、颜色、球速、材质 等）时才输出过滤条件；\n"
            "3. 精确取值字段用列表表示，例如 {\"拍身重量(U)\": [\"4U\"]}；\n"
            "4. 数值条件用后缀操作符，例如 {\"最高磅数>=\": 28}；\n"
            "5. 知识、对比、规则、打法、材质等非规格属性问题一律输出 {}"
            "（如\"哪个更耐打\"\"有什么区别\"\"怎么打\"\"规则是什么\"）；\n"
            "6. 无过滤条件时输出 {}；\n"
            "7. 输出必须是合法 JSON 对象。"
        )
        user = f"问题：{question}"
        try:
            text = self.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                json_mode=True,
            )
        except Exception:
            logger.exception("抽取过滤条件失败")
            return {}
        return parse_filter_json(text)

    def generate_answer(self, question: str, contexts: list[dict]) -> dict:
        """基于检索上下文生成中文回答；不足以回答时返回兜底文案。

        结构化返回 {"answer": str, "used": list[int]}：answer 为回答正文（末尾附来源），
        used 为被引用的条目编号（对应 prompt 里的 [1][2]…，从 1 开始，0 基下标）。
        解析失败时回退为原文本，used 为空数组。
        """
        if not contexts:
            return {"answer": FALLBACK_ANSWER, "used": []}
        lines = []
        for i, rec in enumerate(contexts, 1):
            brand, model = resolve_source(rec)
            lines.append(f"[{i}] {rec.get('document', '')}（来源：{brand} {model}）")
        context_block = "\n".join(lines)
        system = (
            "你是羽毛球装备问答助手。请只依据下面给出的检索内容回答问题，禁止编造任何信息。\n"
            "要求：\n"
            "1. 只输出 JSON，不要输出任何其他文字；\n"
            '2. 格式为 {"answer": "回答正文（末尾附来源）", "used": [被引用的条目编号数组，'
            "对应检索内容里的 [1][2]…]}；\n"
            "3. 回答正文用中文回答，简洁、直接，末尾必须逐条附「来源：品牌 型号」，与检索内容保持一致；\n"
            "4. 若检索内容不足以回答用户问题，或你无法确定答案，则 answer 为 知识库中暂无相关信息，"
            "且 used 为空数组。"
        )
        user = f"问题：{question}\n\n检索内容：\n{context_block}"
        try:
            text = self.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                json_mode=True,
            )
        except Exception:
            logger.exception("生成回答失败")
            return {"answer": FALLBACK_ANSWER, "used": []}
        result = parse_answer_result(text)
        if not result["answer"]:
            result["answer"] = FALLBACK_ANSWER
        return result
