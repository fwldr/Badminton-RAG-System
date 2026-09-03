"""SQLite 层测试：内存/临时库 CRUD + 状态机 + 审计 + 多线程并发。"""

import concurrent.futures

import pytest

from app.core.security import hash_password
from app.db.database import get_conn, reset_db
from app.db.repos import AuditRepo, DocRepo, UserRepo
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """每个测试用独立临时库文件（改 db_path 后重建连接）。"""
    monkeypatch.setattr(get_settings(), "db_path", tmp_path / "test.db")
    reset_db()
    yield
    reset_db()


def test_schema_created():
    conn = get_conn()
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"documents", "audit_logs"} <= tables


def test_migrate_reply_column_on_old_table():
    """旧库 post_replies（无 reply_to_user_id 列）在新连接初始化时自动 ALTER 补列。

    复现过的问题：开发中给 post_replies 加列，生产库先建了旧版表，
    CREATE TABLE IF NOT EXISTS 不会补列 → 查询报 Unknown column。
    """
    conn = get_conn()
    # 模拟旧版表结构（无 reply_to_user_id）
    conn.execute("DROP TABLE IF EXISTS post_likes")
    conn.execute("DROP TABLE IF EXISTS reply_likes")
    conn.execute("DROP TABLE IF EXISTS post_replies")
    conn.execute(
        "CREATE TABLE post_replies ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
        "parent_id INTEGER, content TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    )
    conn.commit()

    # 新连接：executescript 建表被 IF NOT EXISTS 跳过 → _migrate_replies 补列
    reset_db()
    conn2 = get_conn()
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(post_replies)").fetchall()}
    assert "reply_to_user_id" in cols


def test_migrate_doc_tags_column_on_old_table():
    """旧库 documents（无 tags 列）在新连接初始化时自动 ALTER 补列（管理端打标用）。"""
    conn = get_conn()
    conn.execute("DROP TABLE IF EXISTS documents")
    conn.execute(
        "CREATE TABLE documents ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL, doc_type TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'parsing', version INTEGER NOT NULL DEFAULT 1, "
        "chunk_count INTEGER NOT NULL DEFAULT 0, error_msg TEXT, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    )
    conn.commit()

    reset_db()
    conn2 = get_conn()
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(documents)").fetchall()}
    assert "tags" in cols


def test_migrate_users_openid_column_on_old_table():
    """旧库 users（无 openid 等列）→ 新连接自动 ALTER 补列 + 建 openid 唯一索引。"""
    conn = get_conn()
    conn.execute("DROP TABLE IF EXISTS users")
    conn.execute(
        "CREATE TABLE users ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, "
        "password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', nickname TEXT, "
        "permissions TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')), last_active_at TEXT)"
    )
    conn.commit()

    reset_db()
    conn2 = get_conn()
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(users)").fetchall()}
    assert "openid" in cols

    # 唯一索引存在：重复 openid 建号被约束拦截
    from app.db.repos import UserRepo

    UserRepo.create_wx("openid-x")
    with pytest.raises(Exception):
        UserRepo.create_wx("openid-x")
    reset_db()  # 关闭连接以免遗留异常连接


def test_admin_tables_and_seeded_templates():
    """管理端新表（prompt_templates/rag_settings/rag_dictionary）建表 + 种子模板幂等。"""
    from app.db.database import init_db
    from app.db.repos import PromptTemplateRepo

    conn = get_conn()
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"prompt_templates", "rag_settings", "rag_dictionary"} <= tables

    init_db()  # 幂等播种
    seeded = PromptTemplateRepo.list_all()
    names = {t["name"] for t in seeded}
    assert {"默认知识助手", "裁判员语气", "教练员语气"} <= names
    assert sum(1 for t in seeded if t["is_active"] == 1) == 0  # 不自动激活（保持默认行为）
    init_db()  # 再播种不重复
    assert len(PromptTemplateRepo.list_all()) == len(seeded)


def test_doc_crud_and_status_machine():
    doc_id = DocRepo.create("笔记.md", "md")
    doc = DocRepo.get(doc_id)
    assert doc["status"] == "parsing"
    assert doc["version"] == 1

    DocRepo.update_status(doc_id, "ready", chunk_count=5)
    doc = DocRepo.get(doc_id)
    assert doc["status"] == "ready"
    assert doc["chunk_count"] == 5

    assert DocRepo.bump_version(doc_id) == 2

    assert DocRepo.delete(doc_id) is True
    assert DocRepo.get(doc_id) is None
    assert DocRepo.delete(doc_id) is False


def test_audit_insert_query_export():
    AuditRepo.insert("127.0.0.1", "你好", "回答", '[]', 12)
    AuditRepo.insert(None, "第二个", None, None, None)
    logs = AuditRepo.query()
    assert len(logs) == 2
    assert logs[0]["question"] == "第二个"  # 按 id DESC
    assert AuditRepo.count() == 2
    exported = list(AuditRepo.export_all())
    assert len(exported) == 2
    assert exported[0]["client_ip"] == "127.0.0.1"


def test_concurrent_reads_no_interface_error():
    """多线程并发访问：单连接被多线程共用会抛 InterfaceError（工作台曾触发），
    现改为每线程独立连接 + WAL，并发读写应稳定。"""
    uid = UserRepo.create("racer", hash_password("secret123"))

    def _read(_: int) -> int:
        # 每线程独立连接：读写交替，模拟页面多请求并发
        for _ in range(20):
            u = UserRepo.get_by_id(uid)
            assert u is not None
            UserRepo.update_last_active(uid)  # UPDATE（写）
        return u["id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_read, range(8)))
    assert set(results) == {uid}
    # 每线程连接各自独立建表（幂等），总连接数 > 1
    from app.db import database as db_mod

    with db_mod._conns_lock:
        assert len(db_mod._conns) >= 2
