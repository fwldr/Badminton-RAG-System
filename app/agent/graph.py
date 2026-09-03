"""LangGraph 状态图：路由 → 工具 → 生成 → 校验 →（重检索一次）→ 结束。

节点：
- route：问题分类（equipment / rules / technique / chitchat / multi）
- chitchat：闲聊短路（不检索）
- equipment：装备结构化查询（检索 + 抽过滤条件 + 属性过滤）
- rules / technique：定向 RAG 检索（限定 collection 子集）
- multi：问题拆解 → 逐子问题检索 → 合并
- generate：生成回答（结构化 {answer, used}）
- verify：回答校验（不支撑且未重试 → retry；否则结束）
- retry：重检索一次（扩大候选、去掉过滤），retry_count +1

所有节点经 _traced 包装，自动把 {"node","input","output"} 追加到 state["trace"]。
"""

from __future__ import annotations

import logging
import re
from functools import wraps

from langgraph.graph import END, START, StateGraph

from app.agent.state import AgentState, _summarize, trace_entry
from app.agent.tools import (
    ROUTE_COLLECTIONS,
    chitchat,
    decompose,
    equipment_query,
    rag_search,
)
from app.agent.verifier import verify
from app.rag.llm import FALLBACK_ANSWER, LLMClient, parse_answer_result
from app.rag.retriever import Retriever, resolve_source
from app.wiki.navigator import WikiNavigator, context_line

logger = logging.getLogger(__name__)

_MAX_RETRY = 1  # 校验不支撑时最多重检索一次（防死循环）

# 规则 3 独立成常量：兜底时整段替换为「部分作答」规则（部分作答重试用）
_RULE3_DEFAULT = (
    "3. 若检索内容确实不足以回答用户问题，则 answer 为 知识库中暂无相关信息，used 为空数组；"
    "注意：检索内容已按问题精调并通常足够，除与问题完全无关或明显缺失关键信息外，"
    "必须基于检索内容作答，不得凭空回避；"
)
_RULE3_PARTIAL = (
    "3. 请基于上面的检索内容直接作答：给出你能确定的部分回答（合并已知信息即可，"
    "可以只覆盖问题的某个方面）；仅当完全没有任何可用信息时可用 知识库中暂无相关信息，"
    "否则禁止输出该兜底文案；"
)

# 生成节点默认 system 提示（管理端 RAG 调优中心的模板激活时覆盖，见 _generate_system 参数）
_DEFAULT_GENERATE_SYSTEM = (
    "你是羽毛球装备/知识问答助手。请只依据下面给出的检索内容回答问题，禁止编造任何信息。\n"
    "要求：\n"
    "1. 只输出 JSON，不要输出任何其他文字；\n"
    '2. 格式为 {"answer": "回答正文", "used": [被引用的条目编号，对应检索内容里的 [1][2]…]}；\n'
    + _RULE3_DEFAULT
    + "\n"
    "4. 回答正文只写回答内容本身，不要输出「来源：…」等引用标注（引用来源由系统单独展示，answer 中不要重复）；\n"
    "5. 若有历史对话，可结合历史理解当前问题，但结论仍须由本次检索内容支撑；\n"
    "6. 特例：若用户问题引用了之前对话（含 刚才/之前/前面 等），可依据「历史对话」中的回答直接作答，"
    "used 可为空数组，来源标注为「历史对话」。\n"
    "7. 若检索内容中某条带有「图片链接：url」，请在回答合适位置把该图片以 markdown 图片语法原样输出："
    "![图片说明](url)（url 照抄不要改写）。"
)


