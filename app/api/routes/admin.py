"""管理后台路由：文档上传 / 列表 / 删除 / 重索引 + 用户与权限管理（RBAC）。

鉴权：文档/审计端点用 ``require_admin_access``（管理员 JWT 或旧 X-Admin-Key 任一通过，向后兼容）；
用户与权限管理用严格 ``require_admin``（必须管理员账户登录），验证真正的角色控制。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import admin_rate_limit, require_admin, require_admin_access
from app.api.errors import ApiError, ErrorCode, ok
from app.core.config import BASE_DIR, get_settings
from app.db.repos import DocRepo, UserRepo, user_to_public
from app.ingest.doc_ingest import IMAGE_EXTS, ingest_document
from app.ingest.embedder import Embedder, build_embedder
from app.ingest.ocr import OcrEngine, build_ocr_engine
from app.ingest.store import VectorStore
from app.ingest.vision_embed import VisionEmbedder, build_vision_embedder

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_access), Depends(admin_rate_limit())],
)

# txt/md/csv + pdf + 图片（OCR/多模态入库）
ALLOWED_EXTS = {"txt", "md", "csv", "pdf"} | IMAGE_EXTS
UPLOAD_DIR = BASE_DIR / "data" / "uploads"


def _upload_path(doc_id: int, filename: str) -> Path:
    """原文件持久化路径（重索引用）。"""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    return UPLOAD_DIR / f"doc_{doc_id}.{ext}"


def _store_and_embedder() -> tuple[VectorStore, Embedder]:
    """管理接口用真实库（data/chroma + 百炼 embedding）。

    独立函数便于测试注入替换（见 tests/test_admin.py 的 dependency_overrides）。
    """
    settings = get_settings()
    store = VectorStore(persist_dir=settings.chroma_dir)
    return store, build_embedder(settings)


def get_store_embedder() -> tuple[VectorStore, Embedder]:
    """FastAPI 依赖：上传/重索引路由共用；测试可 override。"""
    return _store_and_embedder()


def get_ocr_engine() -> OcrEngine | None:
    """OCR 引擎依赖（图片入库用）；测试可 override 为 FakeOcrEngine/None。"""
    return build_ocr_engine(get_settings())


def get_vision_embedder() -> VisionEmbedder | None:
    """多模态图片 embedding 依赖（无文字图片入库用）；测试可 override。"""
    return build_vision_embedder(get_settings())


@router.post("/documents", summary="上传文档（txt/md/csv/pdf/图片）并入库")
async def upload_document(
    file: UploadFile = File(...),
    se: tuple[VectorStore, Embedder] = Depends(get_store_embedder),
    ocr: OcrEngine | None = Depends(get_ocr_engine),
    vision_embed: VisionEmbedder | None = Depends(get_vision_embedder),
) -> dict:
    """上传文档 → 保存原文件 → 建记录 parsing → 同步入库 → ready/failed。

    说明：demo 用同步解析（≤20MB 文档耗时秒级）；生产可换后台任务队列（Celery/Arq）。
    """
    filename = file.filename or "unnamed"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTS:
        raise ApiError(ErrorCode.VALIDATION, f"仅支持 {sorted(ALLOWED_EXTS)} 格式")

    max_size = get_settings().upload_max_size
    file_bytes = await file.read()
    if len(file_bytes) > max_size:
        raise ApiError(ErrorCode.VALIDATION, f"文件大小不能超过 {max_size // (1024 * 1024)}MB")
    if not file_bytes:
        raise ApiError(ErrorCode.VALIDATION, "文件内容为空")

    store, embedder = se
    settings = get_settings()
    doc_id = DocRepo.create(filename=filename, doc_type=ext)
    _upload_path(doc_id, filename).write_bytes(file_bytes)  # 持久化原文件
    status, chunk_count, error_msg = ingest_document(
        file_bytes, filename, doc_id, store, embedder,
        ocr=ocr, vision_embed=vision_embed,
        chunk_size=settings.doc_chunk_size,
        chunk_overlap=settings.doc_chunk_overlap,
        ocr_min_chars=settings.ocr_min_chars,
        image_dir=settings.doc_images_dir,
    )
    DocRepo.update_status(doc_id, status, chunk_count, error_msg)
    return ok({"id": doc_id, "filename": filename, "status": status, "chunk_count": chunk_count})


@router.get("/documents", summary="文档列表（状态/版本/块数）")
async def list_documents() -> dict:
    return ok({"documents": DocRepo.list_all()})


@router.delete("/documents/{doc_id}", summary="删除文档（记录 + collection + 原文件）")
async def delete_document(
    doc_id: int,
    se: tuple[VectorStore, Embedder] = Depends(get_store_embedder),
) -> dict:
    doc = DocRepo.get(doc_id)
    if not doc:
        raise ApiError(ErrorCode.NOT_FOUND, "文档不存在")
    store, _ = se
    store.delete_collection(f"doc_{doc_id}")
    # 删除原文件（存在才删）
    for p in UPLOAD_DIR.glob(f"doc_{doc_id}.*"):
        try:
            p.unlink()
        except OSError:
            logger.warning("删除原文件失败: %s", p)
    DocRepo.delete(doc_id)
    return ok({"id": doc_id})


@router.post("/documents/{doc_id}/reindex", summary="重索引（版本 +1）")
async def reindex_document(
    doc_id: int,
    se: tuple[VectorStore, Embedder] = Depends(get_store_embedder),
    ocr: OcrEngine | None = Depends(get_ocr_engine),
    vision_embed: VisionEmbedder | None = Depends(get_vision_embedder),
) -> dict:
    doc = DocRepo.get(doc_id)
    if not doc:
        raise ApiError(ErrorCode.NOT_FOUND, "文档不存在")

    # 从持久化的原文件重新解析入库（版本 +1）
    path = _upload_path(doc_id, doc["filename"])
    if not path.exists():
        raise ApiError(ErrorCode.CONFLICT, "原文件已丢失，请重新上传")

    store, embedder = se
    settings = get_settings()
    file_bytes = path.read_bytes()
    status, chunk_count, error_msg = ingest_document(
        file_bytes, doc["filename"], doc_id, store, embedder,
        ocr=ocr, vision_embed=vision_embed,
        chunk_size=settings.doc_chunk_size,
        chunk_overlap=settings.doc_chunk_overlap,
        ocr_min_chars=settings.ocr_min_chars,
        image_dir=settings.doc_images_dir,
    )
    version = DocRepo.bump_version(doc_id)
    DocRepo.update_status(doc_id, status, chunk_count, error_msg)
    if status != "ready":
        raise ApiError(ErrorCode.INTERNAL, f"重索引失败：{error_msg}")
    return ok({"id": doc_id, "version": version, "chunk_count": chunk_count})


class UserPatch(BaseModel):
    """PATCH /admin/users/{id} 请求体：改角色 / 启停 / 模块级权限。"""

    role: Literal["user", "admin"] | None = None
    is_active: bool | None = None
    permissions: list[str] | None = None  # None=不改；[]=清空；非空=仅授予这些模块


# 用户与权限管理走严格管理员账户鉴权（require_admin，JWT role=admin），
# 与文档管理端点的 require_admin_access（兼容旧 X-Admin-Key）区分开：
# 只有登录的管理员账户能管理用户，共享密钥不再有该权限。
users_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(admin_rate_limit())],
)


@users_router.get("/users", summary="用户列表（管理员账户）", dependencies=[Depends(require_admin)])
async def list_users(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """用户与权限管理：列出所有账户与角色（严格管理员 RBAC 示例）。"""
    return ok({"total": UserRepo.count(), "users": UserRepo.list_all(limit=limit, offset=offset)})


@users_router.patch("/users/{user_id}", summary="修改用户角色/状态/模块权限（管理员账户）",
                    dependencies=[Depends(require_admin)])
async def patch_user(user_id: int, body: UserPatch) -> dict:
    """分配角色（user/admin）、禁用账号、或授予模块级权限（对应设计文档「角色权限分配」）。"""
    if not UserRepo.get_by_id(user_id):
        raise ApiError(ErrorCode.NOT_FOUND, "用户不存在")
    if body.role is not None:
        UserRepo.set_role(user_id, body.role)
    if body.is_active is not None:
        UserRepo.set_active(user_id, body.is_active)
    if body.permissions is not None:
        UserRepo.set_permissions(user_id, json.dumps(body.permissions, ensure_ascii=False))
    return ok(user_to_public(UserRepo.get_by_id(user_id)))


class DocTagsPatch(BaseModel):
    """PATCH /admin/documents/{id}/tags：元数据打标（子集更新）。"""

    tags: list[str] = Field(default_factory=list, max_length=20)


@router.patch("/documents/{doc_id}/tags", summary="文档元数据打标（同步向量库 metadata）")
async def patch_doc_tags(
    doc_id: int,
    body: DocTagsPatch,
    se: tuple[VectorStore, Embedder] = Depends(get_store_embedder),
) -> dict:
    """给文档批量打标（如 规则类,2024赛事）；写入 documents.tags 并同步到 Chroma 记录。"""
    doc = DocRepo.get(doc_id)
    if not doc:
        raise ApiError(ErrorCode.NOT_FOUND, "文档不存在")
    tags = [t.strip() for t in body.tags if t.strip()]
    tags_str = ",".join(tags)
    DocRepo.set_tags(doc_id, tags_str)
    # 同步向量库 metadata（doc_{id} collection 全部记录；无 collection 时静默跳过）
    store, _ = se
    try:
        ids = [h["id"] for h in store.get_all(f"doc_{doc_id}")]
        if ids:
            store.update_metadata(f"doc_{doc_id}", ids, {"tags": tags_str})
    except Exception:
        logger.warning("标签同步向量库失败（仅写入数据库）：doc_%s", doc_id, exc_info=True)
    return ok({"id": doc_id, "tags": tags})
