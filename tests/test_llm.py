"""LLM 客户端单元测试：过滤条件 JSON 解析 + 回答生成（stub complete）。"""

import pytest

from app.rag.llm import FALLBACK_ANSWER, LLMClient, parse_answer_result, parse_filter_json


def test_parse_plain_json():
    assert parse_filter_json('{"拍身重量(U)": ["4U"]}') == {"拍身重量(U)": ["4U"]}


def test_parse_fenced_json():
    assert parse_filter_json('```json\n{"最高磅数>=": 28}\n```') == {"最高磅数>=": 28}


def test_parse_with_surrounding_text():
    assert parse_filter_json('条件如下：{"a": 1} 完毕') == {"a": 1}


def test_parse_invalid_returns_empty():
    assert parse_filter_json("不是 JSON") == {}
    assert parse_filter_json("") == {}
    assert parse_filter_json("[1, 2, 3]") == {}
    assert parse_filter_json("这里没有大括号") == {}


class StubLLM(LLMClient):
    """不调用 OpenAI 的测试桩：按序弹出预置响应。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.last_messages: list | None = None
        self.last_json_mode: bool | None = None

    def complete(self, messages, *, json_mode: bool = False) -> str:
        self.last_messages = messages
        self.last_json_mode = json_mode
        return self._responses.pop(0)


def test_extract_filters_parses_and_uses_json_mode():
    llm = StubLLM(['{"拍身重量(U)": ["4U"]}'])
    out = llm.extract_filters("推荐4U球拍")
    assert out == {"拍身重量(U)": ["4U"]}
    assert llm.last_json_mode is True
    # system prompt 应包含可用字段与 JSON 示例；且要求知识/对比类问题不输出过滤条件
    system = llm.last_messages[0]["content"]
    assert "拍身重量(U)" in system
    assert "最高磅数>=" in system
    assert "json" in system.lower()
    assert "哪个更耐打" in system
    assert "{}" in system


def test_extract_filters_invalid_returns_empty():
    llm = StubLLM(["不确定，没有条件"])
    assert llm.extract_filters("随便问问") == {}


def test_extract_filters_empty_response_returns_empty():
    llm = StubLLM([""])
    assert llm.extract_filters("随便问问") == {}


def test_extract_filters_returns_empty_on_exception():
    class Boom(LLMClient):
        def complete(self, messages, *, json_mode: bool = False) -> str:
            raise RuntimeError("API 挂了")

    assert Boom("http://x", "k", "m").extract_filters("问") == {}


def test_parse_answer_result_structured():
    assert parse_answer_result('{"answer": "推荐天斧99", "used": [1, 2]}') == {
        "answer": "推荐天斧99",
        "used": [1, 2],
    }


def test_parse_answer_result_fenced():
    assert parse_answer_result('```json\n{"answer": "答案", "used": [3]}\n```') == {
        "answer": "答案",
        "used": [3],
    }


def test_parse_answer_result_sanitizes_used():
    out = parse_answer_result('{"answer": "答案", "used": [1, true, "3", "x", 2.5]}')
    assert out == {"answer": "答案", "used": [1, 3]}


def test_parse_answer_result_fallback_to_original_text():
    assert parse_answer_result("回答正文，不是 JSON") == {
        "answer": "回答正文，不是 JSON",
        "used": [],
    }
    assert parse_answer_result("") == {"answer": "", "used": []}


def test_generate_answer_fallback_without_context():
    llm = StubLLM([])
    assert llm.generate_answer("问题", []) == {"answer": FALLBACK_ANSWER, "used": []}


def test_generate_answer_builds_context_and_returns():
    llm = StubLLM(['{"answer": "推荐这款球拍。\\n来源：尤尼克斯 YONEX 天斧99", "used": [1]}'])
    contexts = [
        {
            "document": "尤尼克斯 YONEX 天斧99，重量4U，进攻型。",
            "metadata": {"品牌": "尤尼克斯 YONEX", "型号": "天斧99"},
        }
    ]
    out = llm.generate_answer("推荐4U进攻球拍", contexts)
    assert "推荐这款球拍" in out["answer"]
    assert out["used"] == [1]
    assert llm.last_json_mode is True
    # 用户消息应包含编号检索内容与来源
    user = llm.last_messages[1]["content"]
    assert "尤尼克斯 YONEX 天斧99" in user
    assert "来源：尤尼克斯 YONEX 天斧99" in user
    # system prompt 应要求只输出 JSON，并说明 used 语义与兜底
    system = llm.last_messages[0]["content"]
    assert "只输出 JSON" in system
    assert "used" in system
    assert "知识库中暂无相关信息" in system


def test_generate_answer_non_json_falls_back_to_original_text():
    llm = StubLLM(["这不是 JSON"])
    contexts = [{"document": "文档", "metadata": {"品牌": "A", "型号": "B"}}]
    assert llm.generate_answer("问题", contexts) == {"answer": "这不是 JSON", "used": []}


def test_generate_answer_empty_response_falls_back():
    llm = StubLLM([""])
    contexts = [{"document": "文档", "metadata": {"品牌": "A", "型号": "B"}}]
    assert llm.generate_answer("问题", contexts) == {"answer": FALLBACK_ANSWER, "used": []}


def test_generate_answer_exception_falls_back():
    class Boom(LLMClient):
        def complete(self, messages, *, json_mode: bool = False) -> str:
            raise RuntimeError("API 挂了")

    contexts = [{"document": "文档", "metadata": {"品牌": "A", "型号": "B"}}]
    assert Boom("http://x", "k", "m").generate_answer("问题", contexts) == {
        "answer": FALLBACK_ANSWER,
        "used": [],
    }