def _clean_answer(answer: str) -> str:
    """清洗 LLM 生成的回答文本。

    - 还原 Double-escape 的 \\n / \\t / \\" / \\\\（模型常把换行写成字面 \\n）；
    - 剥离末尾「来源：…」/「（来源：…）」标注（引用来源改由结构化 sources 展示）。
    """
    s = answer.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")
    # 末尾独立成行的「来源：…」标注整行去掉（可能多来源用；分隔）
    lines = s.split("\n")
    while lines and lines[-1].strip().startswith(("来源", "参考来源", "（来源")):
        lines.pop()
    s = "\n".join(lines)
    # 内联在末尾的「来源：…」/「（来源：…）」去掉；保留正文标点，不触碰正文中部的「来源文件」等
    s = re.sub(r"[（(]?(?:参考来源|来源)[:：][^\n]*[)）]?$", "", s)
    return s.strip()

# 结构化对比行的字段（规格表 metadata 中可精确展示的属性）
_STRUCTURED_FIELDS: tuple[tuple[str, str], ...] = (
    ("品牌", "品牌"),
    ("型号", "型号"),
    ("拍身重量(U)", "重量"),
    ("最高磅数", "最高磅数"),
    ("平衡点类别", "平衡点类别"),
    ("打法类型", "打法类型"),
    ("适合水平", "适合水平"),
    ("参考价", "参考价"),
    ("球速", "球速"),
    ("羽毛类别", "羽毛类别"),
    ("毛片等级", "毛片等级"),
    ("球头类别", "球头类别"),
    ("直径mm", "线径mm"),
    ("材质", "材质"),
)


def _structured_row(meta: dict) -> str:
    """把规格表 metadata 拼成结构化对比行（只拼非空字段，供 LLM 精确对比）。"""
    parts = []
    for key, label in _STRUCTURED_FIELDS:
        val = str(meta.get(key, "")).strip() if meta.get(key) is not None else ""
        if val and val != "-":
            parts.append(f"{label}:{val}")
    return "，".join(parts)


def _format_contexts(contexts: list[dict]) -> list[str]:
    """把检索上下文渲染成 prompt 里的编号条目（wiki 与 classic 两种格式按上下文自分派）。"""
    lines: list[str] = []
    for i, rec in enumerate(contexts, 1):
        meta = rec.get("metadata") or {}
        if meta.get("entry_title"):
            lines.append(context_line(rec, i))
            continue
        brand, model = resolve_source(rec)
        img_url = str(meta.get("图片URL", "")).strip()
        img_suffix = f"（图片链接：{img_url}）" if img_url else ""
        # 规格表（含品牌与拍身重量(U) metadata）→ 拼结构化对比行，供精确对比
        if meta.get("拍身重量(U)") or meta.get("品牌"):
            structured = _structured_row(meta)
            if structured:
                lines.append(f"[{i}] {structured}（来源：{brand} {model}）{img_suffix}")
                continue
        lines.append(f"[{i}] {rec.get('document', '')}（来源：{brand} {model}）{img_suffix}")
    return lines


