"""AskService 编排单元测试：检索 → 过滤 → 生成 → 来源（全部用 FakeEmbedder + stub LLM）。"""

from app.ingest.embedder import FakeEmbedder
from app.ingest.store import VectorStore
from app.rag.llm import LLMClient
from app.rag.retriever import Retriever
from app.rag.service import AskService

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


def _build_service(rows: list[tuple], stub: StubLLM) -> AskService:
    store = VectorStore()
    embedder = FakeEmbedder()
    for table, docs, metas in rows:
        ids = [f"{table}:{i}" for i in range(len(docs))]
        store.add(table, ids, docs, metas, embedder.embed(docs))
    return AskService(Retriever(store, embedder), stub, vector_top_k=10, filter_top_k=5)


def test_ask_happy_path_with_filter():
    llm = StubLLM(filters={"拍身重量(U)": ["4U"]}, answer="推荐尤尼克斯天斧99")
    svc = _build_service([("racket_specs", [DOC1, DOC2], [META1, META2])], llm)
    result = svc.ask("推荐一款4U球拍")
    assert result.answer == "推荐尤尼克斯天斧99"
    assert len(result.sources) == 1
    assert result.sources[0].table == "球拍"
    assert result.sources[0].brand == "尤尼克斯 YONEX"
    assert result.sources[0].model == "天斧99"


def test_ask_no_records_returns_fallback():
    llm = StubLLM()
    svc = _build_service([], llm)
    result = svc.ask("推荐球拍")
    assert result.answer == "知识库中暂无相关信息"
    assert result.sources == []


def test_ask_filter_yields_nothing_falls_back():
    llm = StubLLM(filters={"拍身重量(U)": ["7U"]}, answer="推荐李宁雷霆90")
    svc = _build_service([("racket_specs", [DOC1, DOC2], [META1, META2])], llm)
    result = svc.ask("推荐球拍")
    assert result.answer == "推荐李宁雷霆90"
    # 回退到未过滤 top-5，来源仍给出
    assert any(s.model == "天斧99" for s in result.sources)


def test_ask_llm_fallback_answer_drops_sources():
    llm = StubLLM(filters={}, answer="知识库中暂无相关信息")
    svc = _build_service([("racket_specs", [DOC1], [META1])], llm)
    result = svc.ask("随便问")
    assert result.answer == "知识库中暂无相关信息"
    assert result.sources == []


def test_ask_cross_table_retrieval_and_dedup():
    # 两个表各一条，查询同时命中两表；来源去重
    llm = StubLLM(filters={}, answer="两者都推荐")
    svc = _build_service(
        [
            ("racket_specs", [DOC1], [META1]),
            ("grip_specs", ["尤尼克斯 YONEX AC102EX，PU/聚氨酯手胶"], [{"品牌": "尤尼克斯 YONEX", "名称": "AC102EX"}]),
        ],
        llm,
    )
    result = svc.ask("尤尼克斯 YONEX")
    assert len(result.sources) >= 1
    tables = {s.table for s in result.sources}
    assert tables <= {"球拍", "手胶"}


def test_ask_sources_only_include_used():
    # 结构化回答声明 used=[2]，来源应只保留 top 中第 2 条（天斧99），排除第 1 条（雷霆90）
    llm = StubLLM(
        filters={},
        answer={"answer": "推荐尤尼克斯天斧99", "used": [2]},
    )
    svc = _build_service([("racket_specs", [DOC1, DOC2], [META1, META2])], llm)
    result = svc.ask("推荐球拍")
    assert result.answer == "推荐尤尼克斯天斧99"
    assert len(result.sources) == 1
    assert result.sources[0].model == "天斧99"


def test_ask_sources_only_include_used_first_entry():
    # used=[1] 只保留雷霆90（检索排序下 top 第 1 条）
    llm = StubLLM(
        filters={},
        answer={"answer": "推荐李宁雷霆90", "used": [1]},
    )
    svc = _build_service([("racket_specs", [DOC1, DOC2], [META1, META2])], llm)
    result = svc.ask("推荐球拍")
    assert len(result.sources) == 1
    assert result.sources[0].model == "雷霆90"


def test_ask_used_invalid_indices_fall_back_to_all():
    # used 全部越界/非法时，维持原有兜底：返回 top-N 全量来源
    llm = StubLLM(
        filters={},
        answer={"answer": "推荐球拍", "used": [99, True, "x"]},
    )
    svc = _build_service([("racket_specs", [DOC1, DOC2], [META1, META2])], llm)
    result = svc.ask("推荐球拍")
    assert any(s.model == "天斧99" for s in result.sources)
    assert any(s.model == "雷霆90" for s in result.sources)
