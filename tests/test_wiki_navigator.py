"""Wiki 在线导航与问答接入测试（全离线：脚本化 LLM + FakeEmbedder + 内存 VectorStore）。

覆盖：orient 两级漏斗与 hybrid 反查补齐、幻想 id 丢弃、read 展开章节、
`mode=wiki` 接入 graph 后的上下文格式与来源，以及 wiki 拿不到知识单元时回落 classic 的降级路径。
"""

import csv
import json
from pathlib import Path

import pytest

from app.agent.graph import BadmintonAgent
from app.ingest.embedder import FakeEmbedder
from app.ingest.pipeline import ingest_table, row_ids
from app.ingest.serializer import KNOWLEDGE_TABLES, SPEC_TABLES
from app.ingest.store import VectorStore
from app.models.spec import SpecTable
from app.rag.llm import LLMClient
from app.rag.retriever import Retriever, resolve_source
from app.wiki.compile import compile_entries, load_records
from app.wiki.indexer import index_wiki
from app.wiki.manifest import (
    TOC_NAME,
    Manifest,
    build_manifest,
    build_toc,
    source_fingerprint,
    write_wiki,
)
from app.wiki.navigator import Target, WikiNavigator, build_navigator, context_line

RACKET: SpecTable = next(t for t in SPEC_TABLES if t.name == "球拍")
SPEC_KNOWLEDGE: SpecTable = next(t for t in KNOWLEDGE_TABLES if t.name == "规格常识")
TABLES = (RACKET, SPEC_KNOWLEDGE)

RACKET_ROWS = [
    {"品牌": "尤尼克斯 YONEX", "型号": "天斧99", "别名": "ASTROX 99", "拍身重量(U)": "4U",
     "中管韧度": "硬", "平衡点类别": "头重", "最高磅数": "35", "参考价": "1350元", "来源": "淘宝"},
    {"品牌": "李宁 LINING", "型号": "战戟12", "别名": "", "拍身重量(U)": "3U",
     "中管韧度": "适中", "平衡点类别": "均衡", "最高磅数": "30", "参考价": "799元", "来源": "淘宝"},
]
KNOWLEDGE_ROWS = [
    {"规格项": "拍身重量U数", "规格值": "4U", "含义说明": "重量约80-84克", "适用建议": "较轻，挥拍快"},
    {"规格项": "拍身重量U数", "规格值": "3U", "含义说明": "重量约85-89克", "适用建议": "攻守兼备，适合大多数选手"},
    {"规格项": "平衡点类别", "规格值": "头重", "含义说明": "平衡点高于295mm", "适用建议": "适合进攻型打法"},
]


def _write(root: Path, table: SpecTable, rows: list[dict]) -> None:
    path = root / table.csv_file
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


class ScriptLLM(LLMClient):
    """按 system 提示分派的脚本化 LLM：路由 / orient 两级漏斗 / step 补展开 / 生成 / 校验，全部离线。"""

    def __init__(self, *, route="equipment", categories=(), entries=(), step=None, filters=None):
        self._route = route
        self._categories = list(categories)
        self._entries = list(entries)
        self._step = step
        self._filters = filters or {}
        self.calls: list[str] = []

    def complete(self, messages, *, json_mode=False) -> str:
        system = messages[0]["content"] if messages else ""
        user = messages[-1]["content"] if messages else ""
        if "路由助手" in system:
            self.calls.append("route")
            return json.dumps({"route": self._route})
        if "校验员" in system:
            self.calls.append("verify")
            return '{"supported": true}'
        if "可补展开" in system:
            self.calls.append("step")
            return json.dumps(self._step or {"enough": True, "expand": []}, ensure_ascii=False)
        if "导航员" in system and "条目清单" in system:
            self.calls.append("orient-entries")
            return json.dumps({"entries": self._entries}, ensure_ascii=False)
        if "导航员" in system:
            self.calls.append("orient-categories")
            return json.dumps({"categories": self._categories}, ensure_ascii=False)
        if json_mode and "问答助手" in system:
            self.calls.append("generate")
            block = user.split("检索内容：", 1)[-1] if "检索内容：" in user else ""
            return json.dumps({"answer": block.strip()[:300], "used": []}, ensure_ascii=False)
        self.calls.append("other")
        return "你好"

    def extract_filters(self, question: str) -> dict:
        return self._filters


