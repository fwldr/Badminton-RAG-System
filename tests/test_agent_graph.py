"""LangGraph 编排测试：路由分流 / 工具 / 校验回边（stub LLM + FakeEmbedder，不触网）。"""

import pytest

from app.agent.graph import BadmintonAgent
from app.ingest.embedder import FakeEmbedder
from app.ingest.pipeline import ingest_table
from app.ingest.serializer import ALL_TABLES
from app.ingest.store import VectorStore
from app.rag.retriever import Retriever


@pytest.fixture()
def env():
    store = VectorStore()
    embedder = FakeEmbedder()
    from app.core.config import get_settings

    for table in ALL_TABLES:
        if table.name in ("手胶", "BWF官方规则", "手法技术"):
            ingest_table(store, embedder, table, get_settings().processed_data_dir)
    retriever = Retriever(store, embedder, use_bm25=False)
    return store, retriever


class StubAgentLLM:
    """可控 Agent 全链路 LLM：route / 过滤 / 拆解 / 生成 / 校验。"""

    def __init__(self, route="equipment", verify_supported=True, generate_answer="测试回答", generate_used=None):
        self._route = route
        self._verify = verify_supported
        self._answer = generate_answer
        self._used = generate_used or []

    def complete(self, messages, *, json_mode=False) -> str:
        last = messages[-1]["content"] if messages else ""
        system = messages[0]["content"] if messages else ""
        # 路由调用：system 含"路由助手"
        if "路由助手" in system:
            return f'{{"route": "{self._route}"}}'
        # 校验调用：system 含"校验员"
        if "校验员" in system:
            return f'{{"supported": {str(self._verify).lower()}}}'
        # 生成调用：json_mode=True 且 system 含"问答助手"
        if json_mode and "问答助手" in system:
            return f'{{"answer": "{self._answer}", "used": {str(self._used)}}}'
        # 闲聊（json_mode=False）
        return "你好！"

    def extract_filters(self, question: str) -> dict:
        return {}


def _run(agent, question):
    return agent.invoke(
        {
            "question": question,
            "session_id": "t",
            "history": [],
            "contexts": [],
            "sources": [],
            "sub_questions": [],
            "retry_count": 0,
            "trace": [],
        }
    )


def test_chitchat_route_short_circuit(env):
    _, retriever = env
    agent = BadmintonAgent(retriever, StubAgentLLM(route="chitchat"))
    result = _run(agent, "你好")
    assert result["answer"] == "你好！"
    assert result["sources"] == []
    # trace 含 route 与 chitchat 节点
    nodes = [t["node"] for t in result["trace"]]
    assert "route" in nodes and "chitchat" in nodes
    assert "generate" not in nodes  # 闲聊不生成


def test_equipment_route_generates(env):
    _, retriever = env
    agent = BadmintonAgent(retriever, StubAgentLLM(route="equipment", generate_answer="推荐一款手胶：GP203。", generate_used=[1]))
    result = _run(agent, "推荐红色的手胶")
    assert result["answer"] == "推荐一款手胶：GP203。"
    nodes = [t["node"] for t in result["trace"]]
    assert "equipment" in nodes and "generate" in nodes and "verify" in nodes


def test_verify_fail_retry_once(env):
    """校验不支撑 → 重检索一次 → 仍不支撑 → 降级（retry_count 有上限）。"""
    _, retriever = env
    llm = StubAgentLLM(route="equipment", verify_supported=False, generate_answer="无依据回答")
    agent = BadmintonAgent(retriever, llm)
    result = _run(agent, "某问题")
    nodes = [t["node"] for t in result["trace"]]
    # 重试只发生一次（retry 节点最多 1 次）
    assert nodes.count("retry") == 1
    # 最终降级为兜底
    assert result["answer"] == "知识库中暂无相关信息"


def test_verify_pass_no_retry(env):
    _, retriever = env
    llm = StubAgentLLM(route="rules", verify_supported=True, generate_answer="发球高度1.15m。", generate_used=[1])
    agent = BadmintonAgent(retriever, llm)
    result = _run(agent, "发球高度限制")
    nodes = [t["node"] for t in result["trace"]]
    assert "verify" in nodes
    assert "retry" not in nodes
    assert result["answer"] == "发球高度1.15m。"


def test_verify_disabled(env):
    _, retriever = env
    llm = StubAgentLLM(route="technique", verify_supported=False, generate_answer="技术回答。")
    agent = BadmintonAgent(retriever, llm, use_verifier=False)
    result = _run(agent, "正手握拍")
    assert result["answer"] == "技术回答。"  # 校验关闭 → 直接返回


def test_multi_route_decompose(env):
    _, retriever = env
    llm = StubAgentLLM(route="multi", generate_answer="综合回答。", generate_used=[])
    agent = BadmintonAgent(retriever, llm)
    result = _run(agent, "夏天和冬天选什么球速")
    nodes = [t["node"] for t in result["trace"]]
    assert "multi" in nodes
    assert "generate" in nodes
