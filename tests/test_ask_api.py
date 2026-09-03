"""POST /ask 接口测试：依赖注入 stub 服务，不触网。"""

from fastapi.testclient import TestClient

from app.api.routes.ask import get_ask_service
from app.models.schema import AskSource
from app.rag.service import AskResult
from main import create_app


class StubService:
    def ask(self, question: str) -> AskResult:
        return AskResult(
            answer="推荐尤尼克斯天斧99",
            sources=[
                AskSource(table="racket_specs", brand="尤尼克斯 YONEX", model="天斧99")
            ],
        )


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_ask_service] = lambda: StubService()
    return TestClient(app)


def test_ask_ok():
    client = _client()
    resp = client.post("/ask", json={"question": "推荐一款4U球拍"})
    assert resp.status_code == 200
    data = resp.json()
    # 统一响应格式 {code, message, data}
    assert data["code"] == 0
    assert data["message"] == "ok"
    payload = data["data"]
    assert payload["answer"] == "推荐尤尼克斯天斧99"
    assert payload["sources"][0]["model"] == "天斧99"
    assert payload["sources"][0]["brand"] == "尤尼克斯 YONEX"


def test_ask_empty_question_422():
    client = _client()
    resp = client.post("/ask", json={"question": ""})
    assert resp.status_code == 422
    data = resp.json()
    assert data["code"] == 42201
    assert data["message"]


def test_ask_missing_body_422():
    client = _client()
    resp = client.post("/ask", json={})
    assert resp.status_code == 422
    assert resp.json()["code"] == 42201
