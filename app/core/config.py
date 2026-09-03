from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（app/core/config.py 向上三层）
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """全局配置。优先读取环境变量，其次项目根目录的 .env 文件。"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "badminton-rag"
    version: str = "0.1.0"
    debug: bool = False

    # 数据目录
    raw_data_dir: Path = BASE_DIR / "data" / "raw"
    processed_data_dir: Path = BASE_DIR / "data" / "processed"

    # 日志
    log_level: str = "INFO"
    log_dir: Path = BASE_DIR / "logs"

    # 数据库后端："mysql"（默认，业务库跑在下方 MySQL 连接配置上）
    # "sqlite" 仅供离线测试使用——tests/conftest.py 会强制 DB_BACKEND=sqlite 并把
    # db_path 指到每个测试自己的临时文件，保证测试既不触网也绝不写入真实业务库。
    db_backend: str = "mysql"
    # 仅 sqlite 后端使用（运行时走 MySQL 时不读这个路径）
    db_path: Path = BASE_DIR / "data" / "app.db"
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "badminton"
    mysql_password: str = ""
    mysql_db: str = "badminton"
    mysql_charset: str = "utf8mb4"

    # 管理接口鉴权（X-Admin-Key 头；保留用于向后兼容，管理端优先走管理员 JWT）
    admin_api_key: str | None = None

    # 双角色账户体系（RBAC）：签名令牌 + 种子管理员
    auth_token_secret: str = "badminton-rag-dev-token-secret"  # 生产环境必须用 AUTH_TOKEN_SECRET 覆盖
    auth_token_ttl: int = 604800                                # 令牌有效期（秒，默认 7 天）
    bootstrap_admin_username: str | None = None                 # 启动时种子管理员用户名（可选）
    bootstrap_admin_password: str | None = None                 # 启动时种子管理员密码（可选）

    # 微信小程序登录（code2session；未配置时 /auth/wechat 返回 500）
    wx_appid: str = ""
    wx_secret: str = ""
    # 微信订阅消息模板 id（纠错审核结果通知；留空=不发送）
    wx_subscribe_template_id: str = ""

    # API 限流（令牌桶；生产换 Redis）
    rate_limit_ask_capacity: int = 30        # 普通 /ask：30 次/分钟/IP
    rate_limit_ask_refill: float = 0.5       # 每秒回填 0.5 个令牌（≈30/60s）
    rate_limit_admin_capacity: int = 120     # 管理接口：120 次/分钟/IP
    rate_limit_admin_refill: float = 2.0     # 每秒回填 2 个令牌

    # 向量库（Chroma 持久化目录）
    chroma_dir: Path = BASE_DIR / "data" / "chroma"

    # ---- 阿里云百炼 DashScope（OpenAI 兼容协议）：生成 LLM 与文本 embedding 共用 key/base_url ----
    # embedding 客户端 POST {llm_base_url}/embeddings，生成客户端 POST {llm_base_url}/chat/completions
    llm_api_key: str | None = None
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen3.8-flash"
    # 入库与查询必须同一 embedding 模型（换模型 = 换向量空间 = 必须全量重索引）
    embedding_model: str = "qwen3.7-text-embedding"

    # SiliconFlow BGE-Reranker 精排（ask_use_rerank 默认关闭，不接入时链路不变）
    rerank_api_key: str | None = None
    rerank_base_url: str = "https://api.siliconflow.cn/v1/rerank"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    ask_use_rerank: bool = False

    # 流程参数
    ingest_batch_size: int = 16
    ask_vector_top_k: int = 10
    ask_filter_top_k: int = 5

    # ---- LLM Wiki 模式（badminton-rag-llm-wiki-plan.md）----
    wiki_mode_enabled: bool = True                     # 总开关；false 时问答链路逐字不变
    wiki_max_steps: int = 3                              # 导航循环步数上限（防 token/延迟失控）
    wiki_dir: Path = BASE_DIR / "data" / "wiki"          # 派生条目页 + manifest + toc

    # Langfuse 可观测（默认关闭；开启需配置 key，缺 key 自动降级 NullTracer）
    langfuse_enabled: bool = False
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL"),
        description="Langfuse 服务地址（兼容 Langfuse 官方环境变量 LANGFUSE_BASE_URL）",
    )
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    # 常见问题缓存（LRU + TTL；仅无历史会话命中）
    faq_cache_capacity: int = 128
    faq_cache_ttl: int = 3600

    # ---- 文档/图片入库（Phase 6：PDF 与图片支持）----
    raw_docs_dir: Path = BASE_DIR / "data" / "raw_docs"  # CLI 批量入库目录（pdf/ 与 images/ 可分层）
    doc_chunk_size: int = 500        # PDF/文本 chunk 字符数
    doc_chunk_overlap: int = 50      # 相邻 chunk 重叠
    ocr_engine: str = "rapidocr"     # 图片 OCR："rapidocr" | "none"
    ocr_min_chars: int = 20          # OCR 文本低于此值 → 判定"无文字"
    # 多模态图片索引（SiliconFlow Qwen3-VL-Embedding API；图片/文本同空间 4096 维）
    vision_embed_enabled: bool = False  # 多模态图片索引总开关
    vision_embed_model: str = "Qwen/Qwen3-VL-Embedding-8B"  # SiliconFlow 多模态 embedding 模型
    vision_embed_dim: int = 0        # 0 = 用 API 默认（4096）；>0 时传 dimensions 截断
    vision_api_key: str | None = None  # SiliconFlow key（缺省回退 rerank_api_key）
    vision_base_url: str = "https://api.siliconflow.cn/v1"
    upload_max_size: int = 20 * 1024 * 1024  # 管理端上传上限（原硬编码 5MB）

    # 跨域（前端 dev 用；生产同域 nginx 反代不需要）。逗号分隔环境变量 CORS_ORIGINS。
    # 不用 list[str] 字段：pydantic-settings 从环境变量解析 JSON 数组易踩坑。
    cors_origins_str: str = ""

    # 用户端上传目录（动态配图；静态服务挂载 /uploads）
    user_uploads_dir: Path = BASE_DIR / "data" / "uploads" / "posts"

    # 文档图片公开副本目录：图片文档入库时复制一份到此（静态挂载 /uploads/docs），
    # metadata 写「图片URL」供聊天回答内联展示（原文件/raw_docs 不对外暴露）
    doc_images_dir: Path = BASE_DIR / "data" / "uploads" / "docs"

    @property
    def cors_origins(self) -> list[str]:
        """解析 CORS_ORIGINS：空串返回 []，按逗号拆分并去空白。"""
        return [o.strip() for o in self.cors_origins_str.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """单例读取配置，避免每次调用重复解析 .env。"""
    return Settings()
