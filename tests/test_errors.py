"""统一错误码 + 全局异常处理器测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import ApiError, ErrorCode, ok, register_exception_handlers


def _app():
    app = FastAPI()

    @app.get("/ok")
    async def good():
        return ok({"x": 1})

    @app.get("/err")
    async def bad():
        raise ApiError(ErrorCode.NOT_FOUND, "没找到")

    @app.get("/boom")
    async def boom():
        raise RuntimeError("内部炸了")

    register_exception_handlers(app)
    return TestClient(app)


def test_ok_format():
    client = _app()
    resp = client.get("/ok")
    assert resp.status_code == 200
    assert resp.json() == {"code": 0, "message": "ok", "data": {"x": 1}}


def test_api_error_format_and_status():
    client = _app()
    resp = client.get("/err")
    assert resp.status_code == 404
    assert resp.json()["code"] == 40401
    assert resp.json()["message"] == "没找到"


def test_unhandled_exception_hidden():
    # raise_server_exceptions=False：让服务器端异常走全局处理器返回 500，而不是抛给测试
    app = FastAPI()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("内部炸了")

    register_exception_handlers(app)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == 50001
    assert "RuntimeError" not in body["message"]  # 不泄露堆栈


def test_http_exception_unified():
    client = _app()
    resp = client.get("/no-such-route")
    assert resp.status_code == 404
    assert resp.json()["code"] == 40401
