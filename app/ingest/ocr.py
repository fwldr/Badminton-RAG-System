"""OCR 抽象：图片字节 → 文字。

- `RapidOcrEngine`：RapidOCR（PP-OCR 系列模型，onnxruntime，CPU 离线推理，模型随包分发或首次自动下载 ~15MB）；
- `FakeOcrEngine`：测试用（返回固定文本，不加载模型不触网）；
- `build_ocr_engine(settings)`：按配置构建，`ocr_engine="none"` 或缺配置时返回 None
  （图片无 OCR 引擎 → 入库失败并提示，或交视觉索引）。
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class OcrEngine(Protocol):
    """图片字节 → 识别文字（无文字返回空串）。"""

    def ocr(self, image_bytes: bytes) -> str:
        ...


class RapidOcrEngine:
    """RapidOCR 本地推理（懒加载：首次调用才加载 onnx 模型）。"""

    def __init__(self) -> None:
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def ocr(self, image_bytes: bytes) -> str:
        engine = self._get_engine()
        result, _ = engine(image_bytes)
        if not result:
            return ""
        # result 形如 [[box, text, score], ...]
        lines = [
            str(item[1]).strip()
            for item in result
            if item and len(item) > 1 and item[1]
        ]
        return "\n".join(lines)


class FakeOcrEngine:
    """测试用：返回固定文本（不加载模型、不触网）。"""

    def __init__(self, text: str = "测试识别文本") -> None:
        self.text = text

    def ocr(self, image_bytes: bytes) -> str:
        return self.text


def build_ocr_engine(settings) -> OcrEngine | None:
    """按 settings.ocr_engine 构建 OCR 引擎；'none' 或未配置时返回 None。"""
    engine_name = (settings.ocr_engine or "none").lower()
    if engine_name == "rapidocr":
        return RapidOcrEngine()
    return None
