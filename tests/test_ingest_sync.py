"""增量同步测试：行主键 id、内容 digest 比对、只重嵌变化行、陈旧行清理。

全离线：FakeEmbedder（计数包装验证「调了几次 embedding」）+ 内存 VectorStore + 临时 CSV。
覆盖设计文档《badminton-rag-incremental-sync-plan.md》的全部行为承诺：
- id 与行位置无关（中间插行不改变既有行 id）；
- 改哪行重嵌哪行，未变行零成本跳过，重复 sync 收敛为零写入；
- CSV 删除行 / 改主键 / 旧 `{coll}:{行号}` 库迁移 → 陈旧 id 自动清理；
- 主键重复按出现序消歧、主键为空的行回退内容哈希，均不炸。
"""

import csv
from dataclasses import replace
from pathlib import Path

import pytest

from app.ingest.embedder import FakeEmbedder
from app.ingest.pipeline import ingest_table, row_ids, sync_table
from app.ingest.serializer import SPEC_TABLES
from app.ingest.store import VectorStore
from app.models.spec import SpecTable

RACKET: SpecTable = next(t for t in SPEC_TABLES if t.name == "球拍")
COLL = "racket_specs"

R1 = {"品牌": "尤尼克斯 YONEX", "型号": "天斧99", "别名": "ASTROX 99", "拍身重量(U)": "4U",
      "最高磅数": "35", "平衡点类别": "头重", "打法类型": "进攻型", "适合水平": "进阶级",
      "适合人群": "", "参考价": "1350元", "来源": "淘宝"}
R2 = {"品牌": "李宁 LINING", "型号": "战戟12", "别名": "", "拍身重量(U)": "3U",
      "最高磅数": "30", "平衡点类别": "均衡", "打法类型": "均衡型", "适合水平": "通用级",
      "适合人群": "", "参考价": "799元", "来源": "淘宝"}
R3 = {"品牌": "威克多 VICTOR", "型号": "突击9900", "别名": "THRUSTER", "拍身重量(U)": "4U",
      "最高磅数": "31", "平衡点类别": "头重", "打法类型": "进攻型", "适合水平": "进阶级",
      "适合人群": "", "参考价": "1050元", "来源": "京东"}


class CountingEmbedder:
    """记录 embedding 调用条数（增量成本断言的核心探针）。"""

    def __init__(self) -> None:
        self.inner = FakeEmbedder()
        self.n = 0

    def embed(self, texts):
        self.n += len(texts)
        return self.inner.embed(texts)


def _write(root: Path, rows: list[dict]) -> None:
    path = root / RACKET.csv_file
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(R1.keys()))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def env(tmp_path: Path):
    return VectorStore(), CountingEmbedder(), tmp_path / "processed"


# ---------- 行 id 规则 ----------


def test_row_id_shape_collection_prefix_and_no_extra_colon():
    ids = row_ids(RACKET, [R1, R2])
    for rid in ids:
        table, _, key = rid.rpartition(":")
        assert table == COLL          # retriever._fetch_record 的反查依赖此前缀形态
        assert ":" not in key         # 键内无冒号，rpartition 语义唯一
    assert len(set(ids)) == 2


def test_row_id_deterministic_and_position_independent():
    ids3 = row_ids(RACKET, [R1, R2, R3])
    assert ids3 == row_ids(RACKET, [R1, R2, R3])          # 确定性
    # 中间插行：R1/R2 的 id 不因位置变化而漂移（这正是增量只重嵌新行的根因）
    ids4 = row_ids(RACKET, [{"品牌": "新品牌 NEW", "型号": "新拍", **{k: "" for k in R1 if k not in ("品牌", "型号")}}, R1, R2, R3])
    assert set(ids3) < set(ids4)


def test_duplicate_pk_disambiguated_by_occurrence():
    dup1 = dict(R1, 别名="DUO7", 参考价="1680元")
    dup2 = dict(R1, 别名="DUO7YX", 参考价="1688元")       # 真实数据：球拍「双刃7」两行
    ids = row_ids(RACKET, [dup1, dup2])
    assert len(set(ids)) == 2 and ids[1] == ids[0] + "-2"


