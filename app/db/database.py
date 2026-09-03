"""数据库层：连接管理 + schema 初始化（MySQL 默认 / SQLite 仅测试）。

后端通过 `DB_BACKEND` 配置切换（mysql | sqlite）：
- **mysql**（默认，运行时）：PyMySQL；同一个 `repos` 层直接复用（占位符 `?` → `%s`、
  `ts_expr()`、DDL 方言替换都由本层透明处理）。每个线程独立连接，事务语义与 sqlite 等价。
- **sqlite**（仅离线测试）：`tests/conftest.py` 强制该后端并把 `db_path` 指到每个测试自己的
  临时文件，全离线且不触碰真实业务库；每线程独立连接（thread-local + 代际管理），
  避免单连接被多线程并发使用抛 `InterfaceError`（FastAPI 同步依赖跑在线程池）。
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.core.config import get_settings

# 每线程一个连接（跨线程并发不安全）；_gen 代际号让 reset_db 后旧连接自动废弃重建
_local = threading.local()
_conns: list[Any] = []   # 全部活跃连接注册表（reset_db 统一关闭）
_conns_lock = threading.Lock()
_gen = 0

_BACKENDS = ("sqlite", "mysql")


def _backend() -> str:
    backend = get_settings().db_backend.strip().lower()
    if backend not in _BACKENDS:
        raise ValueError(f"DB_BACKEND 仅支持 {_BACKENDS}，当前：{backend!r}")
    return backend


def ts_expr() -> str:
    """当前后端的时间戳表达式（本地时间）。"""
    return "datetime('now', 'localtime')" if _backend() == "sqlite" else "NOW()"


# -------------------- schema（SQLite 方言为唯一权威，MySQL 由 _mysql_ddl 转换）--------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL,
    doc_type    TEXT NOT NULL,              -- txt / md / csv
    status      TEXT NOT NULL DEFAULT 'parsing',  -- parsing / ready / failed
    version     INTEGER NOT NULL DEFAULT 1,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    error_msg   TEXT,
    tags        TEXT,                       -- 元数据标签（逗号分隔，如 规则类,2024赛事）
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    client_ip    TEXT,
    question     TEXT NOT NULL,
    answer       TEXT,
    sources_json TEXT,
    latency_ms   INTEGER,
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    question    TEXT NOT NULL,
    answer      TEXT,
    rating      INTEGER NOT NULL,          -- 1 赞 / -1 踩
    comment     TEXT,
    trace_id    TEXT,
    user_id     INTEGER NOT NULL DEFAULT 0,  -- 提交用户 id（0=匿名）
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE,
    openid         TEXT,                            -- 微信 openid（小程序绑定；NULL=未绑定）
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'user',   -- 'user' / 'admin'（双角色）
    nickname       TEXT,
    permissions    TEXT,                            -- JSON 数组字符串；NULL=拥有全部模块权限
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_active_at TEXT,
    gender         TEXT,
    level          TEXT,
    racket_model   TEXT,
    avatar         TEXT,
    pref_style     TEXT DEFAULT 'detailed',
    pref_show_sources INTEGER DEFAULT 1,
    phone          TEXT                             -- 微信手机号绑定（小程序；NULL=未绑定）
);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    session_id  TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '新会话',
    tag         TEXT,                     -- 规则类 / 技术类 / 装备类…（用户自选）
    is_favorite INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_user_session ON conversations(user_id, session_id);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role            TEXT NOT NULL,        -- user / assistant
    content         TEXT NOT NULL,
    sources_json    TEXT,
    trace_id        TEXT,
    cached          INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS favorite_folders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS favorites (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    folder_id    INTEGER,                 -- NULL = 未分文件夹
    question     TEXT NOT NULL,
    answer       TEXT NOT NULL,
    sources_json TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    content     TEXT NOT NULL,
    images_json TEXT,                     -- JSON 数组：["/uploads/posts/xxx.jpg"]
    likes       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 动态点赞（每个用户只能点赞同一动态一次；posts.likes 为冗余计数，靠此表唯一约束防刷）
CREATE TABLE IF NOT EXISTS post_likes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_post_likes_user ON post_likes(post_id, user_id);

-- 动态回复（支持一层楼中楼：parent_id 为 NULL=直接回复动态；回复他人回复时
-- parent_id 上提为其所属一级回复 id，reply_to_user_id 记录实际被回复者）
CREATE TABLE IF NOT EXISTS post_replies (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id          INTEGER NOT NULL,
    user_id          INTEGER NOT NULL,
    parent_id        INTEGER,             -- NULL=一级回复；否则为一级回复 id（仅一层嵌套）
    reply_to_user_id INTEGER,             -- 被回复者 user_id（NULL=回复动态本身）
    content          TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_reply_post ON post_replies(post_id);
CREATE INDEX IF NOT EXISTS idx_reply_parent ON post_replies(parent_id);

-- 回复点赞（每个用户只能点赞同一条回复一次）
CREATE TABLE IF NOT EXISTS reply_likes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    reply_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reply_likes_user ON reply_likes(reply_id, user_id);

CREATE TABLE IF NOT EXISTS corrections (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    doc_ref        TEXT,                  -- 引用的文档/原文片段标识
    original_text  TEXT,
    corrected_text TEXT,
    reason         TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',  -- pending / accepted / rejected
    admin_reply    TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    type       TEXT NOT NULL,             -- system / kb_update / correction
    title      TEXT NOT NULL,
    content    TEXT,
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 管理端 Prompt 模板（RAG 调优中心）：激活的模板覆盖 agent 生成节点的 system 提示
CREATE TABLE IF NOT EXISTS prompt_templates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    description   TEXT,
    system_prompt TEXT NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 0,   -- 同一时间至多一个 active（由 repos 保证）
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 运行时检索参数（RAG 调优中心）：key-value，缺省回退 config 默认
-- 列名用 setting_key（key 是 MySQL 保留字，作列名会 1064 语法错误）
CREATE TABLE IF NOT EXISTS rag_settings (
    setting_key TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- RAG 词典（同义词/敏感词）：type = synonym | blacklist
CREATE TABLE IF NOT EXISTS rag_dictionary (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    word        TEXT NOT NULL,
    values_json TEXT,                             -- synonym：组内其余词 JSON 数组；blacklist：NULL
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(type, word)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rag_dict_type_word ON rag_dictionary(type, word);
"""

