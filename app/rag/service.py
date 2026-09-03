"""RAG 问答编排：检索 → 过滤条件抽取 → 属性过滤 → 生成回答 → 附来源。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.models.schema import AskSource
from app.rag.filters import apply_filters
from app.rag.llm import FALLBACK_ANSWER, LLMClient
from app.rag.reranker import Reranker
from app.rag.retriever import Record, Retriever, resolve_source

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AskResult:
    """一次问答的结果。"""

    answer: str
    sources: list[AskSource]


class AskService:
    """POST /ask 的后端服务。"""

    def __init__(
        self,
        retriever: Retriever,
        llm: LLMClient,
        vector_top_k: int = 10,
        filter_top_k: int = 5,
        reranker: Reranker | None = None,
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._vector_top_k = vector_top_k
        self._filter_top_k = filter_top_k
        # 精排器：None 时跳过（默认关闭，不影响既有链路）
        self._reranker = reranker

    def ask(self, question: str) -> AskResult:
        # 1. 向量检索 top-k
        records = self._retriever.retrieve(question, top_k=self._vector_top_k)
        if not records:
            return AskResult(answer=FALLBACK_ANSWER, sources=[])

        # 后续环节统一使用 dict 形态（含 document/metadata/table）
        contexts = [r.to_dict() for r in records]

        # 2. LLM 抽取可选过滤条件 → 属性过滤
        conditions = self._llm.extract_filters(question)
        filtered = apply_filters(contexts, conditions)

        # 3. 过滤后取 top-N 拼进 prompt（过滤为空时回退到未过滤结果，交给 LLM 判断充分性）；
        #    接了精排器时，对候选集做 BGE-Reranker 精排后取 top-N（失败兜底原序，不破坏链路）
        candidates = filtered if filtered else contexts
        if self._reranker is not None and candidates:
            try:
                recs = [Record.from_dict(d) for d in candidates]
                top = [
                    r.to_dict()
                    for r in self._reranker.rerank(question, recs, top_n=self._filter_top_k)
                ]
            except Exception:
                logger.exception("精排失败，回退到过滤后 top-%d", self._filter_top_k)
                top = candidates[: self._filter_top_k]
        else:
            top = candidates[: self._filter_top_k]

        # 4. 生成回答：结构化 {answer, used}；空回答或 LLM 判定不确定 → 统一兜底文案，不附来源
        result = self._llm.generate_answer(question, top)
        answer = result.get("answer", "") if isinstance(result, dict) else str(result)
        used = result.get("used", []) if isinstance(result, dict) else []
        if not answer or not answer.strip() or FALLBACK_ANSWER in answer:
            return AskResult(answer=FALLBACK_ANSWER, sources=[])
        # 5. 来源与回答一致：只保留 used 指向的条目（编号从 1 开始，下标-1）；
        #    used 为空（未引用或解析失败回退）时维持原有逻辑，返回候选 top-N 的来源。
        if used:
            cited = [
                top[i - 1]
                for i in used
                if isinstance(i, int) and not isinstance(i, bool) and 1 <= i <= len(top)
            ]
            # 合法编号为空（全部越界/非法）时也回退到全量来源
            sources = self._to_sources(cited) if cited else self._to_sources(top)
        else:
            sources = self._to_sources(top)
        return AskResult(answer=answer, sources=sources)

    @staticmethod
    def _to_sources(records: list[dict]) -> list[AskSource]:
        """抽取来源：规格表为品牌+型号，知识表为表名+主题名；按 (表, 品牌, 型号) 去重。"""
        sources: list[AskSource] = []
        seen: set[tuple[str, str, str]] = set()
        for r in records:
            table = r.get("table", "")
            brand, model = resolve_source(r)
            key = (table, brand, model)
            if not (brand or model) or key in seen:
                continue
            seen.add(key)
            sources.append(AskSource(table=table, brand=brand, model=model))
        return sources