def test_empty_pk_row_falls_back_to_content_hash(env):
    store, emb, root = env
    nokey = replace(RACKET, primary_key=("型号",))         # 假设主键列全空
    rows = [dict(R1, 型号="")]
    _write(root, rows)
    ids = row_ids(nokey, rows)
    assert ids[0].startswith(COLL + ":")
    assert ingest_table(store, emb, nokey, root) == 1      # 不抛异常即通过


# ---------- ingest_table 与 digest ----------


def test_ingest_writes_digest_metadata(env):
    store, emb, root = env
    _write(root, [R1, R2, R3])
    ids = row_ids(RACKET, [R1, R2, R3])
    assert ingest_table(store, emb, RACKET, root) == 3
    hits = store.get(RACKET.name, ids)
    assert len(hits) == 3
    assert all(str(h["metadata"].get("digest", "")).startswith("sha256:") for h in hits)


# ---------- sync 行为 ----------


def test_sync_unchanged_is_zero_cost(env):
    store, emb, root = env
    _write(root, [R1, R2, R3])
    ingest_table(store, emb, RACKET, root)
    before = emb.n
    report = sync_table(store, emb, RACKET, root)
    assert report.embedded == 0 and report.skipped == 3 and report.deleted == []
    assert emb.n == before                                   # 一行 embedding 都没调


def test_sync_only_reembeds_changed_row(env):
    store, emb, root = env
    _write(root, [R1, R2, R3])
    ingest_table(store, emb, RACKET, root)
    before = emb.n
    _write(root, [R1, dict(R2, 参考价="1500元"), R3])        # 只改 R2 价格
    report = sync_table(store, emb, RACKET, root)
    assert (report.embedded, report.skipped, report.deleted) == (1, 2, [])
    assert emb.n - before == 1
    hit = store.get(RACKET.name, [row_ids(RACKET, [dict(R2, 参考价="1500元")])[0]])[0]
    assert hit["metadata"]["参考价"] == "1500元"


def test_sync_adds_deletes_and_migrates_legacy_ids(env):
    store, emb, root = env
    # 旧方案 id 的存量库（模拟迁移前现场：racket_specs:0/1 + 一条已删行的孤儿 :2）
    legacy = [f"{COLL}:{i}" for i in range(3)]
    store.add(RACKET.name, legacy, ["旧文档A", "旧文档B", "旧文档C"],
              [{"来源文件": "球拍"}] * 3, FakeEmbedder().embed(["a", "b", "c"]))
    _write(root, [R1, R2, R3])
    report = sync_table(store, emb, RACKET, root)
    # 全部行按新主键 id 重嵌（迁移），旧行号 id 整体判陈旧删除
    assert report.embedded == 3
    assert set(report.deleted) == set(legacy)
    assert set(store.list_ids(RACKET.name)) == set(row_ids(RACKET, [R1, R2, R3]))
    assert store.count(RACKET.name) == 3


def test_sync_row_insert_and_delete_hits_only_them(env):
    store, emb, root = env
    _write(root, [R1, R2, R3])
    ingest_table(store, emb, RACKET, root)
    before = emb.n
    _write(root, [R1, R3])                                   # 删 R2
    report = sync_table(store, emb, RACKET, root)
    assert (report.embedded, report.skipped) == (0, 2)
    assert report.deleted == [row_ids(RACKET, [R2])[0]]
    assert emb.n == before                                   # 纯删除零重嵌
    assert store.count(RACKET.name) == 2

    _write(root, [R1, R2, R3])                               # 再把 R2 加回来
    report = sync_table(store, emb, RACKET, root)
    assert (report.embedded, report.skipped, report.deleted) == (1, 2, [])
    assert emb.n - before == 1                               # 只嵌回来的这 1 行


def test_sync_pk_edit_replaces_identity(env):
    store, emb, root = env
    _write(root, [R1, R2])
    ingest_table(store, emb, RACKET, root)
    old_id = row_ids(RACKET, [R1])[0]
    _write(root, [dict(R1, 型号="天斧99PRO"), R2])           # 改主键 = 换身份
    report = sync_table(store, emb, RACKET, root)
    new_id = row_ids(RACKET, [dict(R1, 型号="天斧99PRO")])[0]
    assert (report.embedded, report.skipped) == (1, 1)       # 只嵌新行
    assert report.deleted == [old_id]                        # 旧身份被清理
    assert store.count(RACKET.name) == 2
    assert store.get(RACKET.name, [new_id])[0]["metadata"]["型号"] == "天斧99PRO"