# users 表扩展列（旧库用 ALTER 迁移；新库由 _SCHEMA 直接全量创建）
_USER_COLUMNS = (
    "gender",            # 性别：男/女/保密
    "level",             # 打球水平：新手/进阶/专业
    "racket_model",      # 常用球拍型号（个性化推荐）
    "avatar",            # 头像（emoji 字符）
    "pref_style",        # 回答语气：simple（简洁）/ detailed（详细）
    "pref_show_sources", # 是否展示引用来源：1/0
    "openid",            # 微信 openid（小程序登录绑定）
    "phone",             # 微信手机号绑定（小程序；合并账号/通知用）
)


def to_mysql_ddl(sqlite_ddl: str) -> str:
    """SQLite DDL → MySQL DDL（纯函数，可单测）。

    转换项：
    - 自增主键 `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGINT AUTO_INCREMENT PRIMARY KEY`；
    - 时间戳默认值 → `CURRENT_TIMESTAMP`；
    - `CREATE [UNIQUE] INDEX IF NOT EXISTS` → 移除 IF NOT EXISTS（MySQL 不支持，存在时忽略）；
    - 被索引/需要 DEFAULT 的文本列改 VARCHAR（MySQL 不允许 TEXT 直接建索引或带默认值）：
      username（唯一约束）→ VARCHAR(255)、session_id（唯一复合索引）→ VARCHAR(100)、
      其余带 DEFAULT 的 TEXT → VARCHAR(255)；
    - 建表语句追加 `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`（保证中文）。
    """
    out: list[str] = []
    for stmt in _split_statements(sqlite_ddl):
        s = stmt
        s = s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT AUTO_INCREMENT PRIMARY KEY")
        s = s.replace("DEFAULT (datetime('now', 'localtime'))", "DEFAULT CURRENT_TIMESTAMP")
        s = s.replace("CREATE UNIQUE INDEX IF NOT EXISTS ", "CREATE UNIQUE INDEX ")
        s = s.replace("CREATE INDEX IF NOT EXISTS ", "CREATE INDEX ")
        s = re.sub(r"username\s+TEXT NOT NULL UNIQUE", "username VARCHAR(255) NOT NULL UNIQUE", s)
        s = re.sub(r"session_id\s+TEXT NOT NULL", "session_id VARCHAR(100) NOT NULL", s)
        s = re.sub(r"\bopenid\s+TEXT", "openid VARCHAR(100)", s)
        s = re.sub(r"\bphone\s+TEXT", "phone VARCHAR(20)", s)
        # 时间戳列在 MySQL 用 DATETIME（TEXT 不能带 DEFAULT CURRENT_TIMESTAMP）
        s = re.sub(r"\bcreated_at\s+TEXT", "created_at DATETIME", s)
        s = re.sub(r"\bupdated_at\s+TEXT", "updated_at DATETIME", s)
        s = re.sub(r"\blast_active_at\s+TEXT", "last_active_at DATETIME", s)
        # MySQL：BLOB/TEXT 列不能带 DEFAULT → 带默认值的 TEXT 转 VARCHAR(255)
        s = re.sub(r"\bTEXT\b(?=[^,\n]*DEFAULT)", "VARCHAR(255)", s)
        # MySQL 不允许 TEXT 直接作索引键（PK/UNIQUE）：
        # rag_settings.setting_key TEXT PRIMARY KEY → VARCHAR；rag_dictionary UNIQUE(type, word) → VARCHAR
        s = re.sub(r"\bsetting_key\s+TEXT(?=\s*PRIMARY KEY)", "setting_key VARCHAR(100)", s)
        s = re.sub(r"\btype\s+TEXT( NOT NULL)?(?=[^;]*UNIQUE)", r"type VARCHAR(50)\1", s)
        s = re.sub(r"\bword\s+TEXT( NOT NULL)?(?=[^;]*UNIQUE)", r"word VARCHAR(255)\1", s)
        # 建表语句可能有前导 `-- 注释` 行（如新表说明），剥离后再判定
        body = re.sub(r"^--.*$", "", s, flags=re.MULTILINE).strip()
        if body.lower().startswith("create table"):
            s = f"{s} ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
        else:
            s = f"{s};"
        out.append(s)
    return "\n".join(out) + "\n"


