import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.errors import register_exception_handlers
from app.api.routes import api_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.security import hash_password
from app.db.database import init_db
from app.db.repos import UserRepo

logger = logging.getLogger(__name__)

tags_metadata = [
    {"name": "health", "description": "存活探针"},
    {"name": "auth", "description": "账户认证：注册 / 登录 / 当前用户（双角色 user/admin）"},
    {"name": "ask", "description": "羽毛球装备/知识问答（RAG）"},
    {"name": "admin", "description": "管理后台：文档上传/删除/重索引 + 用户与权限管理（需管理员）"},
    {"name": "audit", "description": "审计日志查询/导出（需管理员）"},
]


def _seed_bootstrap_admin() -> None:
    """若配置了种子管理员且系统中尚不存在，则创建（供开箱即用登录管理端）。"""
    settings = get_settings()
    username = (settings.bootstrap_admin_username or "").strip()
    password = (settings.bootstrap_admin_password or "").strip()
    if not (username and password):
        return
    if UserRepo.get_by_username(username) is not None:
        return
    UserRepo.create(username, hash_password(password), role="admin", nickname="管理员")
    logger.info("已创建种子管理员账号: %s", username)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("应用启动: %s v%s", settings.app_name, settings.version)
    init_db()
    _seed_bootstrap_admin()
    logger.info("业务数据库已初始化（DB_BACKEND=%s）", settings.db_backend)
    yield
    # 关闭前刷出 Langfuse 队列（若启用），避免进程退出丢 trace
    from app.api.routes.chat import flush_tracer

    flush_tracer()
    logger.info("应用关闭")


def create_app() -> FastAPI:
    """应用工厂：初始化日志、配置、异常处理器并挂载路由。"""
    setup_logging()
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        debug=settings.debug,
        lifespan=lifespan,
        openapi_tags=tags_metadata,
    )
    register_exception_handlers(app)
    # 文档图片公开副本（入库时复制，聊天回答内联展示；先注册更长的前缀避免被 /uploads 吞掉）
    settings.doc_images_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/uploads/docs",
        StaticFiles(directory=str(settings.doc_images_dir), check_dir=False),
        name="uploads-docs",
    )
    # 用户动态配图静态服务（只暴露 posts 子目录；原始文档不对外）
    settings.user_uploads_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/uploads",
        StaticFiles(directory=str(settings.user_uploads_dir), check_dir=False),
        name="uploads",
    )
    # CORS（前端 dev 跨域；CORS_ORIGINS 为空时不挂，生产同域 nginx 反代无需）
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(api_router)
    return app


app = create_app()