@pytest.fixture
def wiki_store(tmp_path: Path):
    """内存库：17 表里的 2 张fixture 表已入库 + wiki 两个 collection 已建索引。"""
    root = tmp_path / "processed"
    _write(root, RACKET, RACKET_ROWS)
    _write(root, SPEC_KNOWLEDGE, KNOWLEDGE_ROWS)
    records = load_records(root, TABLES)
    entries = compile_entries(data_dir=root, tables=TABLES, records=records)

    store = VectorStore()
    embedder = FakeEmbedder()
    for table in TABLES:
        ingest_table(store, embedder, table, root)
    index_wiki(store, embedder, entries)

    manifest = build_manifest(entries, source_fingerprint(root, TABLES))
    toc = build_toc(entries)
    retriever = Retriever(store, embedder, use_bm25=False, use_expansion=False)
    return {
        "root": root, "store": store, "entries": entries, "manifest": manifest,
        "toc": toc, "retriever": retriever,
    }


def _navigator(wiki_store, llm=None, **kwargs) -> WikiNavigator:
    return WikiNavigator(
        wiki_store["store"], wiki_store["manifest"], wiki_store["toc"],
        retriever=wiki_store["retriever"], llm=llm, **kwargs,
    )


def _entry(wiki_store, title: str):
    return next(e for e in wiki_store["entries"] if e.title == title)


def test_orient_uses_two_stage_funnel(wiki_store):
    product = _entry(wiki_store, "尤尼克斯 YONEX 天斧99")
    llm = ScriptLLM(
        categories=["装备规格/球拍"],
        entries=[{"id": product.id, "sections": ["规格参数"]}],
    )
    trace = _navigator(wiki_store, llm).orient("天斧99 有多重", route="equipment")

    assert llm.calls == ["orient-categories", "orient-entries"]
    assert trace.categories == ["装备规格/球拍"]
    assert trace.targets[0].entry_id == product.id
    assert trace.targets[0].origin == "llm"
    assert trace.targets[0].sections == ("specs",)
    assert trace.llm_calls == 2 and not trace.degraded


def test_orient_drops_hallucinated_ids_and_keeps_hybrid(wiki_store):
    llm = ScriptLLM(categories=["装备规格/球拍"], entries=[{"id": "ent_not_real_x_1", "sections": []}])
    trace = _navigator(wiki_store, llm).orient("4U 球拍有多重", route="equipment")

    assert trace.llm_targets == []          # 目录里没有的 id 一律丢弃
    assert trace.hybrid_targets             # 向量反查补回候选
    assert not any(t.entry_id == "ent_not_real_x_1" for t in trace.targets)


def test_orient_without_llm_falls_back_to_hybrid_only(wiki_store):
    trace = _navigator(wiki_store, llm=None).orient("天斧99 有多重", route="equipment")
    assert trace.llm_calls == 0
    assert all(t.origin == "hybrid" for t in trace.targets)
    assert trace.targets


def test_orient_respects_route_and_max_entries(wiki_store):
    llm = ScriptLLM(categories=["规则判罚/BWF官方规则"], entries=[])
    trace = _navigator(wiki_store, llm).orient("场地尺寸是多少", route="rules")
    # 夹具里没有规则表：分类选不到 → 记原因并只靠 hybrid；两模式比较时这类题会回落 classic
    assert trace.categories == []
    assert trace.degraded in ("", "category-miss")

    many = ScriptLLM(categories=["装备规格/球拍"], entries=[])
    targets = _navigator(wiki_store, many, max_entries=1).orient("4U 球拍", route="equipment")
    assert len(targets.targets) <= 1


def test_narrow_groups_uses_page_vectors_when_catalog_is_big(wiki_store, monkeypatch):
    import app.wiki.navigator as nav

    monkeypatch.setattr(nav, "TOC_ENTRY_CAP", 1)
    navigator = _navigator(wiki_store, ScriptLLM(), embedder=FakeEmbedder())
    groups = [
        table
        for category in wiki_store["toc"]["categories"]
        for table in category["tables"]
        if table["path"] == "装备规格/球拍"
    ]
    assert sum(len(g["entries"]) for g in groups) == 2

    narrowed = navigator._narrow_groups("天斧99 有多重", groups)
    assert sum(len(g["entries"]) for g in narrowed) == 1
    assert narrowed[0]["entries"][0]["id"].startswith("ent_racket_specs_")


