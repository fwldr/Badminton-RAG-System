"""GET /kb/overview 测试：内存 VectorStore 注入 + processed 目录扫描 + 降级路径（不触网）。"""

from fastapi.testclient import TestClient

from app.api.routes.kb import get_kb_store, scan_processed
from app.ingest.store import VectorStore
from main import create_app


class ExplodingStore:
    """模拟 chroma 不可用：list_collections 抛错，应降级为空表清单。"""

    def list_collections(self) -> list[str]:
        raise RuntimeError("chroma down")

    def count(self, table: str) -> int:
        raise RuntimeError("chroma down")


def _client(store) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_kb_store] = lambda: store
    return TestClient(app)


def _mem_store_with_data() -> VectorStore:
    store = VectorStore()  # 内存版（EphemeralClient，进程内隔离）
    store.add("racket_specs", ["r1", "r2"], ["记录1", "记录2"], [{"品牌": "A"}, {"品牌": "B"}], [[0.1] * 4, [0.2] * 4])
    store.add("hand_techniques", ["k1"], ["记录3"], [{"来源": "X"}], [[0.3] * 4])
    return store


def test_kb_overview_counts_and_files(monkeypatch, tmp_path):
    """正常路径：chroma 计数 + processed 目录文件扫描。"""
    # 造一个 processed 目录：2 张规格表 csv + 2 个 knowledge csv
    kdir = tmp_path / "knowledge"
    kdir.mkdir()
    (tmp_path / "球拍.csv").write_text("a\n", encoding="utf-8")
    (tmp_path / "手胶.csv").write_text("a\n", encoding="utf-8")
    (kdir / "手法技术.csv").write_text("a\n", encoding="utf-8")
    (kdir / "BWF官方规则.csv").write_text("a\n", encoding="utf-8")

    store = _mem_store_with_data()
    monkeypatch.setattr("app.api.routes.kb.get_settings", lambda: type(
        "S", (), {"processed_data_dir": tmp_path})())
    client = _client(store)

    resp = client.get("/kb/overview")
    assert resp.status_code == 200
    data = resp.json()["data"]
    # chroma 侧：2 个 collection（英文存储名 → 中文展示名），chunk 数正确
    assert {t["table"]: t["chunks"] for t in data["tables"]} == {
        "手法技术": 1,
        "球拍": 2,
    }
    assert data["total_chunks"] == 3
    # processed 侧：规格表只列出存在 csv 的（SPEC_TABLES 里 5 张，这里只建了 2 张）
    assert data["spec_tables"] == ["球拍", "手胶"]
    assert data["knowledge_files"] == ["BWF官方规则", "手法技术"]


def test_kb_overview_chroma_down_degrades(monkeypatch, tmp_path):
    """降级路径：chroma 抛错 → tables 为空清单，但 processed 文件统计仍可用。"""
    kdir = tmp_path / "knowledge"
    kdir.mkdir()
    (tmp_path / "球拍.csv").write_text("a\n", encoding="utf-8")
    (kdir / "战术.csv").write_text("a\n", encoding="utf-8")

    monkeypatch.setattr("app.api.routes.kb.get_settings", lambda: type(
        "S", (), {"processed_data_dir": tmp_path})())
    client = _client(ExplodingStore())

    resp = client.get("/kb/overview")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tables"] == []
    assert data["total_chunks"] == 0
    assert data["spec_tables"] == ["球拍"]
    assert data["knowledge_files"] == ["战术"]


def test_scan_processed_missing_dir(tmp_path):
    """目录不存在 → 空清单，不抛错。"""
    result = scan_processed(tmp_path / "not-exists")
    assert result == {"spec_tables": [], "knowledge_files": []}
