"""Chroma 向量库封装：批量入库 + 向量检索。

- 未指定 persist_dir 时用内存版（EphemeralClient），供测试；
- 指定 persist_dir 时用 PersistentClient 持久化到磁盘；
- 一律传入预计算 embeddings，不依赖 Chroma 内置 embedding 函数；
- 中文表名（SPEC_TABLES/KNOWLEDGE_TABLES 用中文名，面向用户）与 Chroma collection 名
  （仅允许 [A-Za-z0-9._-]，3-512 位）之间的映射由 `collection_name()` 统一处理。
"""

from __future__ import annotations

import ctypes
import logging
import platform
import re
from pathlib import Path

import chromadb

logger = logging.getLogger(__name__)

_STDIO_LIMIT = 2048

# Chroma collection 名合法性：3-512 位，仅 [A-Za-z0-9._-]，首尾为字母数字
_COLLECTION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,511}$")

# 中文表名 → Chroma collection 名（data/chroma 既有数据即按英文名存储，
# 映射保证：中文表名入库/查询落到同名英文 collection，重入库幂等不产生双份）
COLLECTION_NAMES: dict[str, str] = {
    "球拍": "racket_specs",
    "羽毛球": "shuttlecock_specs",
    "球线": "string_specs",
    "手胶": "grip_specs",
    "球鞋": "shoe_specs",
    "BWF官方规则": "bwf_rules",
    "常见判罚": "common_penalties",
    "战术": "tactics",
    "手法技术": "hand_techniques",
    "步法技术": "footwork_techniques",
    "毛片等级": "feather_grade",
    "毛片类型": "feather_type",
    "球头材质": "cork_material",
    "耐打度影响因素": "durability_factors",
    "速度等级": "speed_grade",
    "飞行稳定性影响因素": "flight_stability",
    "规格常识": "spec_knowledge",
}


def collection_name(table: str) -> str:
    """表名 → 合法 Chroma collection 名（已合法的英文名/文档 collection 原样返回）。"""
    if _COLLECTION_NAME_RE.match(table):
        return table
    name = COLLECTION_NAMES.get(table)
    if name is None:
        raise ValueError(
            f"表 {table!r} 不是合法 Chroma collection 名，且未在 COLLECTION_NAMES 注册"
        )
    return name


def display_name(table: str) -> str:
    """collection 名 → 中文表名（反查；未注册原样返回）。"""
    for zh, en in COLLECTION_NAMES.items():
        if en == table:
            return zh
    return table


def _raise_windows_stdio_limit() -> None:
    """Windows 下 Chroma 的 HNSW 缓存大小取 _getmaxstdio()//5，且查询会持续占用 stdio 句柄；
    默认 512 在检索 16 个 collection 后会出现 'Nothing found on disk'，这里把上限调大。"""
    if platform.system() != "Windows":
        return
    try:
        ctypes.windll.msvcrt._setmaxstdio(_STDIO_LIMIT)
    except Exception:
        logger.warning("提高 Windows stdio 上限失败", exc_info=True)

# 检索记录：文档、元数据、距离
QueryResult = dict


class VectorStore:
    """按规格表分 collection 的向量库。"""

    def __init__(self, persist_dir: Path | str | None = None) -> None:
        _raise_windows_stdio_limit()
        if persist_dir is None:
            # 内存版共享同一个进程内系统，初始化时清空，保证每次实例彼此隔离（测试用）
            self._client = chromadb.EphemeralClient()
            self._clear_collections()
        else:
            Path(persist_dir).mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(persist_dir))

    def _clear_collections(self) -> None:
        """删除全部 collection（等价于清空向量库）。"""
        for col in self._client.list_collections():
            self._client.delete_collection(col.name)

    def _collection(self, table: str):
        return self._client.get_or_create_collection(
            name=collection_name(table),
            metadata={"hnsw:space": "cosine"},
        )

    def delete_collection(self, table: str) -> None:
        """删除指定 collection（存在则删，不存在静默）。"""
        name = collection_name(table)
        names = {col.name for col in self._client.list_collections()}
        if name in names:
            self._client.delete_collection(name)

    def list_collections(self) -> list[str]:
        """返回全部 collection 名。"""
        return [col.name for col in self._client.list_collections()]

    def add(
        self,
        table: str,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
        embeddings: list[list[float]],
    ) -> None:
        """批量 upsert。空 id/doc 直接跳过。"""
        cols = self._collection(table)
        cols.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )

    def query(
        self,
        table: str,
        embedding: list[float],
        n_results: int = 10,
        ids: list[str] | None = None,
    ) -> list[QueryResult]:
        """按向量检索，返回按距离升序排列的记录列表。

        `ids` 非空时把候选集限定在这些文档 id 内（wiki 的章节级精排用），None 时为全库检索。
        """
        cols = self._collection(table)
        if cols.count() == 0:
            return []
        res = cols.query(
            query_embeddings=[embedding],
            n_results=n_results,
            ids=ids,
            include=["documents", "metadatas", "distances"],
        )
        hit_ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        return [
            {"id": i, "document": d, "metadata": m or {}, "distance": dist}
            for i, d, m, dist in zip(hit_ids, docs, metas, dists)
        ]

    def get(self, table: str, ids: list[str]) -> list[QueryResult]:
        """按 id 取记录（不检索）；distance 固定为 None。供 BM25 独有命中补齐用。"""
        cols = self._collection(table)
        if cols.count() == 0 or not ids:
            return []
        res = cols.get(ids=ids, include=["documents", "metadatas"])
        by_id = {
            i: (d, m or {})
            for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])
        }
        return [
            {"id": i, "document": by_id[i][0], "metadata": by_id[i][1], "distance": None}
            for i in ids
            if i in by_id
        ]

    def get_all(self, table: str) -> list[QueryResult]:
        """返回表内全量记录（不检索）。供 BM25 索引构建用。"""
        cols = self._collection(table)
        if cols.count() == 0:
            return []
        res = cols.get(include=["documents", "metadatas"])
        return [
            {"id": i, "document": d, "metadata": m or {}, "distance": None}
            for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])
        ]

    def count(self, table: str) -> int:
        return self._collection(table).count()

    def update_metadata(self, table: str, ids: list[str], fields: dict) -> None:
        """合并更新指定 id 的 metadata 字段（不重嵌文档/向量；管理端打标用）。

        只更新库中已存在的 id（Chroma update 语义）；fields 与原 metadata 合并。
        """
        cols = self._collection(table)
        existing = cols.get(ids=ids, include=["metadatas"])
        by_id = dict(zip(existing["ids"], existing["metadatas"] or []))
        merged = [{**(by_id.get(i) or {}), **fields} for i in ids]
        cols.update(ids=ids, metadatas=merged)

    def delete(self, table: str, ids: list[str]) -> None:
        """删除指定 id 的文档（不存在静默）。供 wiki 索引清理已消失的条目页。"""
        if not ids:
            return
        self._collection(table).delete(ids=ids)

    def list_ids(self, table: str) -> list[str]:
        """只取 collection 内的全部文档 id（不拉正文/元数据），供增量比对用。"""
        cols = self._collection(table)
        if cols.count() == 0:
            return []
        return list(cols.get(include=[]).get("ids") or [])

    def reset(self) -> None:
        """清空全部数据（内存/磁盘皆可）。"""
        self._clear_collections()