def test_narrow_groups_skipped_without_embedder(wiki_store):
    groups = [wiki_store["toc"]["categories"][0]["tables"][0]]
    navigator = _navigator(wiki_store, ScriptLLM())
    assert navigator._narrow_groups("天斧99 有多重", groups) == groups


def test_read_offers_all_sections_as_candidates(wiki_store):
    """未指定章节的条目：整页章节都进候选，不再只取前 max_sections 节（修「一题需要多节」）。"""
    concept = _entry(wiki_store, "拍身重量U数")  # 2 节：4U / 3U
    navigator = _navigator(wiki_store, llm=None, embedder=FakeEmbedder(), max_sections=1)
    contexts = navigator.read([Target(concept.id)], "3U代表多少克")
    assert sorted(c["metadata"]["section_title"] for c in contexts) == ["3U", "4U"]


def test_read_caps_total_contexts(wiki_store):
    product = _entry(wiki_store, "尤尼克斯 YONEX 天斧99")  # 3 节
    navigator = _navigator(wiki_store, llm=None, embedder=FakeEmbedder(), max_contexts=1)
    assert len(navigator.read([Target(product.id)], "天斧99 有多重")) == 1


def test_rank_sections_uses_vector_order_within_candidates(wiki_store):
    class _QueryStore:
        def query(self, collection, vec, n_results, ids=None):
            # 反序返回，用于证明排序来自向量命中而不是条目原序
            return [{"id": i} for i in reversed(ids or [])][:n_results]

    navigator = _navigator(wiki_store, llm=None, embedder=FakeEmbedder(), max_contexts=2)
    navigator._store = _QueryStore()
    assert navigator._rank_sections("多重", ["a#1", "a#2", "a#3"]) == ["a#3", "a#2"]
    assert navigator._rank_sections("多重", ["a#1", "a#2", "a#3", "a#4"]) == ["a#4", "a#3"]
    # 候选未超预算时不查库，保持条目原序
    assert navigator._rank_sections("多重", ["a#1", "a#2"]) == ["a#1", "a#2"]


def test_read_without_embedder_keeps_first_sections(wiki_store):
    concept = _entry(wiki_store, "拍身重量U数")
    contexts = _navigator(wiki_store, llm=None, max_sections=1).read([Target(concept.id)], "3U")
    assert [c["metadata"]["section_title"] for c in contexts] == ["4U"]  # 无向量时保守取首节


def test_rank_sections_without_question_only_truncates(wiki_store):
    navigator = _navigator(wiki_store, llm=None, embedder=FakeEmbedder(), max_contexts=2)
    candidates = [f"ent_a#s{i}" for i in range(5)]
    assert navigator._rank_sections("", candidates) == candidates[:2]
    assert navigator._rank_sections("任意问题", []) == []


def test_read_expands_sections_with_anchors(wiki_store):
    product = _entry(wiki_store, "尤尼克斯 YONEX 天斧99")
    concept = _entry(wiki_store, "拍身重量U数")
    contexts = _navigator(wiki_store, llm=None).read([
        Target(product.id, ("specs",)),
        Target(concept.id, ("row-0",)),
    ])

    assert [c["table"] for c in contexts] == ["球拍", "规格常识"]  # 回溯到原始中文表名
    assert all(":" in c["id"] or "#" in c["id"] for c in contexts)
    meta = contexts[0]["metadata"]
    assert meta["entry_title"] == "尤尼克斯 YONEX 天斧99"
    assert meta["records"] == [row_ids(RACKET, RACKET_ROWS)[0]]
    assert "拍身重量(U)" in contexts[0]["document"]


def test_context_line_and_source_rendering(wiki_store):
    product = _entry(wiki_store, "尤尼克斯 YONEX 天斧99")
    context = _navigator(wiki_store, llm=None).read([Target(product.id, ("specs",))])[0]
    line = context_line(context, 1)
    assert line.startswith("[1] 条目《尤尼克斯 YONEX 天斧99》§规格参数")
    assert "品牌:尤尼克斯 YONEX" in line          # 产品条目带 facets 摘要
    assert "《尤尼克斯 YONEX 天斧99》§规格参数" not in line.split("\n", 1)[1]  # 正文不重复标题
    assert resolve_source(context) == ("尤尼克斯 YONEX 天斧99", "规格参数")


def _no_hybrid_navigator(wiki_store, llm, **kwargs):
    """只测 orient/step 本身：关掉 hybrid 补齐与目录粗排。"""
    return WikiNavigator(
        wiki_store["store"], wiki_store["manifest"], wiki_store["toc"],
        retriever=None, llm=llm, embedder=FakeEmbedder(), **kwargs,
    )


