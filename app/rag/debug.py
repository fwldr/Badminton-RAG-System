"""RAG 调试沙箱：完整链路回放（路由 → 查询扩展 → 检索候选 → 过滤 → 上下文 → 回答）。

管理端「检索与问答调优中心」的在线测试沙箱用：展示与真实链路一致的各阶段产物，
不修改任何状态（检索/过滤均为只读；LLM 调用与生成节点同构，可选关闭）。
"""

from __future__ import annotations

import logging

from app.agent.graph import _DEFAULT_GENERATE_SYSTEM
from app.agent.router import classify
from app.agent.tools import ROUTE_COLLECTIONS, rag_search
from app.rag.llm import LLMClient
from app.rag.query_expander import expand
from app.rag.retriever import Retriever, resolve_source

logger = logging.getLogger(__name__)


def debug_pipeline(
    question: str,
    retriever: Retriever,
    llm: LLMClient,
    vision_embed=None,
    top_k: int = 8,
    with_answer: bool = True,
    generate_system: str | None = None,
) -> dict:
    """回放一次 RAG 链路的各阶段（返回结构化结果；with_answer=False 跳过 LLM 生成省 token）。

    - 路由：document / rules / technique / equipment / chitchat / multi（同 agent 分类）；
    - 查询扩展：同义词变体列表（expand 产物）；
    - 候选：定向/全表检索结果（表、得分 = 1-distance、预览、来源）；
    - 条件：equipment 路由的抽取过滤条件；
    - 上下文：与生成节点同构的 prompt 文本块；回答：LLM 生成（默认 system 或激活模板）。
    """
    # 1）路由分类（与 agent 路由节点一致：无历史）
    route = classify(question, llm, [])

    # 2）查询扩展
    queries = expand(question, retriever._synonyms)

    # 3）检索候选（document 路由含 img_*，需 vision_embed）
    if route == "document":
        recs = rag_search(question, retriever, "document", top_k=top_k, vision_embed=vision_embed)
    elif route in ROUTE_COLLECTIONS:
        recs = rag_search(question, retriever, route, top_k=top_k)
    else:
        recs = [r.to_dict() for r in retriever.retrieve(question, top_k=top_k)]
    candidates: list[dict] = []
    for r in recs:
        brand, model = resolve_source(r)
        text = str(r.get("document", ""))
        score = None
        if r.get("distance") is not None:
            score = round(1.0 - float(r["distance"]), 4)
        candidates.append({
            "table": r.get("table", ""),
            "id": r.get("id", ""),
            "score": score,
            "text": text,
            "preview": text[:120],
            "source": f"{brand} {model}".strip(),
            "metadata": r.get("metadata") or {},
        })

    # 4）过滤条件（equipment 路由抽取）
    conditions: dict = {}
    if route == "equipment":
        try:
            conditions = llm.extract_filters(question) or {}
        except Exception:
            conditions = {}

    # 5）生成上下文（与 agent _generate_node 同构）
    context_block = "\n".join(
        f"[{i}] {c['text']}（来源：{c['source']}）" for i, c in enumerate(candidates, 1)
    )
    answer = None
    if with_answer and candidates:
        system = generate_system or _DEFAULT_GENERATE_SYSTEM
        try:
            answer = llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"问题：{question}\n\n检索内容：\n{context_block}"},
                ],
                json_mode=True,
            )
        except Exception:
            logger.exception("沙箱生成回答失败（链路其余阶段不受影响）")

    return {
        "question": question,
        "route": route,
        "expanded_queries": queries,
        "candidates": candidates,
        "conditions": conditions,
        "context_block": context_block,
        "answer": answer,
    }


def wiki_debug_pipeline(
    navigator,
    question: str,
    llm: LLMClient | None = None,
    route: str = "",
    with_answer: bool = True,
    generate_system: str | None = None,
) -> dict:
    """Wiki 链路回放：orient 选了哪几类 → 展开了哪些条目章节 → step 又补了哪几页 → 回答。

    字段与 classic 沙箱同构（candidates/context_block/answer），额外给 `wiki_trace`
    （演示「翻 wiki」过程的关键抓手，也是排障时判断降级原因的地方）。
    """
    from app.wiki.navigator import context_line

    contexts, trace = navigator.navigate(question, route)
    step_docs = {doc_id for step in trace.steps for doc_id in step.get("expand", [])}
    candidates = [
        {
            "table": ctx.get("table", ""),
            "id": ctx.get("id", ""),
            "entry_title": (ctx.get("metadata") or {}).get("entry_title", ""),
            "section_title": (ctx.get("metadata") or {}).get("section_title", ""),
            "origin": "step" if ctx.get("id") in step_docs else "orient",
            "records": (ctx.get("metadata") or {}).get("records", []),
            "preview": str(ctx.get("document", ""))[:120],
            "text": str(ctx.get("document", "")),
        }
        for ctx in contexts
    ]
    context_block = "\n".join(context_line(ctx, i) for i, ctx in enumerate(contexts, 1))
    answer = None
    if with_answer and contexts and llm is not None:
        system = generate_system or _DEFAULT_GENERATE_SYSTEM
        try:
            answer = llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"问题：{question}\n\n检索内容：\n{context_block}"},
                ],
                json_mode=True,
            )
        except Exception:
            logger.exception("wiki 沙箱生成回答失败（链路其余阶段不受影响）")

    return {
        "question": question,
        "mode": "wiki",
        "route": route,
        "wiki_trace": trace.to_dict(),
        "expanded_queries": [],
        "candidates": candidates,
        "conditions": {},
        "context_block": context_block,
        "answer": answer,
    }
