"""Agent 工具层：equipment 结构化查询 / 定向 RAG 检索 / 问题拆解 / 闲聊。

复用现有链路（Retriever / extract_filters / apply_filters / reranker），不重复实现。
"""

from __future__ import annotations

import logging

from app.rag.filters import apply_filters
from app.rag.llm import FALLBACK_ANSWER, LLMClient, parse_filter_json
from app.rag.retriever import (
    Record,
    Retriever,
    document_image_collections,
    document_text_collections,
    resolve_source,
)

logger = logging.getLogger(__name__)

# 各路由的定向 collection 子集（比全表检索更精准）
ROUTE_COLLECTIONS: dict[str, tuple[str, ...]] = {
    "rules": ("bwf_rules", "common_penalties"),
    "technique": ("hand_techniques", "footwork_techniques", "tactics"),
    "equipment": (
        "racket_specs",
        "shuttlecock_specs",
        "string_specs",
        "grip_specs",
        "shoe_specs",
    ),
}


def _to_source_dict(rec: dict) -> dict:
    """从检索记录提取来源 dict {table, brand, model}。"""
    brand, model = resolve_source(rec)
    return {"table": rec.get("table", ""), "brand": brand, "model": model}


def equipment_query(
    question: str,
    retriever: Retriever,
    llm: LLMClient,
    top_k: int = 10,
    filter_top_k: int = 5,
    collections: tuple[str, ...] | None = None,
) -> tuple[list[dict], dict]:
    """装备参数结构化查询：检索 → 抽过滤条件 → 属性过滤 → 取 top-N。

    返回 (contexts, conditions)。与 AskService 的链路一致，但允许调用方拿到过滤条件。
    collections 非空时只在指定规格表内检索（multi 拆解子问题用，避免跨表噪声）。
    """
    if collections:
        records = rag_search(question, retriever, "equipment", top_k=top_k, per_table_k=4)
        records = [r for r in records if r.get("table") in collections]
        contexts = records
    else:
        records = retriever.retrieve(question, top_k=top_k)
        contexts = [r.to_dict() for r in records]
    conditions = llm.extract_filters(question)
    filtered = apply_filters(contexts, conditions)
    top = (filtered if filtered else contexts)[:filter_top_k]
    return top, conditions


def rag_search(
    question: str,
    retriever: Retriever,
    route: str,
    top_k: int = 5,
    per_table_k: int = 4,
    vision_embed=None,
) -> list[dict]:
    """定向 RAG 检索：只查该路由对应的 collection 子集。

    document 路由动态展开全部文档 collection：doc_*/pdf_* 文本用文本 embedding 查询向量；
    img_* 多模态图片（SiliconFlow API 空间，与文本向量不同空间）用 vision_embed.embed_text
    的向量分别检索。返回 dict 列表（含 table/document/metadata/distance）。
    """
    if route == "document":
        text_colls = document_text_collections(retriever._store)
        img_colls = document_image_collections(retriever._store)
        if not text_colls and not img_colls:
            return []

        def _query(table: str, query_vec: list[float]) -> list[Record]:
            hits = retriever._store.query(table, query_vec, n_results=per_table_k)
            return [
                Record(
                    table=table,
                    id=h["id"],
                    text=h["document"],
                    metadata=h["metadata"],
                    distance=h["distance"],
                )
                for h in hits
            ]

        merged: list[Record] = []
        if text_colls:
            [query_vec] = retriever._embedder.embed([question])
            for table in text_colls:
                merged.extend(_query(table, query_vec))
        if img_colls and vision_embed is not None:
            img_vec = vision_embed.embed_text(question)
            for table in img_colls:
                merged.extend(_query(table, img_vec))
        merged.sort(key=lambda r: r.distance if r.distance is not None else float("inf"))
        # 多样性约束：同 collection 最多保留 per_table_k 条
        kept: list[Record] = []
        seen: dict[str, int] = {}
        for r in merged:
            if seen.get(r.table, 0) >= per_table_k:
                continue
            seen[r.table] = seen.get(r.table, 0) + 1
            kept.append(r)
        return [r.to_dict() for r in kept[:top_k]]

    collections = ROUTE_COLLECTIONS.get(route)
    if not collections:
        # 未知路由回退全表
        return [r.to_dict() for r in retriever.retrieve(question, top_k=top_k)]

    # 逐 collection 定向检索，合并排序（复用 Retriever 内部逻辑但限定子集）
    [query_vec] = retriever._embedder.embed([question])
    merged: list[Record] = []
    for table in collections:
        hits = retriever._store.query(table, query_vec, n_results=per_table_k)
        for h in hits:
            merged.append(
                Record(
                    table=table,
                    id=h["id"],
                    text=h["document"],
                    metadata=h["metadata"],
                    distance=h["distance"],
                )
            )
    merged.sort(key=lambda r: r.distance if r.distance is not None else float("inf"))
    return [r.to_dict() for r in merged[:top_k]]


def decompose(question: str, llm: LLMClient) -> list[str]:
    """复杂问题拆解：LLM 输出子问题列表；失败回退 [原问题]。"""
    system = (
        "你是问题拆解助手。把用户的复杂问题拆成 2~4 个可独立检索的子问题，"
        "只输出 JSON：{\"sub_questions\": [\"...\", \"...\"]}。不要输出其他文字。"
    )
    try:
        text = llm.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"问题：{question}"},
            ],
            json_mode=True,
        )
        data = parse_filter_json(text)
        subs = data.get("sub_questions")
        if isinstance(subs, list) and subs:
            cleaned = [str(s).strip() for s in subs if str(s).strip()]
            if cleaned:
                return cleaned
    except Exception:
        logger.exception("问题拆解失败，回退原问题")
    return [question]


def chitchat(question: str, llm: LLMClient, history: list[dict] | None = None) -> str:
    """闲聊回应：不检索，直接对话（可带历史上下文，回答涉及之前对话的指代）。"""
    hist_block = ""
    if history:
        lines = [f"{m.get('role')}: {m.get('content', '')}" for m in history[-4:]]
        hist_block = "\n历史对话：\n" + "\n".join(lines)
    system = (
        "你是羽毛球知识问答助手（Agentic RAG 系统）。用户只是闲聊，请简短友好地回应，"
        "并提示可以问我装备参数、比赛规则或技术教学类问题。"
        "若用户问的是之前对话里出现过的问题，请依据历史对话回答，不要否认有聊天记录。"
    )
    try:
        text = llm.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"{hist_block}\n用户：{question}"},
            ]
        )
        return text.strip() or FALLBACK_ANSWER
    except Exception:
        logger.exception("闲聊回应失败")
        return "你好！我可以回答羽毛球装备参数、比赛规则和技术教学类问题，试试问我吧。"
