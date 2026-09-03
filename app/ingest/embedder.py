"""Embedding 抽象：阿里云百炼（OpenAI 兼容 /embeddings）生成向量；FakeEmbedder 供测试。"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

# 百炼 embedding 单次请求文本条数有上限（超限报 InvalidParameter），内部自动分批；
# 取 10 兼容百炼各 embedding 模型（qwen3.7-text-embedding 上限 20，text-embedding-v4 上限 10）
_MAX_BATCH = 10


class Embedder(Protocol):
    """文本 → 向量列表的抽象。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """把一批文本转成等长向量列表。"""
        ...


class DashScopeEmbedder:
    """百炼 DashScope OpenAI 兼容 /embeddings。transport 供测试注入 MockTransport。"""

    def __init__(
        self,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key: str = "",
        model: str = "qwen3.7-text-embedding",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        # transport 供测试注入 MockTransport
        self._client = httpx.Client(timeout=60, transport=transport)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), _MAX_BATCH):
            vectors.extend(self._embed_batch(texts[i : i + _MAX_BATCH]))
        if len(vectors) != len(texts):
            raise ValueError(
                f"返回的向量数({len(vectors)})与输入文本数({len(texts)})不一致"
            )
        return vectors

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        resp = self._client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self.model, "input": batch, "encoding_format": "float"},
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if len(data) != len(batch):
            raise ValueError(
                f"embedding 返回向量数({len(data)})与输入文本数({len(batch)})不一致"
            )
        # 响应项顺序不保证与入参一致，按 index 还原
        return [item["embedding"] for item in sorted(data, key=lambda d: d.get("index", 0))]


def build_embedder(settings) -> DashScopeEmbedder:
    """按配置构建文本 embedding 引擎。入库与查询统一走这里，保证两边同模型同向量空间。"""
    return DashScopeEmbedder(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "",
        model=settings.embedding_model,
    )


class FakeEmbedder:
    """测试用确定性 embedding：按字符 hash 到固定维度并归一化。

    余弦相似度能反映文本字符重合度，便于单测中验证检索排序。
    """

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            for ch in text:
                digest = hashlib.md5(ch.encode("utf-8")).hexdigest()
                idx = int(digest[:8], 16) % self.dim
                vec[idx] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vectors.append([x / norm for x in vec])
        return vectors
