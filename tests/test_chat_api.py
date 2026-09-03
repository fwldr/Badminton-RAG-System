"""POST /chat 接口测试：依赖注入 stub agent，不触网。"""

from fastapi.testclient import TestClient

from app.api.routes.chat import get_agent
from main import create_app


class StubAgent:
    def invoke(self, state: dict) -> dict:
        return {
            "question": state["question"],
            "answer": "推荐李宁 GP203 红色手胶。",
            "sources": [{"table": "grip_specs", "brand": "李宁 LINING", "model": "GP203"}],
            "images": [{"url": "/uploads/docs/img_abc.png", "title": "握拍姿势.png"}],
            "clarification": None,
            "trace": [
                {"node": "route", "input": {"question": state["question"]}, "output": {"route": "equipment"}},
                {"node": "generate", "input": {}, "output": {"answer": "推荐李宁 GP203 红色手胶。"}},
            ],
        }


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_agent] = lambda: StubAgent()
    return TestClient(app)


def test_chat_ok():
    client = _client()
    resp = client.post("/chat", json={"session_id": "s1", "question": "推荐红色手胶"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["answer"] == "推荐李宁 GP203 红色手胶。"
    assert data["sources"][0]["model"] == "GP203"
    assert data["trace"][0]["node"] == "route"
    assert data["images"][0]["url"] == "/uploads/docs/img_abc.png"


def test_chat_missing_session_422():
    client = _client()
    resp = client.post("/chat", json={"question": "你好"})
    assert resp.status_code == 422


def test_chat_missing_question_422():
    client = _client()
    resp = client.post("/chat", json={"session_id": "s1"})
    assert resp.status_code == 422


def test_chat_mode_passthrough_and_wiki_trace():
    """请求级 mode 原样交给 agent（None 时由 agent 按全局 WIKI_MODE_ENABLED 归一）。"""
    captured: list[dict] = []

    class EchoAgent:
        def invoke(self, state: dict) -> dict:
            captured.append(dict(state))
            return {
                "answer": "4U 表示重量约 80-84 克。",
                "sources": [{"table": "规格常识", "brand": "拍身重量U数", "model": "4U"}],
                "trace": [{"node": "wiki", "input": {}, "output": {}}],
                "mode": state.get("mode") or "classic",
                "wiki_trace": {"targets": [{"id": "con_spec_knowledge_x_1", "origin": "llm"}]},
            }

    app = create_app()
    app.dependency_overrides[get_agent] = lambda: EchoAgent()
    client = TestClient(app)

    wiki = client.post(
        "/chat", json={"session_id": "s-mode-wiki", "question": "4U 是多重要", "mode": "wiki"}
    ).json()["data"]
    default = client.post(
        "/chat", json={"session_id": "s-mode-default", "question": "4U 是什么概念"}
    ).json()["data"]

    assert captured[0]["mode"] == "wiki"
    assert wiki["mode"] == "wiki" and wiki["wiki_trace"]["targets"][0]["origin"] == "llm"
    assert captured[1]["mode"] is None      # 未指定 → 由 agent 取全局开关
    assert default["mode"] == "classic"     # agent 归一后的实际模式回传前端
