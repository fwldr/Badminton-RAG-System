"""图片入库测试：OCR 文本链路（索引①）+ 多模态向量链路（索引②）离线验证。

用 FakeOcrEngine / FakeVisionEmbedder 注入，不加载真实模型、不触网。
"""

from app.ingest.doc_ingest import ingest_document
from app.ingest.embedder import FakeEmbedder
from app.ingest.ocr import FakeOcrEngine
from app.ingest.store import VectorStore
from app.ingest.vision_embed import FakeVisionEmbedder

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def test_ingest_image_with_ocr_text_ok():
    store = VectorStore()
    embedder = FakeEmbedder()
    ocr = FakeOcrEngine("球拍握把尺寸：G4 适合中等手型，G5 适合偏小手型")
    status, count, err = ingest_document(
        PNG_BYTES, "握把尺寸说明.png", 1, store, embedder, ocr=ocr
    )
    assert status == "ready"
    assert count >= 1
    hits = store.get_all("doc_1")
    assert hits
    meta = hits[0]["metadata"]
    assert meta["source_type"] == "image"
    assert "ocr_text" in meta
    assert meta["file_hash"]


def test_ingest_image_without_ocr_engine_failed():
    store = VectorStore()
    status, count, err = ingest_document(
        PNG_BYTES, "a.png", 1, store, FakeEmbedder(), ocr=None
    )
    assert status == "failed"
    assert "OCR" in (err or "")


def test_ingest_image_no_text_failed():
    """OCR 文本低于阈值（无文字图）→ failed，提示交视觉索引。"""
    store = VectorStore()
    status, count, err = ingest_document(
        PNG_BYTES, "b.png", 1, store, FakeEmbedder(),
        ocr=FakeOcrEngine(""),  # 识别不出文字
        ocr_min_chars=20,
    )
    assert status == "failed"
    assert "无文字" in (err or "")


def test_ingest_image_ocr_min_chars_threshold():
    """OCR 文本过短（< 阈值）视为无文字。"""
    store = VectorStore()
    status, count, err = ingest_document(
        PNG_BYTES, "c.png", 1, store, FakeEmbedder(),
        ocr=FakeOcrEngine("短"), ocr_min_chars=20,
    )
    assert status == "failed"


def test_ingest_no_text_image_with_vision_embed_ok():
    """无文字图片 + 多模态引擎 → img_{hash} collection（视觉向量，SiliconFlow 默认 4096 维）。"""
    store = VectorStore()
    status, count, err = ingest_document(
        PNG_BYTES, "动作分解图.png", 1, store, FakeEmbedder(),
        ocr=FakeOcrEngine(""),  # 无文字
        vision_embed=FakeVisionEmbedder(dim=4096),
        ocr_min_chars=20,
    )
    assert status == "ready"
    assert count == 1
    img_colls = [c for c in store.list_collections() if c.startswith("img_")]
    assert len(img_colls) == 1
    hits = store.get_all(img_colls[0])
    assert len(hits) == 1
    meta = hits[0]["metadata"]
    assert meta["source_type"] == "image"
    assert meta["embed_dim"] == 4096
    assert "动作分解图" in hits[0]["document"]


def test_ingest_no_text_image_without_vision_failed():
    """无文字图片且无多模态引擎 → failed 并提示开启 VISION_EMBED_ENABLED。"""
    store = VectorStore()
    status, count, err = ingest_document(
        PNG_BYTES, "d.png", 1, store, FakeEmbedder(),
        ocr=FakeOcrEngine(""), ocr_min_chars=20,
    )
    assert status == "failed"
    assert "VISION_EMBED_ENABLED" in (err or "")


def test_ingest_image_saves_public_copy_for_vision(tmp_path):
    """无文字图片 + image_dir → 副本落盘 + metadata「图片URL」（聊天展示链接）。"""
    store = VectorStore()
    status, count, err = ingest_document(
        PNG_BYTES, "动作分解图.png", 1, store, FakeEmbedder(),
        ocr=FakeOcrEngine(""), vision_embed=FakeVisionEmbedder(dim=4096),
        ocr_min_chars=20, image_dir=tmp_path,
    )
    assert status == "ready"
    img_colls = [c for c in store.list_collections() if c.startswith("img_")]
    meta = store.get_all(img_colls[0])[0]["metadata"]
    assert meta["图片URL"].startswith("/uploads/docs/")
    assert (tmp_path / meta["图片URL"].rsplit("/", 1)[-1]).exists()


def test_ingest_image_saves_public_copy_for_ocr_text(tmp_path):
    """有文字图片 + image_dir → OCR 文本分支同样落盘副本 + 图片URL。"""
    store = VectorStore()
    status, count, err = ingest_document(
        PNG_BYTES, "握拍说明.png", 1, store, FakeEmbedder(),
        ocr=FakeOcrEngine("球拍握把尺寸：G4 适合中等手型，G5 适合偏小手型"),
        ocr_min_chars=20, image_dir=tmp_path,
    )
    assert status == "ready"
    meta = store.get_all("doc_1")[0]["metadata"]
    assert meta["图片URL"].startswith("/uploads/docs/")
    assert (tmp_path / meta["图片URL"].rsplit("/", 1)[-1]).exists()


def test_ingest_image_without_image_dir_no_url():
    """不传 image_dir（旧行为）→ 不落盘、metadata 无图片URL。"""
    store = VectorStore()
    status, count, err = ingest_document(
        PNG_BYTES, "b.png", 1, store, FakeEmbedder(),
        ocr=FakeOcrEngine(""), vision_embed=FakeVisionEmbedder(dim=4096), ocr_min_chars=20,
    )
    assert status == "ready"
    img_colls = [c for c in store.list_collections() if c.startswith("img_")]
    assert "图片URL" not in store.get_all(img_colls[0])[0]["metadata"]
