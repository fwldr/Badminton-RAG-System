from fastapi.testclient import TestClient

from main import create_app


def test_health() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["status"] == "ok"
