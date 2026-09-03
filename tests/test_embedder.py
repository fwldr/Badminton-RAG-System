"""embedder 单元测试：DashScopeEmbedder 请求形状/分批/顺序 / FakeEmbedder 确定性。"""

import json

import httpx
import pytest

from app.ingest.embedder import _MAX_BATCH, DashScopeEmbedder, FakeEmbedder, build_embedder


def _resp(batch_size: int, offset: int = 0) -> httpx.Response:
    """构造 OpenAI 兼容 embeddings 响应：向量值编码了全局序号，便于校验顺序。"""
    data = [
        {"index": offset + i, "embedding": [float(offset + i), 0.5]} for i in range(batch_size)
    ]
    return httpx.Response(200, json={"data": data})


def test_embedder_correct_endpoint_payload_and_auth():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return _resp(2)

    embedder = DashScopeEmbedder(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "sk-test",
        "qwen3.7-text-embedding",
        transport=httpx.MockTransport(handler),
    )
    out = embedder.embed(["羽毛球", "球拍"])
    assert captured["url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["payload"]["model"] == "qwen3.7-text-embedding"
    assert captured["payload"]["input"] == ["羽毛球", "球拍"]
    assert out == [[0.0, 0.5], [1.0, 0.5]]


def test_embedder_splits_input_into_batches_and_keeps_order():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload["input"])
        return _resp(len(payload["input"]), offset=sum(len(c) for c in calls[:-1]))

    embedder = DashScopeEmbedder("http://x/v1", "sk-test", transport=httpx.MockTransport(handler))
    texts = [f"文本{i}" for i in range(_MAX_BATCH * 2 + 3)]
    out = embedder.embed(texts)
    assert [len(c) for c in calls] == [_MAX_BATCH, _MAX_BATCH, 3]
    assert out == [[float(i), 0.5] for i in range(len(texts))]


def test_embedder_restores_order_from_response_index():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [
                {"index": 1, "embedding": [1.0]},
                {"index": 0, "embedding": [0.0]},
            ]},
        )

    embedder = DashScopeEmbedder("http://x/v1", "sk-test", transport=httpx.MockTransport(handler))
    assert embedder.embed(["a", "b"]) == [[0.0], [1.0]]


def test_embedder_raises_on_count_mismatch():
    def handler(request: httpx.Request) -> httpx.Response:
        return _resp(1)

    embedder = DashScopeEmbedder("http://x/v1", "sk-test", transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError):
        embedder.embed(["文本一", "文本二"])


def test_embedder_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    embedder = DashScopeEmbedder("http://x/v1", "sk-test", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        embedder.embed(["文本"])


def test_build_embedder_reuses_llm_credentials():
    from app.core.config import Settings

    s = Settings(llm_api_key="sk-test", embedding_model="qwen3.7-text-embedding")
    embedder = build_embedder(s)
    assert embedder.model == "qwen3.7-text-embedding"
    assert embedder.base_url == s.llm_base_url.rstrip("/")


def test_fake_embedder_deterministic_and_normalized():
    embedder = FakeEmbedder(dim=64)
    a = embedder.embed(["天斧99 重量4U"])
    b = embedder.embed(["天斧99 重量4U"])
    assert a == b
    assert len(a[0]) == 64
    norm = sum(x * x for x in a[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-6
