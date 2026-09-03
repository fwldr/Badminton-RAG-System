"""Agent 工具层测试：equipment 查询 / 定向检索 / 拆解 / 闲聊（FakeEmbedder + 内存库，不触网）。"""

import json

import pytest

from app.agent.tools import chitchat, decompose, equipment_query, rag_search
from app.ingest.embedder import FakeEmbedder
from app.ingest.pipeline import ingest_table
from app.ingest.serializer import ALL_TABLES
from app.ingest.store import VectorStore
from app.rag.retriever import Retriever


@pytest.fixture()
def env():
    store = VectorStore()  # 内存版
    embedder = FakeEmbedder()
    # 只入 3 张表提速：手胶（规格）、BWF官方规则（规则）、手法技术（技术）
    from app.core.config import get_settings

    for table in ALL_TABLES:
        if table.name in ("手胶", "BWF官方规则", "手法技术"):
            ingest_table(store, embedder, table, get_settings().processed_data_dir)
    retriever = Retriever(store, embedder, use_bm25=False)
    return store, retriever


class StubLLM:
    """可控返回的 stub LLM。"""

    def __init__(self, filter_conditions=None, sub_questions=None, chitchat_text=None):
        self._conds = filter_conditions
        self._subs = sub_questions
        self._chat = chitchat_text

    def extract_filters(self, question: str) -> dict:
        return self._conds or {}

    def complete(self, messages, *, json_mode=False) -> str:
        # 拆解调用（json_mode=True）→ 返回子问题；否则闲聊
        if json_mode and self._subs is not None:
            return json.dumps({"sub_questions": self._subs}, ensure_ascii=False)
        return self._chat or "你好！"


def test_equipment_query_with_filter(env):
    _, retriever = env
    llm = StubLLM(filter_conditions={"颜色": ["红"]})
    contexts, conditions = equipment_query("推荐红色的手胶", retriever, llm)
    assert conditions == {"颜色": ["红"]}
    # FakeEmbedder 下按颜色过滤后应有命中（grip 表存在含红手胶）
    assert isinstance(contexts, list)


def test_equipment_query_no_filter(env):
    _, retriever = env
    llm = StubLLM(filter_conditions={})
    contexts, conditions = equipment_query("推荐一款手胶", retriever, llm)
    assert conditions == {}
    assert len(contexts) > 0


def test_rag_search_rules_only(env):
    _, retriever = env
    contexts = rag_search("发球高度限制", retriever, "rules", top_k=5)
    assert len(contexts) <= 5
    # 定向检索只返回 bwf_rules / common_penalties
    assert all(c["table"] in ("bwf_rules", "common_penalties") for c in contexts)


def test_rag_search_technique_only(env):
    _, retriever = env
    contexts = rag_search("正手握拍要领", retriever, "technique", top_k=5)
    assert all(c["table"] in ("hand_techniques", "footwork_techniques", "tactics") for c in contexts)


def test_decompose(env):
    _, retriever = env
    llm = StubLLM(sub_questions=["夏天选什么球速", "冬天选什么球速"])
    subs = decompose("夏天和冬天选什么球速", llm)
    assert subs == ["夏天选什么球速", "冬天选什么球速"]


def test_decompose_fallback(env):
    _, retriever = env
    llm = StubLLM(sub_questions=[])
    subs = decompose("复杂问题", llm)
    assert subs == ["复杂问题"]  # 空 → 回退原问题


def test_chitchat(env):
    _, retriever = env
    llm = StubLLM(chitchat_text="你好！我是羽毛球助手")
    assert chitchat("你好", llm) == "你好！我是羽毛球助手"
