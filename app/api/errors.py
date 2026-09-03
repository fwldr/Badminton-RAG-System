"""统一错误码 + 全局异常处理器：所有响应统一为 {"code", "message", "data"}。

- code=0 表示成功（HTTP 200）；
- 业务错误用 ApiError 抛出，code 为业务码（如 42901），HTTP 状态由 code 前三位映射；
- 未捕获异常由全局处理器兜底为 50001，不向客户端泄露堆栈。
"""

from __future__ import annotations

from enum import IntEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorCode(IntEnum):
    """业务错误码：前三位 = HTTP 状态码，后两位 = 细分。"""

    OK = 0
    UNAUTHORIZED = 40101
    FORBIDDEN = 40301
    NOT_FOUND = 40401
    VALIDATION = 42201
    CONFLICT = 40901
    RATE_LIMITED = 42901
    INTERNAL = 50001


def http_status(code: ErrorCode) -> int:
    """业务码 → HTTP 状态码（前三位）。"""
    return int(code) // 100


class ApiError(Exception):
    """业务异常：携带业务码与用户可读信息。"""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def ok(data: object = None) -> dict:
    """成功响应体。"""
    return {"code": int(ErrorCode.OK), "message": "ok", "data": data}


def _error_body(code: ErrorCode, message: str) -> dict:
    return {"code": int(code), "message": message, "data": None}


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器：ApiError / 校验错误 / 未捕获异常。"""

    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=http_status(exc.code),
            content=_error_body(exc.code, exc.message),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # 统一 404/405 等标准 HTTP 异常的响应格式
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.INTERNAL, str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 提取第一个错误的定位，给出可读信息
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        msg = first.get("msg", "参数校验失败")
        return JSONResponse(
            status_code=http_status(ErrorCode.VALIDATION),
            content=_error_body(ErrorCode.VALIDATION, f"{loc} {msg}".strip() or "参数校验失败"),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        # 兜底：不向客户端泄露堆栈
        return JSONResponse(
            status_code=http_status(ErrorCode.INTERNAL),
            content=_error_body(ErrorCode.INTERNAL, "服务器内部错误"),
        )