class BadmintonAgent:
    """Agentic RAG 编排器：封装 LangGraph 图。"""

    def __init__(
        self,
        retriever: Retriever,
        llm: LLMClient,
        vector_top_k: int = 10,
        filter_top_k: int = 5,
        memory=None,
        use_verifier: bool = True,
        tracer=None,
        vision_embed=None,
        generate_system: str | None = None,
        wiki: WikiNavigator | None = None,
        default_mode: str = "classic",
    ) -> None:
        """tracer：可观测性 tracer（None 时不追踪；见 app/observability/tracer.py）。

        vision_embed：多模态图片 embedding（SiliconFlow API），document 路由检索
        img_* collection 时用其 embed_text（与文本 embedding 不同空间）；None 时忽略 img_*。
        generate_system：生成节点 system 提示覆盖（管理端 Prompt 模板激活后注入；
        None 使用内置默认 _DEFAULT_GENERATE_SYSTEM）。
        wiki / default_mode：LLM Wiki 检索链路（plan `badminton-rag-llm-wiki-plan.md`）。
        `wiki=None`（未编译或已落后源表）时任何 mode=wiki 请求都按 classic 执行，问答不受影响。
        """
        self._retriever = retriever
        self._llm = llm
        self._vector_top_k = vector_top_k
        self._filter_top_k = filter_top_k
        self._memory = memory  # MemoryStore | None
        self._use_verifier = use_verifier
        self._tracer = tracer
        self._vision_embed = vision_embed
        self._generate_system = generate_system
        self._wiki = wiki
        self._default_mode = default_mode
        if tracer is not None:
            # 把 LLM token 用量归因到当前 span（旁路，不影响主链路）
            tracer.attach_llm(self._llm)
        self._graph = self._build_graph()

    # ---------- trace 包装 ----------

    def _traced(self, name: str):
        """节点包装器：执行后把 {"node","input","output"} 追加到 trace；有 tracer 时同时记录 span。"""

        def deco(fn):
            @wraps(fn)
            def wrapper(state: AgentState) -> dict:
                tracer = self._tracer
                span = (
                    tracer.span(name, input={"question": state.get("question")})
                    if tracer is not None
                    else None
                )
                try:
                    result = fn(state)
                except Exception:
                    # 节点异常也必须收尾 span（否则 Langfuse v4 的 OTel current span 泄漏，
                    # 后续节点会错误挂到它下面）
                    if span is not None:
                        span.end(output={"error": "node_failed"})
                    raise
                trace = list(state.get("trace") or [])
                trace.append(trace_entry(name, {"question": state.get("question")}, result))
                merged = dict(result)
                merged["trace"] = trace
                if span is not None:
                    span.end(output=_summarize(result))
                return merged

            return wrapper

        return deco

    # ---------- 节点实现 ----------

    @staticmethod
    def _enhanced_question(state: AgentState) -> str:
        """带历史的增强查询：历史摘要 + 当前问题（供指代消解）。"""
        question = state.get("question", "")
        history = state.get("history") or []
        if not history:
            return question
        hist_text = "；".join(
            f"{m.get('content', '')}" for m in history[-3:] if m.get("content")
        )
        if not hist_text:
            return question
        return f"对话背景：{hist_text}。当前问题：{question}"

    def _resolve_mode(self, state: AgentState) -> str:
        """归一化检索模式：请求级 `mode` 优先，其次全局 default_mode；导航器不可用一律回落 classic。"""
        requested = (state.get("mode") or self._default_mode or "classic").strip().lower()
        if requested == "wiki" and self._wiki is None:
            return "classic"
        return "wiki" if requested == "wiki" else "classic"

    def _route_node(self, state: AgentState) -> dict:
        from app.agent.router import classify

        # 范围限定（用户主动选择的检索范围）→ 强制路由，跳过自动分类
        mode = self._resolve_mode(state)
        scope = state.get("scope")
        if scope in ROUTE_COLLECTIONS:
            return {"route": scope, "mode": mode}
        route = classify(state.get("question", ""), self._llm, state.get("history") or [])
        return {"route": route, "mode": mode}

    def _chitchat_node(self, state: AgentState) -> dict:
        answer = _clean_answer(chitchat(state.get("question", ""), self._llm, state.get("history") or []))
        return {"answer": answer, "sources": [], "verified": True}

    def _equipment_node(self, state: AgentState) -> dict:
        contexts, conditions = equipment_query(
            self._enhanced_question(state), self._retriever, self._llm,
            top_k=self._vector_top_k, filter_top_k=self._filter_top_k,
        )
        return {"contexts": contexts, "conditions": conditions}

    def _rules_node(self, state: AgentState) -> dict:
        contexts = rag_search(self._enhanced_question(state), self._retriever, "rules", top_k=self._filter_top_k)
        return {"contexts": contexts}

    def _technique_node(self, state: AgentState) -> dict:
        contexts = rag_search(self._enhanced_question(state), self._retriever, "technique", top_k=self._filter_top_k)
        return {"contexts": contexts}

    def _document_node(self, state: AgentState) -> dict:
        """文档类问题：定向检索全部文档 collection（doc_*/pdf_* 文本 + img_* 多模态图片）。"""
        contexts = rag_search(
            self._enhanced_question(state), self._retriever, "document",
            top_k=self._filter_top_k, vision_embed=self._vision_embed,
        )
        return {"contexts": contexts}

    def _multi_node(self, state: AgentState) -> dict:
        from app.agent.router import classify

        question = state.get("question", "")
        sub_questions = decompose(question, self._llm)
        all_contexts: list[dict] = []
        all_conditions: dict = {}
        for sub in sub_questions:
            sub_route = classify(sub, self._llm, state.get("history") or [])
            if sub_route == "equipment":
                ctx, cond = equipment_query(
                    sub, self._retriever, self._llm,
                    top_k=self._vector_top_k, filter_top_k=self._filter_top_k,
                    collections=ROUTE_COLLECTIONS["equipment"],
                )
                all_contexts.extend(ctx)
                all_conditions.update(cond)
            elif sub_route in ROUTE_COLLECTIONS:
                all_contexts.extend(rag_search(sub, self._retriever, sub_route, top_k=self._filter_top_k))
            elif sub_route == "document":
                all_contexts.extend(rag_search(
                    sub, self._retriever, "document",
                    top_k=self._filter_top_k, vision_embed=self._vision_embed,
                ))
            else:
                all_contexts.extend(rag_search(sub, self._retriever, "equipment", top_k=self._filter_top_k))
        # 去重（按 id）
        seen: set[str] = set()
        deduped: list[dict] = []
        for c in all_contexts:
            rid = c.get("id", "")
            if rid in seen:
                continue
            seen.add(rid)
            deduped.append(c)
        return {
            "contexts": deduped[: self._filter_top_k * 2],
            "sub_questions": sub_questions,
            "conditions": all_conditions,
        }

    def _classic_for_route(self, state: AgentState) -> dict:
        """按 route 复用既有 classic 检索节点（wiki 无结果时的降级路径，行为与开关关闭时一致）。"""
        nodes = {
            "equipment": self._equipment_node,
            "rules": self._rules_node,
            "technique": self._technique_node,
            "document": self._document_node,
            "multi": self._multi_node,
        }
        node = nodes.get(state.get("route", "equipment"), self._equipment_node)
        return node(state)

    def _wiki_node(self, state: AgentState) -> dict:
        """Wiki 检索：orient（LLM 两级漏斗 + hybrid 反查）→ read → step 补展开（至多 `wiki_max_steps` 轮）。

        展开不到任何章节时降级为 classic 检索并在 `wiki_trace.degraded` 记原因——
        wiki 是精度增强，任何一环失效都不能让问答拿不到上下文。
        """
        contexts, trace = self._wiki.navigate(
            self._enhanced_question(state), state.get("route", "")
        )
        if not contexts:
            degraded = trace.degraded or "no-context"
            result = self._classic_for_route(state)
            result["wiki_trace"] = {**trace.to_dict(), "degraded": degraded}
            return result
        return {
            "contexts": contexts,
            "conditions": {},
            "sub_questions": [],
            "wiki_trace": trace.to_dict(),
        }

    def _generate_node(self, state: AgentState) -> dict:
        question = state.get("question", "")
        contexts = state.get("contexts") or []
        sub_questions = state.get("sub_questions") or []
        conditions = state.get("conditions") or {}
        history = state.get("history") or []

        # 指代回忆类问题（询问之前对话内容）：优先依据历史回答，不强制检索支撑
        refer_signal = any(k in question for k in ("刚才", "之前", "前面", "刚才问", "上一条"))
        history_answers = [m.get("content", "") for m in history if m.get("role") == "assistant" and m.get("content")]
        use_history_answer = refer_signal and bool(history_answers)

        if not contexts and not use_history_answer:
            return {"answer": FALLBACK_ANSWER, "sources": [], "verified": True}
        context_block = "\n".join(_format_contexts(contexts))

        hist_block = "\n".join(f"{m.get('role')}: {m.get('content', '')}" for m in history[-4:])
        sub_block = "；".join(sub_questions) if sub_questions else ""
        cond_block = str(conditions) if conditions else ""

        # 管理端激活的 Prompt 模板（generate_system）优先，否则内置默认
        system = self._generate_system or _DEFAULT_GENERATE_SYSTEM
        user = (
            f"问题：{question}\n"
            f"{('拆解出的子问题：' + sub_block + '\n') if sub_block else ''}"
            f"{('历史对话：\n' + hist_block + '\n') if hist_block else ''}"
            f"{('过滤条件：' + cond_block + '\n') if cond_block else ''}"
            f"\n检索内容：\n{context_block}"
        )
        try:
            text = self._llm.complete(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                json_mode=True,
            )
            result = parse_answer_result(text)
            answer = _clean_answer(result.get("answer", ""))
            used = result.get("used", [])
        except Exception:
            logger.exception("生成回答失败")
            return {"answer": FALLBACK_ANSWER, "sources": [], "verified": True}

        if not answer or not answer.strip() or FALLBACK_ANSWER in answer:
            # 有上下文但 LLM 整答兜底：部分作答重试一次（规则 3 替换为「必须基于检索内容作答」），
            # 仍兜底才交给 verify 节点按 retry_count 决定重试
            if contexts and not use_history_answer:
                try:
                    forced = self._llm.complete(
                        [
                            {"role": "system", "content": system.replace(_RULE3_DEFAULT, _RULE3_PARTIAL)},
                            {"role": "user", "content": user},
                        ],
                        json_mode=True,
                    )
                    r2 = parse_answer_result(forced)
                    a2 = _clean_answer(r2.get("answer", ""))
                    if a2.strip() and FALLBACK_ANSWER not in a2:
                        answer, used = a2, r2.get("used", [])
                except Exception:
                    logger.warning("部分作答重试失败（旁路）", exc_info=True)
        if not answer or not answer.strip() or FALLBACK_ANSWER in answer:
            # 有上下文但 LLM 兜底：不标记 verified，交给 verify 节点按 retry_count 决定重试
            return {"answer": FALLBACK_ANSWER, "sources": []}

        # 指代回忆类且依据历史回答 → 直接跳过校验（来源标注历史对话）
        if use_history_answer and not used:
            return {
                "answer": answer,
                "sources": [{"table": "history", "brand": "历史对话", "model": ""}],
                "verified": True,
            }

        # 来源：优先 used 指向的条目，否则全量
        if used:
            cited = [
                contexts[i - 1]
                for i in used
                if isinstance(i, int) and not isinstance(i, bool) and 1 <= i <= len(contexts)
            ]
            source_recs = cited if cited else contexts
        else:
            source_recs = contexts

        sources: list[dict] = []
        seen_src: set[tuple[str, str, str]] = set()
        for rec in source_recs:
            brand, model = resolve_source(rec)
            key = (rec.get("table", ""), brand, model)
            if not (brand or model) or key in seen_src:
                continue
            seen_src.add(key)
            sources.append({"table": rec.get("table", ""), "brand": brand, "model": model})

        # 图片条目（带「图片URL」的引用）→ 响应 {url, title}，供前端内联/图集展示
        images: list[dict] = []
        seen_url: set[str] = set()
        for rec in source_recs:
            url = str((rec.get("metadata") or {}).get("图片URL", "")).strip()
            if not url or url in seen_url:
                continue
            seen_url.add(url)
            title = str((rec.get("metadata") or {}).get("文件名", "")).strip()
            images.append({"url": url, "title": title or url.rsplit("/", 1)[-1]})

        return {"answer": answer, "sources": sources, "images": images}

    def _verify_node(self, state: AgentState) -> dict:
        question = state.get("question", "")
        answer = state.get("answer", "")
        contexts = state.get("contexts") or []
        retry_count = state.get("retry_count", 0)

        # 生成环节未兜底才需要校验；兜底回答走与"不支撑"相同的重试逻辑
        if not self._use_verifier or not answer:
            return {"verified": True}
        is_fallback = FALLBACK_ANSWER in answer
        if not is_fallback:
            supported = verify(question, answer, contexts, self._llm)
            if supported:
                return {"verified": True}
        # 兜底或不支撑：未重试过 → 标记回检索；已重试过 → 降级
        if retry_count < _MAX_RETRY:
            return {"verified": False}
        return {"verified": False, "answer": FALLBACK_ANSWER, "sources": []}

    def _retry_node(self, state: AgentState) -> dict:
        """校验不支撑后的重检索：扩大候选、去掉过滤条件。retry_count +1。

        multi 路由的特殊处理：拆解可能引入噪声导致首轮兜底，重试时不再拆解，
        直接用原问题做全表检索（候选更广，降低 LLM 自判不足的概率）。
        """
        question = state.get("question", "")
        route = state.get("route", "equipment")
        retry_count = state.get("retry_count", 0) + 1
        if state.get("mode") == "wiki" and self._wiki is not None:
            # wiki 链路的重试语义：放开分类重新 orient + 至少补展开一轮（而非全表堆 top-k）
            contexts, trace = self._wiki.navigate(
                question, route="", max_steps=max(1, self._wiki.max_steps)
            )
            if contexts:
                return {
                    "contexts": contexts,
                    "conditions": {},
                    "retry_count": retry_count,
                    "wiki_trace": trace.to_dict(),
                }
        if route in ("equipment", "multi"):
            # 全表检索 + 不过滤
            records = self._retriever.retrieve(question, top_k=self._vector_top_k)
            return {"contexts": [r.to_dict() for r in records], "conditions": {}, "retry_count": retry_count}
        contexts = rag_search(
            question, self._retriever, route,
            top_k=self._filter_top_k + 3, per_table_k=6,
            vision_embed=self._vision_embed,
        )
        return {"contexts": contexts, "retry_count": retry_count}

    # ---------- 图组装 ----------

    def _build_graph(self):
        g = StateGraph(AgentState)

        g.add_node("route", self._traced("route")(self._route_node))
        g.add_node("chitchat", self._traced("chitchat")(self._chitchat_node))
        g.add_node("equipment", self._traced("equipment")(self._equipment_node))
        g.add_node("rules", self._traced("rules")(self._rules_node))
        g.add_node("technique", self._traced("technique")(self._technique_node))
        g.add_node("document", self._traced("document")(self._document_node))
        g.add_node("multi", self._traced("multi")(self._multi_node))
        g.add_node("wiki", self._traced("wiki")(self._wiki_node))
        g.add_node("generate", self._traced("generate")(self._generate_node))
        g.add_node("verify", self._traced("verify")(self._verify_node))
        g.add_node("retry", self._traced("retry")(self._retry_node))

        g.add_edge(START, "route")

        def _dispatch(state: AgentState) -> str:
            """mode=wiki 的非闲聊路由统一走 wiki 检索；classic 路径与开关关闭时完全一致。"""
            route = state.get("route", "equipment")
            if state.get("mode") == "wiki" and route != "chitchat":
                return "wiki"
            return route

        g.add_conditional_edges(
            "route",
            _dispatch,
            {
                "chitchat": "chitchat",
                "equipment": "equipment",
                "rules": "rules",
                "technique": "technique",
                "document": "document",
                "multi": "multi",
                "wiki": "wiki",
            },
        )

        for node in ("equipment", "rules", "technique", "document", "multi", "wiki"):
            g.add_edge(node, "generate")
        g.add_edge("generate", "verify")

        def _verify_choice(state: AgentState) -> str:
            if state.get("verified"):
                return "done"
            if state.get("retry_count", 0) < _MAX_RETRY:
                return "retry"
            return "done"  # 已重试过一次仍不支撑 → 结束（降级）

        g.add_conditional_edges(
            "verify",
            _verify_choice,
            {"retry": "retry", "done": END},
        )
        g.add_edge("retry", "generate")
        g.add_edge("chitchat", END)

        return g.compile()

    # ---------- 对外接口 ----------

    def invoke(self, state: AgentState) -> AgentState:
        """执行一次对话，返回最终状态（含 trace）。"""
        return self._graph.invoke(state)
