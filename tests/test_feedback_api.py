"""POST /feedback 接口测试：落库 / rating 校验 / 点踩收集（独立临时库，不触网）。"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.database import reset_db
from app.db.repos import FeedbackRepo
from main import create_app


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "db_path", tmp_path / "test.db")
    reset_db()
    yield
    reset_db()


def _client() -> TestClient:
    return TestClient(create_app())


def test_feedback_thumbs_up_persisted():
    client = _client()
    resp = client.post("/feedback", json={"session_id": "s1", "question": "推荐红色手胶", "rating": 1})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] >= 1
    assert FeedbackRepo.count() == 1
    row = FeedbackRepo.query()[0]
    assert row["question"] == "推荐红色手胶"
    assert row["rating"] == 1


def test_feedback_thumbs_down_with_comment():
    client = _client()
    resp = client.post(
        "/feedback",
        json={
            "session_id": "s2",
            "question": "双打怎么打",
            "answer": "回答内容",
            "rating": -1,
            "comment": "答非所问",
            "trace_id": "abc123",
        },
    )
    assert resp.status_code == 200
    row = FeedbackRepo.query()[0]
    assert row["rating"] == -1
    assert row["comment"] == "答非所问"
    assert row["trace_id"] == "abc123"


def test_feedback_invalid_rating_422():
    client = _client()
    resp = client.post("/feedback", json={"question": "q", "rating": 0})
    assert resp.status_code == 422
    assert FeedbackRepo.count() == 0


def test_feedback_missing_question_422():
    client = _client()
    resp = client.post("/feedback", json={"rating": 1})
    assert resp.status_code == 422
    assert FeedbackRepo.count() == 0
