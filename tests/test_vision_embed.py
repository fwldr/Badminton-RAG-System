"""多模态 embedding 抽象测试：SiliconFlow 工厂 + 离线 mock 请求解析 + Fake（不触网）。"""

import json

import httpx

from app.core.config import Settings
from app.ingest.vision_embed import (
    FakeVisionEmbedder,
    SiliconFlowVisionEmbedder,
    _image_mime,
    build_vision_embedder,
)


def test_build_vision_embedder_disabled():
    assert build_vision_embedder(Settings(vision_embed_enabled=False)) is None


def test_build_vision_embedder_enabled_without_key_none():
    # 显式清空 key（.env 若有 RERANK_API_KEY 会被注入，需覆盖）
    assert (
        build_vision_embedder(
            Settings(vision_embed_enabled=True, rerank_api_key=None, vision_api_key=None)
        )
        is None
    )


def test_build_vision_embedder_uses_rerank_key_fallback():
    engine = build_vision_embedder(
        # 显式清空 vision_api_key：.env 若配置了 VISION_API_KEY 会注入，掩盖回退逻辑
        Settings(vision_embed_enabled=True, rerank_api_key="sk-rerank", vision_api_key=None)
    )
    assert isinstance(engine, SiliconFlowVisionEmbedder)
    assert engine.api_key == "sk-rerank"  # 回退到 rerank_api_key（同为 SiliconFlow key）


def test_siliconflow_embed_requests_and_parses():
    """离线：MockTransport 捕获请求、固定响应，验证格式与解析。"""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(
            {
                "auth": request.headers.get("Authorization"),
                "path": str(request.url),
                "input": payload["input"],
                "model": payload["model"],
            }
        )
        emb = [0.1] * 8 if isinstance(payload["input"], list) else [0.9] * 8
        return httpx.Response(
            200,
            json={"object": "list", "data": [{"object": "embedding", "embedding": emb, "index": 0}]},
        )

    engine = SiliconFlowVisionEmbedder(
        api_key="sk-test",
        base_url="https://api.siliconflow.cn/v1",
        transport=httpx.MockTransport(handler),
    )
    img = b"\x89PNG\r\n\x1a\n" + b"0" * 16
    img_vec = engine.embed_images([img])[0]
    txt_vec = engine.embed_text("反手发球动作图解")

    assert len(calls) == 2
    img_call, txt_call = calls[0], calls[1]
    assert img_call["auth"] == "Bearer sk-test"
    assert img_call["path"].endswith("/embeddings")
    assert img_call["model"] == "Qwen/Qwen3-VL-Embedding-8B"
    # 图片输入为 data URI 列表；文本输入为原始字符串
    assert isinstance(img_call["input"], list)
    assert img_call["input"][0]["image"].startswith("data:image/png;base64,")
    assert txt_call["input"] == "反手发球动作图解"
    assert len(img_vec) == 8
    assert len(txt_vec) == 8


def test_siliconflow_mime_detection():
    assert _image_mime(b"\x89PNG\r\n\x1a\n" + b"x") == "image/png"
    assert _image_mime(b"\xff\xd8\xff" + b"x") == "image/jpeg"


def test_fake_vision_embedder_deterministic_with_embed_text():
    engine = FakeVisionEmbedder(dim=64)
    va = engine.embed_images([b"AAA"])[0]
    vb = engine.embed_images([b"AAA"])[0]
    vc = engine.embed_images([b"BBB"])[0]
    assert len(va) == 64
    assert va == vb
    assert va != vc
    assert len(engine.embed_text("反手发球动作图解")) == 64