def _split_statements(script: str) -> list[str]:
    """按分号切分 DDL 脚本（当前 DDL 无 ';' 的字面量）。"""
    return [s.strip() for s in script.split(";") if s.strip()]


# -------------------- MySQL 连接代理（让 repos 无感知复用）--------------------


class _MySQLConn:
    """PyMySQL 连接适配器：模拟 sqlite3 的 conn.execute/commit 用法。

    - `execute(sql, params)`：`?` 占位符 → `%s`，返回可 fetch/lastrowid/rowcount 的游标；
    - `executescript(script)`：逐条执行 DDL（MySQL 无 executescript）；
    - 行对象为 dict（等价 sqlite3.Row 的 dict(row)/row["col"] 用法）。
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    def execute(self, sql: str, params: tuple | list | dict = ()):
        cur = self._conn.cursor()
        cur.execute(sql.replace("?", "%s"), params if params not in ((), None) else None)
        return cur

    def executescript(self, script: str) -> None:
        for stmt in _split_statements(script):
            try:
                self.execute(stmt)
            except Exception:
                # MySQL 不支持 CREATE INDEX IF NOT EXISTS：索引已存在时忽略
                lower = stmt.lstrip().lower()
                if not (lower.startswith("create unique index") or lower.startswith("create index")):
                    raise
        self.commit()

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def _table_columns(conn, table: str) -> set[str]:
    """取表的所有列名（兼容 sqlite PRAGMA 与 MySQL information_schema）。"""
    if isinstance(conn, sqlite3.Connection):
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r["name"] for r in rows}
    rows = conn.execute(
        "SELECT COLUMN_NAME AS name FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    ).fetchall()
    return {r["name"] for r in rows}


def _migrate_users(conn) -> None:
    """旧库 users 表缺少新列时 ALTER 补列（幂等，兼容 sqlite/mysql）。

    用 VARCHAR(255)（sqlite 接受，MySQL 也接受；TEXT 在 MySQL 不能带默认值）。
    ALTER 之后补 openid 唯一索引：不能在 _SCHEMA 里建（旧库执行时列还不存在）。
    """
    existing = _table_columns(conn, "users")
    for col in _USER_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} VARCHAR(255) DEFAULT NULL")
    if _backend() == "mysql":
        try:
            conn.execute("CREATE UNIQUE INDEX idx_users_openid ON users(openid)")
        except Exception:
            pass  # 已存在（1061）时忽略
        try:
            conn.execute("CREATE UNIQUE INDEX idx_users_phone ON users(phone)")
        except Exception:
            pass  # 已存在（1061）时忽略
    else:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_openid ON users(openid)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone)")
    conn.commit()


# post_replies 在开发中加过列（楼中楼被回复者），旧表（CREATE TABLE IF NOT EXISTS 不会补列）需 ALTER
_REPLY_COLUMNS = ("reply_to_user_id",)


def _migrate_replies(conn) -> None:
    """旧库 post_replies 表缺少新列时 ALTER 补列（幂等，兼容 sqlite/mysql）。"""
    existing = _table_columns(conn, "post_replies")
    for col in _REPLY_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE post_replies ADD COLUMN {col} INTEGER")
    conn.commit()


# documents 表管理端元数据打标列（旧库 ALTER；新库由 _SCHEMA 直建）
_DOC_COLUMNS = ("tags",)


def _migrate_documents(conn) -> None:
    """旧库 documents 表缺少新列时 ALTER 补列（幂等，兼容 sqlite/mysql）。"""
    existing = _table_columns(conn, "documents")
    for col in _DOC_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} VARCHAR(255) DEFAULT NULL")
    conn.commit()


# 预置 Prompt 模板（幂等：仅首次建库时播种；管理端可在调优中心编辑/激活）
_SEED_PROMPT_TEMPLATES = (
    (
        "默认知识助手",
        "默认模板：与内置提示一致（事实驱动、附来源、可用图片链接）",
        (
            "你是羽毛球装备/知识问答助手。请只依据给定的检索内容回答问题，禁止编造任何信息。\n"
            "要求：\n"
            "1. 只输出 JSON，不要输出任何其他文字；\n"
            '2. 格式为 {"answer": "回答正文（末尾附来源）", "used": [被引用的条目编号]}；\n'
            "3. 若检索内容不足以回答，则 answer 为 知识库中暂无相关信息，used 为空数组；\n"
            "4. 回答末尾必须逐条附「来源：品牌 型号」或「来源：表名 主题」；\n"
            "5. 若检索内容带「图片链接：url」，请在回答合适位置以 markdown 图片语法原样输出 ![图片说明](url)。"
        ),
    ),
    (
        "裁判员语气",
        "严格按规则条款作答，引文用《》，措辞严谨客观",
        (
            "你是羽毛球比赛裁判长。请只依据给定的检索内容，用严谨、客观、权威的口吻回答问题。\n"
            "引用规则条款时使用《》引用原文，禁止编造任何信息；检索内容不足时直接回答 知识库中暂无相关信息。\n"
            '只输出 JSON：{"answer": "回答正文（末尾附来源）", "used": [...]}。'
        ),
    ),
    (
        "教练员语气",
        "面向学员的教学口吻，分要点、给建议",
        (
            "你是羽毛球教练。请只依据给定的检索内容，用亲切、鼓励的教学口吻回答问题，\n"
            "按要点分条讲解，可给出练习建议；禁止编造任何信息；检索内容不足时直接回答 知识库中暂无相关信息。\n"
            '只输出 JSON：{"answer": "回答正文（末尾附来源）", "used": [...]}。'
        ),
    ),
)


def _seed_prompt_templates(conn) -> None:
    """首次启动播种预置模板（幂等：已有同名模板则不重复插入；不激活任何模板）."""
    existing = {
        r["name"]
        for r in conn.execute("SELECT name FROM prompt_templates")
    }
    idx = 0
    for name, desc, system_prompt in _SEED_PROMPT_TEMPLATES:
        if name not in existing:
            conn.execute(
                "INSERT INTO prompt_templates (name, description, system_prompt, is_active) "
                "VALUES (?, ?, ?, 0)",
                (name, desc, system_prompt),
            )
            idx += 1
    if idx:
        conn.commit()


def _new_sqlite_conn() -> sqlite3.Connection:
    path = get_settings().db_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")  # 多连接写冲突时等待而非直接报错
    conn.executescript(_SCHEMA)
    _migrate_users(conn)
    _migrate_replies(conn)
    _migrate_documents(conn)
    conn.commit()
    return conn


def _ensure_mysql_database(pymysql, s) -> None:
    """目标库不存在时自动创建（首次连接自动建库，免手动 CREATE DATABASE）。

    需要账号具备 CREATE 权限（root 或已授权）；无权时抛出原始错误。
    """
    if "`" in s.mysql_db:  # 库名必须能安全嵌入反引号标识符
        raise ValueError("MYSQL_DB 不能包含反引号")
    admin = pymysql.connect(
        host=s.mysql_host,
        port=s.mysql_port,
        user=s.mysql_user,
        password=s.mysql_password,
        charset=s.mysql_charset,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with admin.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{s.mysql_db}` CHARACTER SET {s.mysql_charset}"
            )
        admin.commit()
    finally:
        admin.close()


def _new_mysql_conn() -> _MySQLConn:
    try:
        import pymysql
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时给出明确提示
        raise RuntimeError("DB_BACKEND=mysql 需要安装 PyMySQL：pip install pymysql") from exc
    s = get_settings()

    def _connect():
        return pymysql.connect(
            host=s.mysql_host,
            port=s.mysql_port,
            user=s.mysql_user,
            password=s.mysql_password,
            database=s.mysql_db,
            charset=s.mysql_charset,
            cursorclass=pymysql.cursors.DictCursor,
        )

    try:
        raw = _connect()
    except pymysql.err.OperationalError as exc:
        if exc.args and exc.args[0] == 1049:  # Unknown database → 自动建库后重连一次
            _ensure_mysql_database(pymysql, s)
            raw = _connect()
        else:
            raise
    conn = _MySQLConn(raw)
    conn.executescript(to_mysql_ddl(_SCHEMA))
    _migrate_users(conn)
    _migrate_replies(conn)
    _migrate_documents(conn)
    conn.commit()
    return conn


def _new_conn():
    """按后端创建并初始化一个新连接（建表 + 迁移）。"""
    if _backend() == "mysql":
        return _new_mysql_conn()
    return _new_sqlite_conn()


def get_conn():
    """返回当前线程的数据库连接（懒创建；reset_db 后按代际号自动重建）。"""
    pair = getattr(_local, "pair", None)
    if pair is None or pair[0] != _gen:
        conn = _new_conn()
        with _conns_lock:
            _conns.append(conn)
        pair = (_gen, conn)
        _local.pair = pair
    return pair[1]


def init_db() -> None:
    """幂等初始化：确保建表 + 播种预置 Prompt 模板（供应用启动与测试复用）。"""
    conn = get_conn()
    _seed_prompt_templates(conn)


def reset_db() -> None:
    """清空全部连接（测试隔离用：关闭所有线程连接，代际 +1 使旧引用失效重建）。"""
    global _gen
    with _conns_lock:
        for conn in _conns:
            try:
                conn.close()
            except Exception:
                pass
        _conns.clear()
    _gen += 1
    try:
        del _local.pair
    except AttributeError:
        pass
