from fastapi import APIRouter

from app.api.routes.admin import router as admin_router
from app.api.routes.admin import users_router as admin_users_router
from app.api.routes.admin_dashboard import router as admin_dashboard_router
from app.api.routes.admin_rag import router as admin_rag_router
from app.api.routes.admin_review import router as admin_review_router
from app.api.routes.admin_system import router as admin_system_router
from app.api.routes.ask import router as ask_router
from app.api.routes.audit import router as audit_router
from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.feedback import router as feedback_router
from app.api.routes.health import router as health_router
from app.api.routes.kb import router as kb_router
from app.api.routes.user import router as user_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(ask_router)
api_router.include_router(chat_router)
api_router.include_router(feedback_router)
api_router.include_router(kb_router)
api_router.include_router(user_router)
api_router.include_router(admin_router)
api_router.include_router(admin_users_router)
api_router.include_router(admin_dashboard_router)
api_router.include_router(admin_rag_router)
api_router.include_router(admin_review_router)
api_router.include_router(admin_system_router)
api_router.include_router(audit_router)
