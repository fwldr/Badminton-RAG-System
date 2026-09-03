"""向量 + BM25 混合检索：跨全部（规格表 + 知识表）collection 检索，可选同义词查询扩展。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.ingest.embedder import Embedder
from app.ingest.serializer import ALL_TABLES
from app.ingest.store import VectorStore, display_name
from app.models.spec import SpecTable
from app.rag.bm25 import Bm25Index
from app.rag.query_expander import SYNONYMS, expand

logger = logging.getLogger(__name__)

# 文档类 collection 前缀：
# - 文本链路（doc_*/pdf_*）：文本 embedding 向量，进全表检索（retrieve）/BM25/document 路由；
# - 多模态（img_*）：SiliconFlow Qwen3-VL 图片向量，**与文本向量不同空间**，
#   只在 document 路由用 vision_embed.embed_text 检索（不进 /ask 全表，避免低信息文本干扰答案）
DOC_TEXT_PREFIXES = ("doc_", "pdf_")
DOC_IMAGE_PREFIX = "img_"


def document_text_collections(store: VectorStore) -> list[str]:
    """返回文本类文档 collection（doc_*/pdf_*，含管理端上传与 CLI 批量入库）。"""
    return [c for c in store.list_collections() if c.startswith(DOC_TEXT_PREFIXES)]


def document_image_collections(store: VectorStore) -> list[str]:
    """返回多模态图片 collection（img_*，SiliconFlow Qwen3-VL 独立向量空间）。"""
    return [c for c in store.list_collections() if c.startswith(DOC_IMAGE_PREFIX)]


def document_collections(store: VectorStore) -> list[str]:
    """document 路由检索集合 = 文本类 + 多模态图片类。"""
    return document_text_collections(store) + document_image_collections(store)


@dataclass(frozen=True)
class Record:
    """一条检索命中的记录。"""

    table: str
    id: str
    text: str
    metadata: dict
    distance: float | None

    def to_dict(self) -> dict:
        """转成 dict 便于后续过滤/生成环节使用。"""
        return {
            "table": self.table,
            "id": self.id,
            "document": self.text,
            "metadata": self.metadata,
            "distance": self.distance,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Record:
        """从 to_dict() 产物还原（属性过滤后的 dict → 可再精排的 Record）。"""
        return cls(
            table=data["table"],
            id=data["id"],
            text=data["document"],
            metadata=data.get("metadata") or {},
            distance=data.get("distance"),
        )


def _table_spec(table: str) -> SpecTable | None:
    # 中文表名（ALL_TABLES.name）直接匹配；英文 collection 名（定向路由/文档路径）
    # 经 display_name 反查，保证来源展示为中文表名
    for t in ALL_TABLES:
        if t.name == table:
            return t
    zh = display_name(table)
    return next((t for t in ALL_TABLES if t.name == zh), None)


def table_display_name(table: str) -> str:
    """英文 collection 名 → 中文表名（csv_file 去掉 knowledge/ 前缀与扩展名）。"""
    spec = _table_spec(table)
    return Path(spec.csv_file).stem if spec else table


def _subject_field(table: str) -> str | None:
    """知识表的「主题名」列 = 注册表 metadata_fields 中第一个非「来源文件」的字段。"""
    spec = _table_spec(table)
    if not spec:
        return None
    for field in spec.metadata_fields:
        if field != "来源文件":
            return field
    return None


def resolve_source(record: dict) -> tuple[str, str]:
    """解析来源展示用的 (品牌, 型号)。

    - wiki 模式上下文：(条目标题, 章节名)——table 已是原始中文表名，可继续回溯到行；
    - 规格表：品牌 + 型号/名称；
    - 知识表（无品牌）：表名（中文）+ 主题名（metadata 首列，按注册表字段名取值，
      避免依赖 Chroma 返回的 dict 键顺序）。
    """
    meta = record.get("metadata") or {}
    if meta.get("entry_title"):
        return str(meta["entry_title"]), str(meta.get("section_title", ""))
    if meta.get("品牌"):
        brand = str(meta["品牌"]).strip()
        model = str(meta.get("型号") or meta.get("名称", "")).strip()
        return brand, model
    # 上传文档（doc_{id}）：来源显示文件名，便于追溯
    filename = str(meta.get("文件名", "")).strip()
    if filename:
        return "上传文档", filename
    brand = table_display_name(record.get("table", ""))
    subject = _subject_field(record.get("table", ""))
    model = str(meta.get(subject, "")).strip() if subject else ""
    return brand, model


class Retriever:
    """向量检索器（可选 BM25 混合 + 同义词查询扩展）。"""

    def __init__(
        self,
        store: VectorStore,
        embedder: Embedder,
        use_bm25: bool = False,
        use_expansion: bool = True,
        extra_synonyms: list[tuple[str, ...]] | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._use_bm25 = use_bm25
        self._use_expansion = use_expansion
        # 同义词组 = 内置 + 管理端 RAG 词典注入（extra_synonyms）
        self._synonyms: tuple[tuple[str, ...], ...] = tuple(SYNONYMS) + tuple(extra_synonyms or ())
        self._bm25: Bm25Index | None = None
        self._bm25_fingerprint: tuple[tuple[str, int], ...] | None = None

    def retrieve(
        self,
        question: str,
        top_k: int = 10,
        per_table_k: int = 8,
        max_per_table: int = 4,
        use_bm25: bool | None = None,
        use_expansion: bool | None = None,
    ) -> list[Record]:
        """每个 collection 查 per_table_k 条后合并，按距离排序取全局前 top_k，并追加 BM25 词法补充。

        - per_table_k 保证 16 张表都有代表进候选池；
        - max_per_table 是多样性约束：同一 collection 在**主结果**和**词法补充**各自
          最多保留该条数，防止单表刷屏；
        - use_bm25 为 None 时沿用构造时的 _use_bm25；开启时返回**候选池**：
          - 主结果：按向量距离升序（语义精度优先），取 top_k；
          - 词法补充：主结果未覆盖的 BM25 命中按表内排名升序追加（独立受 max_per_table
            约束），解决向量对短属性词（如「红色」）召回弱的问题。
        - use_expansion 为 None 时沿用构造时的 _use_expansion（默认开）：开启时对原查询
          做同义词扩展（如「杀球」→「杀球/扣杀/劈杀」），对每个扩展查询分别做向量/BM25
          检索，结果按 id 合并去重，distance 取各查询中最优（最小），BM25 排名取最优（最小）；
          未命中同义词时 expand 返回 [原查询]，行为与关闭一致。

        混合模式早期版本用 RRF 融合（向量排名 + BM25 排名），实测发现 BM25 对
        「单打/双打」这类高频词过召回会拉低语义问题的精度（单双打场地从可答退回兜底），
        故改为向量主排序 + BM25 独立补充：语义问答仍由向量主导，词法召回喂给属性过滤
        窄化——如「推荐红色的手胶」里 GP203 只在 BM25 侧被召回，经颜色过滤后进入生成窗口；
        无过滤条件时回退到主结果前 filter_top_k 条，不受补充干扰。
        """
        use_bm25 = self._use_bm25 if use_bm25 is None else use_bm25
        use_expansion = self._use_expansion if use_expansion is None else use_expansion
        # 查询改写：同义词扩展成多个查询（未命中时 expand 返回 [原查询]，行为不变）
        queries = expand(question, self._synonyms) if use_expansion else [question]

        # 对每个扩展查询做向量/BM25 检索，结果按 id 合并：distance 取各查询中最优（最小）
        merged_by_id: dict[str, Record] = {}
        bm25_ranks: dict[str, int] = {}
        for query in queries:
            [query_vec] = self._embedder.embed([query])
            for table in ALL_TABLES:
                hits = self._store.query(table.name, query_vec, n_results=per_table_k)
                for h in hits:
                    rec = Record(
                        table=table.name,
                        id=h["id"],
                        text=h["document"],
                        metadata=h["metadata"],
                        distance=h["distance"],
                    )
                    prev = merged_by_id.get(rec.id)
                    if prev is None or rec.distance < prev.distance:
                        merged_by_id[rec.id] = rec
            # 上传文档（doc_*/pdf_* 文本类 collection）也纳入向量检索；
            # img_* 多模态向量与文本 embedding 不同空间，只在 document 路由用 vision 向量检索（避免干扰 /ask 答案）
            for table in self._store.list_collections():
                if table.startswith(DOC_TEXT_PREFIXES):
                    hits = self._store.query(table, query_vec, n_results=per_table_k)
                    for h in hits:
                        rec = Record(
                            table=table,
                            id=h["id"],
                            text=h["document"],
                            metadata=h["metadata"],
                            distance=h["distance"],
                        )
                        prev = merged_by_id.get(rec.id)
                        if prev is None or rec.distance < prev.distance:
                            merged_by_id[rec.id] = rec
            if use_bm25:
                self._ensure_bm25_index()
                bm25_hits = self._bm25.search(query, per_table_k) if self._bm25 else {}
                for rid, rank in bm25_hits.items():
                    if rid not in bm25_ranks or rank < bm25_ranks[rid]:
                        bm25_ranks[rid] = rank

        merged = list(merged_by_id.values())
        if use_bm25:
            by_id = merged_by_id

            def materialize(rid: str) -> Record | None:
                return by_id.get(rid) or self._fetch_record(rid)

            main = self._diversity(
                sorted(merged, key=lambda r: r.distance), max_per_table
            )[:top_k]
            main_ids = {r.id for r in main}
            supplement = [
                r
                for rid in sorted(bm25_ranks, key=lambda rid: bm25_ranks[rid])
                for r in [materialize(rid)]
                if r is not None and rid not in main_ids
            ]
            supplement = self._diversity(supplement, max_per_table)
            return (main + supplement)[: top_k + per_table_k]
        merged.sort(key=lambda r: r.distance)
        return self._diversity(merged, max_per_table)[:top_k]

    def _ensure_bm25_index(self) -> None:
        """BM25 索引懒构建并缓存；collection 计数变化即重建（重新入库后失效）。

        覆盖 ALL_TABLES + 全部文档 collection（doc_*/pdf_*/img_*）：修复上传文档无词法
        召回、文档重入库后指纹不失效的盲区；img_* 的 "[图片] 文件名" 文本也参与词法命中。
        """
        doc_colls = document_collections(self._store)
        fingerprint = tuple((t.name, self._store.count(t.name)) for t in ALL_TABLES) + tuple(
            (c, self._store.count(c)) for c in doc_colls
        )
        if self._bm25 is None or self._bm25_fingerprint != fingerprint:
            names = [t.name for t in ALL_TABLES] + doc_colls
            index = Bm25Index()
            index.build(
                names,
                lambda table: [
                    (h["id"], h["document"]) for h in self._store.get_all(table)
                ],
            )
            self._bm25 = index
            self._bm25_fingerprint = fingerprint

    def _fetch_record(self, rid: str) -> Record | None:
        """BM25 独有命中（不在向量候选池里）时按 id 从库中取回完整记录。"""
        table, _, _ = rid.rpartition(":")
        hits = self._store.get(table, [rid])
        if not hits:
            return None
        h = hits[0]
        return Record(
            table=table,
            id=rid,
            text=h["document"],
            metadata=h["metadata"],
            distance=h["distance"],
        )

    @staticmethod
    def _diversity(records: list[Record], max_per_table: int) -> list[Record]:
        """多样性约束：同一 collection 最多保留 max_per_table 条（保持输入顺序）。"""
        kept: list[Record] = []
        seen: dict[str, int] = {}
        for r in records:
            if seen.get(r.table, 0) >= max_per_table:
                continue
            seen[r.table] = seen.get(r.table, 0) + 1
            kept.append(r)
        return kept