def test_step_expands_missing_sections(wiki_store):
    product = _entry(wiki_store, "尤尼克斯 YONEX 天斧99")
    llm = ScriptLLM(
        categories=["装备规格/球拍"],
        entries=[{"id": product.id, "sections": ["概况"]}],
        step={"enough": False, "expand": [{"id": product.id, "sections": ["适用人群与打法"]}]},
    )
    contexts, trace = _no_hybrid_navigator(wiki_store, llm).navigate("天斧99 适合谁", "equipment")

    titles = [c["metadata"]["section_title"] for c in contexts]
    assert titles == ["概况", "适用人群与打法"]
    assert trace.steps == [{"enough": False, "expand": [f"{product.id}#fit"]}]
    assert trace.llm_calls == 3  # orient 两次 + step 一次
    assert [t.origin for t in trace.targets] == ["llm"]


def test_step_skipped_when_information_is_enough(wiki_store):
    product = _entry(wiki_store, "尤尼克斯 YONEX 天斧99")
    llm = ScriptLLM(
        categories=["装备规格/球拍"],
        entries=[{"id": product.id, "sections": ["规格参数"]}],
        step={"enough": True, "expand": []},
    )
    contexts, trace = _no_hybrid_navigator(wiki_store, llm).navigate("天斧99 多重", "equipment")

    assert [c["metadata"]["section_title"] for c in contexts] == ["规格参数"]
    assert trace.steps == [{"enough": True, "expand": []}]


def test_step_drops_ids_outside_the_pool(wiki_store):
    product = _entry(wiki_store, "尤尼克斯 YONEX 天斧99")
    llm = ScriptLLM(
        categories=["装备规格/球拍"],
        entries=[{"id": product.id, "sections": ["概况"]}],
        step={"enough": False, "expand": [{"id": "ent_幻想条目_x1", "sections": ["任意"]}]},
    )
    contexts, trace = _no_hybrid_navigator(wiki_store, llm).navigate("天斧99 适合谁", "equipment")

    assert [c["metadata"]["section_title"] for c in contexts] == ["概况"]
    assert trace.steps[0]["expand"] == []       # 清单外的 id 被丢弃 → 没有可展开项，本轮即停
    assert trace.steps[0]["enough"] is False    # enough 保留 LLM 原判，轨迹如实记录「觉得不够但无合法目标」


def test_step_zero_budget_does_not_call_llm(wiki_store):
    product = _entry(wiki_store, "尤尼克斯 YONEX 天斧99")
    llm = ScriptLLM(
        categories=["装备规格/球拍"], entries=[{"id": product.id, "sections": ["概况"]}]
    )
    contexts, trace = _no_hybrid_navigator(wiki_store, llm, max_steps=0).navigate(
        "天斧99 适合谁", "equipment"
    )
    assert "step" not in llm.calls and trace.steps == [] and len(contexts) == 1


def test_expandable_pool_includes_one_hop_links(wiki_store):
    product = _entry(wiki_store, "尤尼克斯 YONEX 天斧99")
    navigator = _no_hybrid_navigator(wiki_store, ScriptLLM())
    pool = navigator._expandable([Target(product.id, ("overview",))], [{"id": f"{product.id}#overview"}])
    linked = product.links_out[0].split("#")[0]
    assert linked in pool                       # 沿 links.out 一跳：可补展开关联概念页
    assert "fit" in [key for key, _ in pool[product.id]]  # 同条目未展开的章节也在池里


def test_agent_wiki_mode_feeds_sections_to_generate(wiki_store):
    llm = ScriptLLM(
        route="equipment",
        categories=["装备规格/球拍"],
        entries=[{"id": _entry(wiki_store, "尤尼克斯 YONEX 天斧99").id, "sections": ["规格参数"]}],
    )
    agent = BadmintonAgent(
        wiki_store["retriever"], llm, wiki=_navigator(wiki_store, llm), default_mode="wiki"
    )
    state = agent.invoke({"question": "天斧99 有多重", "history": [], "trace": []})

    assert state["mode"] == "wiki"
    assert [n["node"] for n in state["trace"]] == ["route", "wiki", "generate", "verify"]
    assert "条目《尤尼克斯 YONEX 天斧99》§规格参数" in state["answer"]
    assert state["sources"][0]["table"] == "球拍"
    assert state["sources"][0]["brand"] == "尤尼克斯 YONEX 天斧99"
    assert state["wiki_trace"]["targets"][0]["origin"] == "llm"


