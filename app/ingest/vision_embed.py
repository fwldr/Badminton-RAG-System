"""多模态图片 embedding 抽象：图片字节 → 向量（SiliconFlow Qwen3-VL-Embedding API）。

- `SiliconFlowVisionEmbedder`：调 SiliconFlow `POST /v1/embeddings`（OpenAI 兼容）。
  图片与文本由同一模型编码，**同空间**（默认 4096 维）；图片走 `{"image": data-URI}`、
  文本走 `{"text": ...}`，混合列表按索引对应。查询时用 `embed_text` 得与图片同空间的向量。
- `FakeVisionEmbedder`：测试用（确定性向量，不触网）。
- `build_vision_embedder(settings)`：`vision_embed_enabled=true` 且配置了
  `vision_api_key`（缺省回退 `rerank_api_key`，同属 SiliconFlow key）时构建，否则 None。

说明：改走 API 后不再依赖本地 torch/1.75GB 权重/镜像下载；无文字图片入库与文本查询
都经 SiliconFlow（按量计费），图片会发送到第三方，需接受隐私约束。
"""

from __future__ import annotations

import base64
import hashlib
import logging
import math
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "Qwen/Qwen3-VL-Embedding-8B"
_DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"


def _image_mime(data: bytes) -> str:
    """按 magic bytes 判断图片 MIME（data URI 需带正确类型）。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"


class VisionEmbedder(Protocol):
    """图片/文本 → 同空间向量（跨索引余弦可比较）。"""

    def embed_images(self, image_bytes_list: list[bytes]) -> list[list[float]]:
        ...

    def embed_text(self, text: str) -> list[float]:
        ...


class SiliconFlowVisionEmbedder:
    """SiliconFlow Qwen3-VL-Embedding-8B（多模态）。transport 供测试注入 MockTransport。"""

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        dimensions: int = 0,  # 0 = 用 API 默认（4096）
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.dimensions = dimensions
        self._client = httpx.Client(timeout=60, transport=transport)

    def _embed(self, input_) -> list[list[float]]:
        payload: dict = {"model": self.model, "input": input_, "encoding_format": "float"}
        if self.dimensions and self.dimensions > 0:
            payload["dimensions"] = self.dimensions
        resp = self._client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return [d["embedding"] for d in data if isinstance(d, dict)]

    def embed_images(self, image_bytes_list: list[bytes]) -> list[list[float]]:
        inputs = [
            {"image": f"data:{_image_mime(b)};base64," + base64.b64encode(b).decode()}
            for b in image_bytes_list
        ]
        return self._embed(inputs)

    def embed_text(self, text: str) -> list[float]:
        return self._embed(text)[0]


class FakeVisionEmbedder:
    """测试用：确定性向量（按字节 hash，相似输入得到相似向量；不触网）。"""

    def __init__(self, dim: int = 4096) -> None:
        self.dim = dim

    def _vec(self, data: bytes) -> list[float]:
        vec = [0.0] * self.dim
        for byte in data[:512]:
            idx = int(hashlib.md5(bytes([byte])).hexdigest()[:8], 16) % self.dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_images(self, image_bytes_list: list[bytes]) -> list[list[float]]:
        return [self._vec(d) for d in image_bytes_list]

    def embed_text(self, text: str) -> list[float]:
        return self._vec(text.encode("utf-8"))


def build_vision_embedder(settings) -> VisionEmbedder | None:
    """按配置构建多模态 embedding 引擎；未启用或缺 key 返回 None。"""
    if not getattr(settings, "vision_embed_enabled", False):
        return None
    api_key = (getattr(settings, "vision_api_key", "") or settings.rerank_api_key or "").strip()
    if not api_key:
        logger.warning(
            "VISION_EMBED_ENABLED=true 但缺 VISION_API_KEY（或用 RERANK_API_KEY），多模态图片索引未启用"
        )
        return None
    return SiliconFlowVisionEmbedder(
        api_key=api_key,
        model=settings.vision_embed_model or _DEFAULT_MODEL,
        base_url=settings.vision_base_url or _DEFAULT_BASE_URL,
        dimensions=getattr(settings, "vision_embed_dim", 0) or 0,
    )
