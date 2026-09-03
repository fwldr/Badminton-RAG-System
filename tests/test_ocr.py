"""OCR 抽象测试：工厂分支 + FakeOcrEngine（不加载真实模型、不触网）。"""

from app.ingest.ocr import FakeOcrEngine, RapidOcrEngine, build_ocr_engine
from app.core.config import Settings


def test_build_ocr_engine_none():
    assert build_ocr_engine(Settings(ocr_engine="none")) is None
    assert build_ocr_engine(Settings(ocr_engine="")) is None


def test_build_ocr_engine_rapidocr_is_lazy():
    engine = build_ocr_engine(Settings(ocr_engine="rapidocr"))
    assert isinstance(engine, RapidOcrEngine)
    # 未调用 ocr() 前不应加载模型（懒加载），构造本身零开销


def test_fake_ocr_engine_returns_fixed_text():
    engine = FakeOcrEngine("发球规则：击球点不得高于腰部")
    assert engine.ocr(b"\x89PNG") == "发球规则：击球点不得高于腰部"