def test_agent_classic_mode_unchanged(wiki_store):
    llm = ScriptLLM(route="equipment", categories=["装备规格/球拍"], entries=[])
    agent = BadmintonAgent(
        wiki_store["retriever"], llm, wiki=_navigator(wiki_store, llm), default_mode="classic"
    )
    state = agent.invoke({"question": "天斧99 有多重", "history": [], "trace": []})

    assert state["mode"] == "classic"
    assert [n["node"] for n in state["trace"]] == ["route", "equipment", "generate", "verify"]
    assert state.get("wiki_trace") in (None, {})
    assert all("#" not in c["id"] for c in state["contexts"])


def test_agent_degrades_to_classic_when_wiki_finds_nothing(wiki_store):
    llm = ScriptLLM(route="equipment", categories=["器材常识/规格常识"], entries=[])
    navigator = WikiNavigator(
        wiki_store["store"], wiki_store["manifest"], {},  # 无目录 → orient 直接降级
        retriever=wiki_store["retriever"], llm=llm,
    )
    agent = BadmintonAgent(wiki_store["retriever"], llm, wiki=navigator, default_mode="wiki")
    state = agent.invoke({"question": "天斧99 有多重", "history": [], "trace": []})

    assert [n["node"] for n in state["trace"]] == ["route", "wiki", "generate", "verify"]
    assert state["wiki_trace"]["degraded"] == "no-toc"
    assert state["contexts"] and all("#" not in c["id"] for c in state["contexts"])  # 已是 classic 记录


def test_agent_falls_back_to_classic_without_navigator(wiki_store):
    llm = ScriptLLM(route="equipment")
    agent = BadmintonAgent(wiki_store["retriever"], llm, wiki=None, default_mode="wiki")
    state = agent.invoke({"question": "天斧99 有多重", "history": [], "trace": [], "mode": "wiki"})
    assert state["mode"] == "classic"
    assert "equipment" in [n["node"] for n in state["trace"]]


def test_wiki_debug_pipeline_shows_page_turns(wiki_store):
    from app.rag.debug import wiki_debug_pipeline

    product = _entry(wiki_store, "尤尼克斯 YONEX 天斧99")
    llm = ScriptLLM(
        categories=["装备规格/球拍"],
        entries=[{"id": product.id, "sections": ["概况"]}],
        step={"enough": False, "expand": [{"id": product.id, "sections": ["规格参数"]}]},
    )
    blob = wiki_debug_pipeline(
        _no_hybrid_navigator(wiki_store, llm), "天斧99 多重", llm, route="equipment", with_answer=False
    )

    assert blob["mode"] == "wiki"
    assert [c["origin"] for c in blob["candidates"]] == ["orient", "step"]
    assert blob["wiki_trace"]["categories"] == ["装备规格/球拍"]
    assert blob["wiki_trace"]["steps"][0]["expand"] == [f"{product.id}#specs"]
    assert "条目《尤尼克斯 YONEX 天斧99》§规格参数" in blob["context_block"]
    assert blob["answer"] is None  # with_answer=False 省 token


def test_build_navigator_requires_fresh_compiled_wiki(wiki_store, tmp_path: Path):
    root = wiki_store["root"]
    wiki_dir = tmp_path / "wiki"
    fingerprint = source_fingerprint(root, TABLES)
    build_manifest(wiki_store["entries"], fingerprint).save(wiki_dir)
    retriever = wiki_store["retriever"]

    assert build_navigator(wiki_store["store"], retriever, None, tmp_path / "nope") is None

    write_wiki(wiki_dir, wiki_store["entries"], fingerprint)
    assert (wiki_dir / TOC_NAME).exists()
    navigator = build_navigator(
        wiki_store["store"], retriever, None, wiki_dir, root, tables=TABLES
    )
    assert isinstance(navigator, WikiNavigator)

    # 源表变了但没重编译 → 判定落后，返回 None 让问答降级 classic
    _write(root, SPEC_KNOWLEDGE, KNOWLEDGE_ROWS[:1])
    assert build_navigator(
        wiki_store["store"], retriever, None, wiki_dir, root, tables=TABLES
    ) is None
    assert Manifest.load(wiki_dir).stats["entries"] == len(wiki_store["entries"])
