"""在线导航层（W2：orient + read；`step` 循环与动作空间在 W3 接入）。

orient = 两级漏斗（目录分类 → 条目行）+ **向量检索反查补齐**：
LLM 负责「选得准」（宁可少选），hybrid 命中 record 经 manifest 反查条目负责「别漏」，
两者合并后才展开章节正文——向量/BM25 在这里的角色是**条目定位器**，不是上下文供给者。

read = 按 `entry_id#section_key` 从 `wiki_section` 精确取回章节全文，
产出与 classic 链路同构的 context dict（额外带 `wiki` 元数据），因此生成/校验/来源统计全链路无需分叉。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.ingest.serializer import ALL_TABLES
from app.ingest.store import display_name
from app.models.spec import SpecTable
from app.rag.llm import LLMClient, parse_filter_json
from app.wiki.indexer import WIKI_PAGE_COLLECTION, WIKI_SECTION_COLLECTION, section_doc_id
from app.wiki.manifest import TOC_NAME, Manifest, source_fingerprint
from app.wiki.prompts import (
    MAX_CATEGORIES,
    ORIENT_CATEGORIES_SYSTEM,
    ORIENT_ENTRIES_SYSTEM,
    STEP_SYSTEM,
)
from app.wiki.schema import split_anchor

logger = logging.getLogger(__name__)

# 路由 → 目录一级分类（延续「定向比全表准」的既有实测结论）
ROUTE_CATEGORY_PREFIXES: dict[str, tuple[str, ...]] = {
    "equipment": ("装备规格",),
    "rules": ("规则判罚",),
    "technique": ("技术教学",),
    "document": ("文档",),
}

DEFAULT_MAX_ENTRIES = 5
DEFAULT_MAX_SECTIONS = 2
DEFAULT_MAX_CONTEXTS = 8
# W3 改造版导航：默认只补展开一步，每步最多 4 节（成本可控：每问 LLM 调用 orient2 + step1 + generate + verify）
DEFAULT_MAX_STEPS = 1
DEFAULT_MAX_EXPANSIONS = 4
HYBRID_TOP_K = 12
# 第二级漏斗交给 LLM 的条目行上限：超过则先用 wiki_page 向量粗排（大分类有 235 行）
TOC_ENTRY_CAP = 40
MAX_CONTEXT_CHARS = 900


@dataclass(frozen=True)
class Target:
    """一个待展开的知识单元：条目 + 章节键。"""

    entry_id: str
    sections: tuple[str, ...] = ()
    origin: str = "hybrid"  # llm | hybrid | step


@dataclass(frozen=True)
class StepDecision:
    """一步导航的结论：信息是否足够 + 需要补展开的知识单元。"""

    enough: bool = True
    expand: tuple[Target, ...] = ()


@dataclass
class OrientTrace:
    """orient 的决策轨迹（沙箱回放与 Langfuse span 直接展示用）。"""

    categories: list[str] = field(default_factory=list)
    llm_targets: list[str] = field(default_factory=list)
    hybrid_targets: list[str] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    llm_calls: int = 0
    degraded: str = ""  # 非空表示降级原因（无索引 / LLM 不可用 / 输出非法）

    def to_dict(self) -> dict:
        return {
            "categories": self.categories,
            "llm_targets": self.llm_targets,
            "hybrid_targets": self.hybrid_targets,
            "targets": [{"id": t.entry_id, "sections": list(t.sections), "origin": t.origin} for t in self.targets],
            "steps": self.steps,
            "llm_calls": self.llm_calls,
            "degraded": self.degraded,
        }


def _category_lines(toc: dict) -> list[tuple[str, int]]:
    """目录第一/二级展平：`装备规格/球拍` + 条目数。"""
    lines: list[tuple[str, int]] = []
    for category in toc.get("categories") or []:
        for table in category.get("tables") or []:
            lines.append((str(table.get("path", "")), int(table.get("count", 0))))
    return lines


def _entry_lines(groups: list[dict]) -> str:
    """条目行清单（给 LLM 的压缩视图）：`id | 标题（摘要）｜章节: a、b`。"""
    out: list[str] = []
    for group in groups:
        out.append(f"## {group['path']}（{group['count']}）")
        for line in group["entries"]:
            hint = f"（{line['hint']}）" if line.get("hint") else ""
            sections = "、".join(line.get("sections") or [])
            out.append(f"{line['id']} | {line['title']}{hint}｜章节: {sections}")
    return "\n".join(out)


class WikiNavigator:
    """Wiki 模式的上下文收集器（orient → read）。"""

    def __init__(
        self,
        store,
        manifest: Manifest,
        toc: dict,
        retriever=None,
        llm: LLMClient | None = None,
        embedder=None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_sections: int = DEFAULT_MAX_SECTIONS,
        max_contexts: int = DEFAULT_MAX_CONTEXTS,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_expansions: int = DEFAULT_MAX_EXPANSIONS,
    ) -> None:
        self._store = store
        self._manifest = manifest
        self._toc = toc or {}
        self._retriever = retriever
        self._llm = llm
        self._embedder = embedder
        self._max_entries = max_entries
        self._max_sections = max_sections
        self._max_contexts = max_contexts
        self._max_steps = max_steps
        self._max_expansions = max_expansions
        self._groups = {
            group["path"]: group for group in _flatten_groups(self._toc)
        }

    # ---------- orient ----------

    @property
    def max_steps(self) -> int:
        """配置的补展开轮数上限（graph 的 retry 节点会在此基础上再保证至少 1 轮）。"""
        return self._max_steps

    def orient(self, question: str, route: str = "") -> OrientTrace:
        """定位候选知识单元：LLM 两级漏斗 + hybrid 反查补齐（缺 LLM 时只走 hybrid）。"""
        trace = OrientTrace()
        if not self._groups:
            trace.degraded = "no-toc"
            return trace
        allowed = self._allowed_categories(route)
        if self._llm is not None:
            for target in self._orient_by_llm(question, allowed, trace):
                trace.llm_targets.append(target.entry_id)
                trace.targets.append(target)
        for target in self._orient_by_hybrid(question):
            index = next(
                (i for i, t in enumerate(trace.targets) if t.entry_id == target.entry_id), None
            )
            if index is not None:
                # LLM 只选了条目没选章节时，用 hybrid 命中的那一节补上精确展开位置
                if not trace.targets[index].sections and target.sections:
                    trace.targets[index] = target
                continue
            trace.hybrid_targets.append(target.entry_id)
            trace.targets.append(target)
        trace.targets = trace.targets[: self._max_entries]
        if not trace.targets and not trace.degraded:
            trace.degraded = "no-target"
        return trace

    def _allowed_categories(self, route: str) -> list[str]:
        prefixes = ROUTE_CATEGORY_PREFIXES.get(route)
        paths = [p for p in self._groups if p]
        if not prefixes:
            return paths
        return [p for p in paths if p.split("/")[0] in prefixes] or paths

    def _orient_by_llm(self, question: str, allowed: list[str], trace: OrientTrace) -> list[Target]:
        picked = self._pick_categories(question, allowed)
        if not picked:
            trace.degraded = trace.degraded or "category-miss"
            return []
        trace.categories = picked
        groups = [self._groups[p] for p in picked if p in self._groups]
        picked_entries = self._pick_entries(question, groups)
        trace.llm_calls += 2
        return picked_entries

    def _pick_categories(self, question: str, allowed: list[str]) -> list[str]:
        """第一级漏斗：从分类清单里选 1~3 个分类（清单极小，约 300 token）。"""
        catalog = "\n".join(f"- {p}（{len(self._groups[p]['entries'])}）" for p in allowed)
        text = self._complete(
            ORIENT_CATEGORIES_SYSTEM, f"问题：{question}\n\n目录：\n{catalog}"
        )
        try:
            raw = parse_filter_json(text).get("categories")
        except Exception:
            logger.warning("orient 分类输出解析失败：%s", text[:120])
            return []
        if not isinstance(raw, list):
            return []
        picked = [str(c).strip() for c in raw]
        return [p for p in dict.fromkeys(picked) if p in self._groups][:MAX_CATEGORIES]

    def _narrow_groups(self, question: str, groups: list[dict]) -> list[dict]:
        """条目行过多时先用 `wiki_page` 向量在选中分类内粗排（LLM 只看最相关的前 N 行）。"""
        total = sum(len(g["entries"]) for g in groups)
        if total <= TOC_ENTRY_CAP or self._embedder is None:
            return groups
        if self._store.count(WIKI_PAGE_COLLECTION) == 0:
            return groups
        try:
            [query_vec] = self._embedder.embed([question])
            hits = self._store.query(WIKI_PAGE_COLLECTION, query_vec, n_results=TOC_ENTRY_CAP * 3)
        except Exception:
            logger.warning("orient 的 wiki_page 粗排失败，按目录原序交给 LLM", exc_info=True)
            return groups
        paths = {g["path"] for g in groups}

        def in_group(category: str) -> bool:
            return any(category == p or category.startswith(p + "/") for p in paths)

        ranked: list[str] = []
        for hit in hits:
            meta = hit.get("metadata") or {}
            entry_id_ = str(meta.get("entry_id", ""))
            if in_group(str(meta.get("category", ""))) and entry_id_:
                ranked.append(entry_id_)
            if len(ranked) >= TOC_ENTRY_CAP:
                break
        keep = set(ranked)
        narrowed = [
            {**g, "entries": [line for line in g["entries"] if line["id"] in keep]}
            for g in groups
        ]
        # 组内保持向量序，且丢掉粗排后空掉的分类
        ordered = [
            {**g, "entries": sorted(g["entries"], key=lambda line: ranked.index(line["id"]))}
            for g in narrowed
            if g["entries"]
        ]
        return ordered or groups

    def _pick_entries(self, question: str, groups: list[dict]) -> list[Target]:
        """第二级漏斗：在选中分类的条目行里挑条目与章节（id/章节名照抄，非法输出一律丢弃）。"""
        lines = _entry_lines(self._narrow_groups(question, groups))
        system = ORIENT_ENTRIES_SYSTEM.format(
            max_entries=self._max_entries, max_sections=self._max_sections
        )
        text = self._complete(system, f"问题：{question}\n\n条目清单：\n{lines}")
        try:
            raw = parse_filter_json(text).get("entries")
        except Exception:
            logger.warning("orient 条目输出解析失败：%s", text[:120])
            return []
        if not isinstance(raw, list):
            return []
        targets: list[Target] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            eid = str(item.get("id", "")).strip()
            group = next((g for g in groups if any(e["id"] == eid for e in g["entries"])), None)
            if group is None:
                continue  # LLM 幻想的 id：直接丢弃（向量补齐负责召回）
            titles = [str(s).strip() for s in (item.get("sections") or [])]
            summary = self._manifest.entries.get(eid) or {}
            keys = tuple(
                s["key"]
                for s in summary.get("sections", [])
                if s["title"] in titles
            )[: self._max_sections]
            targets.append(Target(entry_id=eid, sections=keys, origin="llm"))
            if len(targets) >= self._max_entries:
                break
        return targets

    def _orient_by_hybrid(self, question: str) -> list[Target]:
        """第三道保险：现有 hybrid 检索命中的 record 经 manifest 反查**条目 + 命中的那一节**。

        只给 entry_id 会让 read() 按「前 N 节」展开，命中行可能被截掉（如《拍身重量U数》的 4U 节）；
        章节级 records 锚点在这里直接把召回精确到节。
        """
        if self._retriever is None:
            return []
        try:
            records = self._retriever.retrieve(question, top_k=HYBRID_TOP_K)
        except Exception:
            logger.warning("orient 的 hybrid 补齐失败（旁路，不影响已选条目）", exc_info=True)
            return []
        keys_by_entry: dict[str, list[str]] = {}
        for record in records:
            for eid in self._manifest.entry_ids_for_record(record.id):
                summary = self._manifest.entries.get(eid)
                if not summary:
                    continue
                keys = keys_by_entry.setdefault(eid, [])
                for section in summary.get("sections", []):
                    if record.id in section.get("records", []) and section["key"] not in keys:
                        keys.append(section["key"])
        return [
            Target(entry_id=eid, sections=tuple(keys[: self._max_sections]), origin="hybrid")
            for eid, keys in keys_by_entry.items()
        ]

    def _complete(self, system: str, user: str) -> str:
        if self._llm is None:
            return ""
        try:
            return self._llm.complete(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                json_mode=True,
            )
        except Exception:
            logger.warning("orient LLM 调用失败，降级为 hybrid 定位", exc_info=True)
            self._llm = None  # 同一问题不再重试第二次漏斗
            return ""

    # ---------- read ----------

    def read(self, targets: list[Target], question: str = "") -> list[dict]:
        """展开知识单元：按 `entry#section` 从 wiki_section 取章节全文，并收口总量。

        条目数上限是 `max_entries`，但「5 条目 × 2 章节」仍有 10 条上下文的稀释风险
        （实测 technique 路由即为此），故候选章节超出 `max_contexts` 时按问题向量精排取前 N。
        """
        if self._store.count(WIKI_SECTION_COLLECTION) == 0:
            logger.warning("wiki_section 索引为空，请先运行 python -m scripts.build_wiki --index")
            return []
        wanted: list[str] = []
        for target in targets:
            summary = self._manifest.entries.get(target.entry_id)
            if not summary:
                continue
            sections = [s for s in summary["sections"]]
            if target.sections:
                keys = list(target.sections)[: self._max_sections]
            elif self._embedder is not None and question:
                # 全部章节进候选，交给章节级精排挑前 N（比「取前 2 节」准，也支持一题需要多节）
                keys = [s["key"] for s in sections]
            else:
                keys = [s["key"] for s in sections[: self._max_sections]]
            for key in keys:
                doc_id = section_doc_id(target.entry_id, key)
                if doc_id not in wanted:
                    wanted.append(doc_id)
        return [self._to_context(hit) for hit in self._store.get(
            WIKI_SECTION_COLLECTION, self._rank_sections(question, wanted)
        )]

    def _rank_sections(self, question: str, candidates: list[str]) -> list[str]:
        """候选章节超预算时，在候选集内按问题向量精排，并**按条目公平轮转**取前 N。

        纯相似度截断会让一个条目的多节占满预算、把其它已打开条目挤掉（实测掉多跳召回）；
        轮转保证每个已选中条目至少留下一节。
        """
        if len(candidates) <= self._max_contexts or self._embedder is None or not question:
            return candidates[: self._max_contexts]
        try:
            [query_vec] = self._embedder.embed([question])
            hits = self._store.query(
                WIKI_SECTION_COLLECTION, query_vec, n_results=len(candidates), ids=candidates
            )
        except Exception:
            logger.warning("章节级精排失败，按条目顺序截断", exc_info=True)
            return candidates[: self._max_contexts]

        by_entry: dict[str, list[str]] = {}
        for hit in hits:
            by_entry.setdefault(split_anchor(hit["id"])[0], []).append(hit["id"])
        ranked: list[str] = []
        for round_index in range(max(len(v) for v in by_entry.values()) if by_entry else 0):
            for doc_ids in by_entry.values():          # 每个条目第 round_index 相似的一节
                if round_index < len(doc_ids):
                    ranked.append(doc_ids[round_index])
        return ranked[: self._max_contexts]

    @staticmethod
    def _to_context(hit: dict) -> dict:
        """索引记录 → 与 classic 链路同构的 context dict（额外带 wiki 元数据供格式化与回溯）。"""
        meta = dict(hit.get("metadata") or {})
        facets = _load_json(meta.get("facets"), dict)
        records = _load_json(meta.get("records"), list)
        meta["facets"] = facets
        meta["records"] = records
        return {
            "table": _source_table(records),
            "id": hit["id"],
            "document": str(hit.get("document", ""))[:MAX_CONTEXT_CHARS],
            "metadata": meta,
            "distance": hit.get("distance"),
        }

    # ---------- 组合（orient → read → step） ----------

    def _expandable(self, targets: list[Target], contexts: list[dict]) -> dict[str, list[tuple[str, str]]]:
        """可补展开池：已展开条目的**剩余章节** + 它们 `links.out` 一跳指向的条目章节。"""
        used = {str(c.get("id", "")) for c in contexts}
        pool: dict[str, list[tuple[str, str]]] = {}
        hops: list[str] = []
        for target in targets:
            summary = self._manifest.entries.get(target.entry_id) or {}
            rest = [
                (s["key"], s["title"])
                for s in summary.get("sections", [])
                if section_doc_id(target.entry_id, s["key"]) not in used
            ]
            if rest:
                pool.setdefault(target.entry_id, []).extend(rest)
            hops.extend(summary.get("links_out") or [])
        for link in hops:
            eid = link.partition("#")[0]
            if eid in pool:
                continue
            summary = self._manifest.entries.get(eid) or {}
            sections = [(s["key"], s["title"]) for s in summary.get("sections", [])]
            if sections:
                pool[eid] = sections[: self._max_sections]
        return pool

    def step(
        self, question: str, targets: list[Target], contexts: list[dict], trace: OrientTrace | None = None
    ) -> StepDecision:
        """一步导航：LLM 判断已展开信息是否够答，不够则从候选池里挑要补展开的章节。"""
        if self._llm is None:
            return StepDecision()
        pool = self._expandable(targets, contexts)
        if not pool:
            return StepDecision()
        if trace is not None:
            trace.llm_calls += 1

        opened = "\n".join(
            f"- {t.entry_id} 《{self._manifest.title(t.entry_id)}》"
            for t in targets
        )
        candidates = "\n".join(
            f"- {eid} 《{self._manifest.title(eid)}》｜可展开章节: {'、'.join(title for _, title in sections)}"
            for eid, sections in pool.items()
        )
        user = (
            f"问题：{question}\n\n已展开：\n{opened}\n\n"
            f"可补展开：\n{candidates}\n\n最多补展开 {self._max_expansions} 节。"
        )
        text = self._complete(STEP_SYSTEM, user)
        try:
            data = parse_filter_json(text)
        except Exception:
            logger.warning("step 输出解析失败：%s", text[:120])
            return StepDecision()

        budget = self._max_expansions
        expand: list[Target] = []
        for item in data.get("expand") or []:
            if not isinstance(item, dict) or budget <= 0:
                continue
            eid = str(item.get("id", "")).strip()
            options = pool.get(eid)
            if not options:
                continue  # 清单外的 id 一律丢弃
            titles = [str(s).strip() for s in (item.get("sections") or [])]
            keys = [k for k, title in options if title in titles][:budget] or [options[0][0]]
            expand.append(Target(entry_id=eid, sections=tuple(keys), origin="step"))
            budget -= len(keys)
        return StepDecision(enough=bool(data.get("enough", True)) and not expand, expand=tuple(expand))

    def navigate(
        self, question: str, route: str = "", max_steps: int | None = None
    ) -> tuple[list[dict], OrientTrace]:
        """orient → read → （至多 max_steps 轮）补展开，返回 (contexts, trace)。"""
        trace = self.orient(question, route)
        contexts = self.read(trace.targets, question)
        steps = self._max_steps if max_steps is None else max_steps
        for _ in range(max(int(steps), 0)):
            decision = self.step(question, trace.targets, contexts, trace)
            trace.steps.append(
                {
                    "enough": decision.enough,
                    "expand": [
                        section_doc_id(t.entry_id, key) for t in decision.expand for key in t.sections
                    ],
                }
            )
            if decision.enough or not decision.expand:
                break
            known = {str(c.get("id", "")) for c in contexts}
            new = [c for c in self.read(list(decision.expand), question) if c["id"] not in known]
            if not new:
                break
            contexts.extend(new)
            opened = {t.entry_id for t in trace.targets}
            trace.targets.extend(t for t in decision.expand if t.entry_id not in opened)
        trace.targets = [t for t in trace.targets if _in_contexts(t, contexts)]
        return contexts, trace


def _in_contexts(target: Target, contexts: list[dict]) -> bool:
    return any(str(c["id"]).startswith(f"{target.entry_id}#") for c in contexts)


def _flatten_groups(toc: dict) -> list[dict]:
    groups: list[dict] = []
    for category in toc.get("categories") or []:
        for table in category.get("tables") or []:
            if table.get("path"):
                groups.append(table)
    return groups


def _load_json(raw, expected):
    if not raw:
        return expected()
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return expected()
    return data if isinstance(data, expected) else expected()


def _source_table(records: list[str]) -> str:
    """上下文来源表（中文表名）：取第一个 record id 的 collection 前缀反查。"""
    if not records:
        return "wiki"
    collection, _, _ = str(records[0]).rpartition(":")
    return display_name(collection)


def context_line(context: dict, index: int) -> str:
    """Wiki 上下文的 prompt 渲染：`[i] 条目《标题》§章节（属性：…）` + 正文。"""
    meta = context.get("metadata") or {}
    head = f"条目《{meta.get('entry_title', '')}》§{meta.get('section_title', '')}"
    facets = meta.get("facets") or {}
    if meta.get("entry_type") == "product" and facets:
        brief = "，".join(f"{k}:{v}" for k, v in list(facets.items())[:6])
        head = f"{head}（{brief}）"
    document = str(context.get("document", ""))
    parts = document.split("\n", 1)
    # 索引文档首行是《条目》§章节，标题已在 head 里，正文不重复
    body = parts[1] if len(parts) > 1 and parts[0].startswith("《") else document
    return f"[{index}] {head}\n{body.strip()}"


def build_navigator(
    store,
    retriever,
    llm: LLMClient | None,
    wiki_dir: Path,
    data_dir: Path | None = None,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    tables: tuple[SpecTable, ...] = ALL_TABLES,
    embedder=None,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> WikiNavigator | None:
    """从 `data/wiki/` 装配导航器。

    返回 None 的两种情况都由调用方降级为 classic 链路（wiki 是可选增强，绝不能让问答挂掉）：
    尚未编译（缺 manifest/toc）、或编译产物落后于 `data/processed/` 源 CSV。
    """
    manifest = Manifest.load(wiki_dir)
    toc_path = wiki_dir / TOC_NAME
    if manifest is None or not toc_path.exists():
        logger.info("Wiki 尚未编译（%s），问答走 classic 链路", wiki_dir)
        return None
    if data_dir is not None and manifest.source_fingerprint != source_fingerprint(data_dir, tables):
        logger.warning("Wiki 落后于源 CSV，需要重编译；本次问答降级为 classic 链路")
        return None
    toc = json.loads(toc_path.read_text(encoding="utf-8"))
    return WikiNavigator(
        store, manifest, toc, retriever=retriever, llm=llm, embedder=embedder,
        max_entries=max_entries, max_steps=max_steps,
    )
