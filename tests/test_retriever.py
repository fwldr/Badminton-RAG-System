"""Retriever 检索单测：per_table_k 提升 + 多样性约束 + 同义词查询扩展合并。

用 FakeEmbedder + 内存库，不触网。
"""

from app.ingest.embedder import FakeEmbedder
from app.ingest.store import VectorStore
from app.rag.retriever import Retriever


def _build_store(docs_a: list[str], docs_b: list[str]) -> tuple[VectorStore, FakeEmbedder]:
    """把 docs_a/docs_b 分别入库到 racket_specs / grip_specs 两个真实表。"""
    store = VectorStore()
    embedder = FakeEmbedder()
    for table, docs in (("racket_specs", docs_a), ("grip_specs", docs_b)):
        if docs:
            ids = [f"{table}:{i}" for i in range(len(docs))]
            store.add(table, ids, docs, [{"来源": "测试"} for _ in docs], embedder.embed(docs))
    return store, embedder


def test_single_table_at_most_max_per_table():
    # 6 条几乎相同的记录：per_table_k 默认 8 会全部取回，但合并后同一表最多保留 4 条
    doc = "李宁 LINING 雷霆90，重量4U，进攻型，适合专业级。"
    store, embedder = _build_store([doc] * 6, ["尤尼克斯 YONEX AC102EX，PU手胶"])
    hits = Retriever(store, embedder).retrieve("李宁雷霆90", top_k=10)
    racket_hits = [r for r in hits if r.table == "球拍"]
    assert len(racket_hits) <= 4, "同一 collection 合并后最多保留 4 条"
    assert any(r.table == "手胶" for r in hits), "其他表的代表应保留在结果里"
    assert len(hits) <= 10, "总条数不超过 top_k"


def test_per_table_k_explicit_still_works():
    # 向后兼容：显式传 per_table_k 仍生效，结果条数受其约束
    docs = [f"尤尼克斯 YONEX 天斧{i}，重量4U，进攻型。" for i in range(6)]
    store, embedder = _build_store(docs, [])
    hits = Retriever(store, embedder).retrieve("尤尼克斯天斧", top_k=10, per_table_k=2)
    assert len(hits) == 2
    assert all(r.table == "球拍" for r in hits)


def test_expansion_recalls_synonym_doc():
    # 同义词扩展：原查询「怎么杀球」只命中「杀球」文档，「扣杀」文档排进单表 top-8 之外；
    # 扩展出「怎么扣杀」后该文档进入候选池（多查询按 id 合并，距离取各查询中最优）
    filler = "李宁雷霆90，杀球凶狠杀球，进攻型球拍。"
    d_syn = "扣杀扣杀威力大。"
    store, embedder = _build_store([d_syn] + [filler] * 11, [])
    q = "怎么杀球"
    hits_off = Retriever(store, embedder, use_expansion=False).retrieve(
        q, top_k=10, per_table_k=8
    )
    assert not any("扣杀" in r.text for r in hits_off), "未扩展时扣杀文档不在单表 top-8 内"
    hits_on = Retriever(store, embedder).retrieve(q, top_k=10, per_table_k=8)
    assert any("扣杀" in r.text for r in hits_on), "扩展查询「怎么扣杀」应把扣杀文档带入候选池"


def test_expansion_merges_by_id_and_keeps_best_distance():
    # 同一 id 被原查询与多个扩展查询都命中：合并去重只留一条，distance 取各查询中最优（最小）
    store, embedder = _build_store(["威克多突击9900，扣杀威力大，进攻型球拍。"], [])
    q = "怎么杀球"
    hits = Retriever(store, embedder).retrieve(q, top_k=10, per_table_k=1)
    assert len(hits) == 1, "多查询命中同一 id 应按 id 合并去重"
    # 分别用原查询与扩展查询检索（关闭扩展），合并结果距离应等于其中最小者
    d_orig = Retriever(store, embedder, use_expansion=False).retrieve(
        q, top_k=10, per_table_k=1
    )[0].distance
    d_exp1 = Retriever(store, embedder, use_expansion=False).retrieve(
        "怎么扣杀", top_k=10, per_table_k=1
    )[0].distance
    d_exp2 = Retriever(store, embedder, use_expansion=False).retrieve(
        "怎么劈杀", top_k=10, per_table_k=1
    )[0].distance
    assert hits[0].distance == min(d_orig, d_exp1, d_exp2)


def test_no_synonym_query_expansion_is_noop():
    # 未命中同义词时，开启/关闭扩展行为完全一致（expand 返回 [原查询]）
    store, embedder = _build_store(["李宁雷霆90，进攻型球拍，专业级。"], ["尤尼克斯 YONEX AC102EX，PU手胶"])
    q = "推荐一款进攻型球拍"
    r_on = Retriever(store, embedder).retrieve(q, top_k=10)
    r_off = Retriever(store, embedder, use_expansion=False).retrieve(q, top_k=10)
    assert [(r.id, r.distance) for r in r_on] == [(r.id, r.distance) for r in r_off]
