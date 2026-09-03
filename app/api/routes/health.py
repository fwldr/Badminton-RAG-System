from fastapi import APIRouter

from app.api.errors import ok

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """存活探针。"""
    return ok({"status": "ok", "app": "badminton-rag"})
