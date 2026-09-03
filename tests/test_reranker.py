"""reranker 单元测试：FakeReranker 不改变链路；SiliconFlowReranker payload 与解析（MockTransport）。"""

import json

import httpx
import pytest

from app.core.config import Settings
from app.ingest.embedder import FakeEmbedder
from app.ingest.store import VectorStore
from app.rag.llm import LLMClient
from app.rag.reranker import FakeReranker, SiliconFlowReranker, build_reranker
from app.rag.retriever import Record, Retriever
from app.rag.service import AskService

BASE_URL = "https://api.siliconflow.cn/v1/rerank"
MODEL = "BAAI/bge-reranker-v2-m3"


def _rec(text: str, idx: int) -> Record:
    return Record(
        table="racket_specs",
        id=f"racket_specs:{idx}",
        text=text,
        metadata={"品牌": "李宁 LINING", "型号": f"型号{idx}"},
        distance=None,
    )


RECORDS = [
    _rec("平衡点 310 的进攻型球拍", 0),
    _rec("平衡点 290 的均衡拍", 1),
    _rec("入门拍，适合初学者", 2),
]


# ---------- FakeReranker ----------

def test_fake_reranker_returns_as_is():
    out = FakeReranker().rerank("平衡点超过300的进攻拍", RECORDS, top_n=2)
    assert [r.id for r in out] == ["racket_specs:0", "racket_specs:1"]


def test_fake_reranker_top_n_limited():
    out = FakeReranker().rerank("问题", RECORDS, top_n=1)
    assert len(out) == 1


# ---------- SiliconFlowReranker（MockTransport 不触网） ----------

def test_siliconflow_payload_and_parsing():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        # 返回与输入顺序不同的相关度：第 1 条最高
        return httpx.Response(
            200,
            json={
                "id": "rerank-x",
                "results": [
                    {"index": 2, "relevance_score": 0.2},
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.5},
                ],
            },
        )

    rk = SiliconFlowReranker("sk-test", transport=httpx.MockTransport(handler))
    out = rk.rerank("平衡点超过300的进攻拍", RECORDS, top_n=2)

    assert captured["url"] == BASE_URL
    assert captured["authorization"] == "Bearer sk-test"
    assert captured["payload"]["model"] == MODEL
    assert captured["payload"]["query"] == "平衡点超过300的进攻拍"
    assert captured["payload"]["documents"] == [r.text for r in RECORDS]
    # relevance_score 降序取前 2：index 0(0.9)、index 1(0.5)
    assert [r.id for r in out] == ["racket_specs:0", "racket_specs:1"]


def test_siliconflow_missing_results_keeps_order():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "x", "results": []})

    rk = SiliconFlowReranker("sk-test", transport=httpx.MockTransport(handler))
    out = rk.rerank("问题", RECORDS, top_n=2)
    assert [r.id for r in out] == ["racket_specs:0", "racket_specs:1"]


def test_siliconflow_requires_api_key():
    with pytest.raises(ValueError):
        SiliconFlowReranker("")


def test_siliconflow_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    rk = SiliconFlowReranker("sk-test", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        rk.rerank("问题", RECORDS, top_n=2)


# ---------- build_reranker 开关语义 ----------

def test_build_reranker_respects_switch_and_key():
    assert build_reranker(Settings(ask_use_rerank=False, rerank_api_key="sk-x")) is None
    assert build_reranker(Settings(ask_use_rerank=True, rerank_api_key=None)) is None
    assert isinstance(
        build_reranker(Settings(ask_use_rerank=True, rerank_api_key="sk-x")),
        SiliconFlowReranker,
    )


# ---------- AskService 接入 ----------

DOC1 = "尤尼克斯 YONEX 天斧99，重量4U，进攻型，适合专业级。"
META1 = {"品牌": "尤尼克斯 YONEX", "型号": "天斧99", "拍身重量(U)": "4U", "来源": "球拍.csv"}
DOC2 = "李宁 LINING 雷霆90，重量5U，均衡型，适合业余级。"
META2 = {"品牌": "李宁 LINING", "型号": "雷霆90", "拍身重量(U)": "5U", "来源": "球拍.csv"}


class StubLLM(LLMClient):
    def __init__(self, filters: dict | None = None, answer: str | dict = "推荐这款球拍") -> None:
        self._filters = filters or {}
        self._answer = answer

    def extract_filters(self, question: str) -> dict:
        return self._filters

    def generate_answer(self, question: str, contexts: list[dict]) -> dict:
        if isinstance(self._answer, dict):
            return self._answer
        return {"answer": self._answer, "used": []}


def _build_service(rows: list[tuple], stub: StubLLM, reranker=None) -> AskService:
    store = VectorStore()
    embedder = FakeEmbedder()
    for table, docs, metas in rows:
        ids = [f"{table}:{i}" for i in range(len(docs))]
        store.add(table, ids, docs, metas, embedder.embed(docs))
    return AskService(
        Retriever(store, embedder),
        stub,
        vector_top_k=10,
        filter_top_k=5,
        reranker=reranker,
    )


def test_ask_service_with_fake_reranker_behavior_unchanged():
    # 接 FakeReranker 后链路行为与不接一致：过滤生效、来源正确
    llm = StubLLM(filters={"拍身重量(U)": ["4U"]}, answer="推荐尤尼克斯天斧99")
    svc = _build_service([("racket_specs", [DOC1, DOC2], [META1, META2])], llm, FakeReranker())
    result = svc.ask("推荐一款4U球拍")
    assert result.answer == "推荐尤尼克斯天斧99"
    assert len(result.sources) == 1
    assert result.sources[0].brand == "尤尼克斯 YONEX"
    assert result.sources[0].model == "天斧99"


class ReverseReranker(FakeReranker):
    """倒序精排：验证精排结果确实被送入生成环节。"""

    def rerank(self, query: str, records: list[Record], top_n: int) -> list[Record]:
        return list(reversed(records))[:top_n]


def test_ask_service_reranker_reorders_generation_input():
    class CapturingLLM(StubLLM):
        def __init__(self) -> None:
            super().__init__(filters={}, answer={"answer": "ok", "used": []})
            self.contexts: list[dict] | None = None

        def generate_answer(self, question: str, contexts: list[dict]) -> dict:
            self.contexts = contexts
            return {"answer": "ok", "used": []}

    # 以实际检索顺序为基准：倒序精排后进入生成窗口的顺序应正好翻转
    store = VectorStore()
    embedder = FakeEmbedder()
    docs, metas = [DOC1, DOC2], [META1, META2]
    ids = [f"racket_specs:{i}" for i in range(len(docs))]
    store.add("racket_specs", ids, docs, metas, embedder.embed(docs))
    retriever = Retriever(store, embedder)
    retrieved = [r.to_dict() for r in retriever.retrieve("推荐球拍", top_k=10)]
    assert len(retrieved) == 2

    llm = CapturingLLM()
    svc = AskService(retriever, llm, vector_top_k=10, filter_top_k=5, reranker=ReverseReranker())
    svc.ask("推荐球拍")
    assert llm.contexts is not None
    assert [c["document"] for c in llm.contexts] == [
        d["document"] for d in reversed(retrieved)
    ]
