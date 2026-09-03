"""Agent 节点 span 记录测试：注入 NullTracer，断言 span 顺序与内容（离线，不触网）。

用 chitchat 路由短路：route → chitchat → END，无需任何入库数据。
"""

from app.agent.graph import BadmintonAgent
from app.ingest.embedder import FakeEmbedder
from app.ingest.store import VectorStore
from app.observability.tracer import NullTracer
from app.rag.retriever import Retriever
from scripts.eval_agent import OfflineAgentLLM


def _agent(tracer=None) -> BadmintonAgent:
    store = VectorStore()
    embedder = FakeEmbedder()
    retriever = Retriever(store, embedder, use_bm25=False)
    return BadmintonAgent(retriever, OfflineAgentLLM("chitchat"), memory=None, tracer=tracer)


def _invoke(agent) -> dict:
    return agent.invoke(
        {
            "question": "你好",
            "session_id": "s",
            "history": [],
            "contexts": [],
            "sources": [],
            "sub_questions": [],
            "retry_count": 0,
            "trace": [],
        }
    )


def test_agent_spans_recorded_in_order():
    tracer = NullTracer()
    agent = _agent(tracer)
    state = _invoke(agent)
    names = [s["name"] for s in tracer.spans()]
    assert names == ["route", "chitchat"]
    assert tracer.spans()[0]["output"]["route"] == "chitchat"
    # span 与 state["trace"] 节点一一对应（同 trace_id 可互相索引）
    assert names == [t["node"] for t in state["trace"]]


def test_agent_trace_state_unchanged_with_tracer():
    """接入 tracer 不改变 state["trace"] 结构（与 Phase 3 行为一致）。"""
    agent = _agent(NullTracer())
    state = _invoke(agent)
    assert [t["node"] for t in state["trace"]] == ["route", "chitchat"]
    assert all(set(t) == {"node", "input", "output"} for t in state["trace"])


def test_agent_token_attribution_via_attach_llm():
    """真实 LLMClient 形态：attach_llm 把 usage_hook 挂上（桩 LLM complete 触发）。"""
    class UsageLLM(OfflineAgentLLM):
        def __init__(self):
            super().__init__("chitchat")
            self.usage_hook = None

        def complete(self, messages, *, json_mode=False):
            text = super().complete(messages, json_mode=json_mode)
            if self.usage_hook is not None:
                self.usage_hook({"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4})
            return text

    tracer = NullTracer()
    store = VectorStore()
    embedder = FakeEmbedder()
    retriever = Retriever(store, embedder, use_bm25=False)
    agent = BadmintonAgent(retriever, UsageLLM(), memory=None, tracer=tracer)
    _invoke(agent)
    summary = tracer.token_summary()
    # route 节点与 chitchat 节点各触发一次 LLM 调用 → 各 4 token
    assert summary["route"]["total_tokens"] == 4
    assert summary["chitchat"]["total_tokens"] == 4
