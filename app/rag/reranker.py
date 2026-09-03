"""精排（Rerank）：在属性过滤后、生成前对候选记录重新排序，取最相关的 top-N。

向量 + BM25 混合检索给出的是「宽」候选池，属性过滤后顺序仍按检索得分排列，
与问题的语义贴近度未必最优。BGE-Reranker 对「query 与单条文档」做交叉编码，
比向量余弦相似度更能反映相关性——用于把最相关的记录顶到生成窗口前列。

实现：
- Reranker（Protocol）：rerank(query, records, top_n) → list[Record]；
- SiliconFlowReranker：httpx POST 硅基流动 /v1/rerank（bge-reranker-v2-m3），
  payload 形如 {"model", "query", "documents"}，解析 results 按 relevance_score 降序；
- FakeReranker：原样返回前 top_n 条（不改变排序），测试用；
- build_reranker(settings)：按配置构建；开关未开或缺 api_key 时返回 None（跳过精排）。
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from app.core.config import Settings
from app.rag.retriever import Record

logger = logging.getLogger(__name__)

DEFAULT_RERANK_BASE_URL = "https://api.siliconflow.cn/v1/rerank"
DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"


class Reranker(Protocol):
    """把检索记录按与 query 的相关度重新排序。"""

    def rerank(self, query: str, records: list[Record], top_n: int) -> list[Record]:
        """返回按相关度降序的前 top_n 条记录。"""
        ...


class SiliconFlowReranker:
    """硅基流动 BGE-Reranker：httpx POST /v1/rerank。

    transport 供测试注入 MockTransport（与 DashScopeEmbedder 同模式）。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_RERANK_BASE_URL,
        model: str = DEFAULT_RERANK_MODEL,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("SiliconFlowReranker 需要 api_key")
        self.base_url = base_url
        self.model = model
        self._client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    def rerank(self, query: str, records: list[Record], top_n: int) -> list[Record]:
        if not records:
            return []
        resp = self._client.post(
            self.base_url,
            json={
                "model": self.model,
                "query": query,
                "documents": [r.text for r in records],
            },
        )
        resp.raise_for_status()
        results = (resp.json() or {}).get("results") or []
        # 兼容两种返回：带 relevance_score 的按分降序；否则按 API 返回顺序（即已按相关度排序）
        items: list[tuple[int, float]] = []
        has_score = any(
            isinstance(r, dict) and r.get("relevance_score") is not None for r in results
        )
        for item in results:
            if not (isinstance(item, dict) and isinstance(item.get("index"), int)):
                continue
            idx = item["index"]
            if not (0 <= idx < len(records)):
                continue
            score = float(item.get("relevance_score") or 0.0) if has_score else 0.0
            items.append((idx, score))
        if not items:
            return records[:top_n]
        if has_score:
            items.sort(key=lambda t: t[1], reverse=True)
        ordered = [records[idx] for idx, _ in items]
        return ordered[:top_n]


class FakeReranker:
    """测试用：原样返回前 top_n 条（不改变检索排序，验证未接入时链路不变）。"""

    def rerank(self, query: str, records: list[Record], top_n: int) -> list[Record]:
        return records[:top_n]


def build_reranker(settings: Settings) -> Reranker | None:
    """按配置构建精排器；ask_use_rerank 未开或缺 rerank_api_key 时返回 None（跳过精排）。"""
    if not settings.ask_use_rerank:
        return None
    if not settings.rerank_api_key:
        logger.warning("ask_use_rerank=True 但未配置 rerank_api_key，跳过精排")
        return None
    return SiliconFlowReranker(
        api_key=settings.rerank_api_key,
        base_url=settings.rerank_base_url,
        model=settings.rerank_model,
    )
