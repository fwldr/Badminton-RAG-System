"""MySQL 方言适配测试（纯离线：不连真实 MySQL，验证 DDL 转换与连接代理的 SQL 形态）。

- to_mysql_ddl：SQLite DDL → MySQL DDL（自增主键/时间戳默认值/索引/带默认值 TEXT→VARCHAR/DATETIME）。
- _MySQLConn.execute：`?` 占位符 → `%s`，参数透传。
- ts_expr：sqlite/mysql 各自的时间戳表达式。
- 自动建库：目标库不存在（1049）时 CREATE DATABASE IF NOT EXISTS 后重连（假 pymysql 模块，全程离线）。
"""

import re
import sys

import pytest

from app.core.config import get_settings
from app.db.database import (
    _MySQLConn,
    _SCHEMA,
    _backend,
    _new_mysql_conn,
    to_mysql_ddl,
    ts_expr,
)


def test_backend_validation_and_ts():
    assert _backend() == "sqlite"  # 默认后端（测试环境无 .env 覆盖时）
    assert ts_expr() == "datetime('now', 'localtime')"
    assert to_mysql_ddl("")  # 空输入不报错


def test_mysql_ddl_transforms(monkeypatch):
    monkeypatch.setattr(get_settings(), "db_backend", "mysql")
    assert ts_expr() == "NOW()"

    ddl = to_mysql_ddl(_SCHEMA)
    # 自增主键 & 引擎字符集
    assert "BIGINT AUTO_INCREMENT PRIMARY KEY" in ddl
    assert "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4" in ddl
    # 时间戳默认值与索引（去掉 IF NOT EXISTS）
    assert "DEFAULT CURRENT_TIMESTAMP" in ddl
    assert "datetime('now'" not in ddl
    assert "CREATE INDEX IF NOT EXISTS" not in ddl
    assert "CREATE UNIQUE INDEX idx_conv_user_session ON conversations(user_id, session_id);" in ddl
    # TEXT 列不允许直接建索引或带默认值 → 关键列改 VARCHAR / 时间戳列改 DATETIME
    assert "username VARCHAR(255) NOT NULL UNIQUE" in ddl
    assert "session_id VARCHAR(100) NOT NULL" in ddl
    assert "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP" in ddl
    assert "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP" in ddl
    assert re.search(r"role\s+VARCHAR\(255\) NOT NULL DEFAULT 'user'", ddl)  # 带默认值的 TEXT → VARCHAR
    assert re.search(r"status\s+VARCHAR\(255\) NOT NULL DEFAULT 'parsing'", ddl)
    assert "TEXT NOT NULL DEFAULT" not in ddl                          # 不再有带默认值的 TEXT
    # TEXT 键列（PK/UNIQUE 索引键）转 VARCHAR（MySQL 不允许 TEXT 直接作索引键）；
    # 列名用 setting_key（key 是 MySQL 保留字，作列名会语法错误）
    assert "setting_key VARCHAR(100) PRIMARY KEY" in ddl
    assert "type VARCHAR(50) NOT NULL" in ddl
    assert "word VARCHAR(255) NOT NULL" in ddl
    # 每个表都带引擎（17 张表 + 索引语句）
    assert ddl.count("ENGINE=InnoDB") == 17
    # 索引语句不应被追加 ENGINE
    for line in ddl.split("\n"):
        if line.strip().startswith("CREATE"):
            assert "session_id); ENGINE" not in line


def test_mysql_proxy_placeholder_translation(monkeypatch):
    monkeypatch.setattr(get_settings(), "db_backend", "mysql")

    class FakeCursor:
        def __init__(self):
            self.executed = None
            self.args = None

        def execute(self, sql, args=None):
            self.executed = sql
            self.args = args
            return self

    class FakeConn:
        def __init__(self):
            self.cursor_ = FakeCursor()

        def cursor(self):
            return self.cursor_

    proxy = _MySQLConn(FakeConn())
    cur = proxy.execute("SELECT * FROM users WHERE id = ? AND role = ?", (1, "admin"))
    assert cur.executed == "SELECT * FROM users WHERE id = %s AND role = %s"
    assert cur.args == (1, "admin")
    # 只翻译占位符，不影响 LIKE 的 % 字面量
    cur = proxy.execute("SELECT * FROM x WHERE name LIKE ?", ("%李%",))
    assert cur.executed == "SELECT * FROM x WHERE name LIKE %s"
    assert cur.args == ("%李%",)


def test_mysql_autocreate_database(monkeypatch):
    """目标库不存在（1049）时自动 CREATE DATABASE IF NOT EXISTS 并重连（假 pymysql，离线）。"""
    calls = []   # connect 调用记录（kw 参数）
    sqls = []    # 全部执行的 SQL

    class FakeError(Exception):
        def __init__(self, args):
            self.args = args

    class FakeCursor:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, args=None):
            sqls.append(sql)
            return self

        def fetchall(self):
            return []   # information_schema 查询 → 无列 → 走全量 ALTER（无副作用）

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor(self)

        def commit(self):
            pass

        def close(self):
            pass

    class FakePymysql:
        cursors = type("_cursors", (), {"DictCursor": object})
        err = type("_err", (), {"OperationalError": FakeError})

        def __init__(self):
            self.attempts = 0

        def connect(self, **kw):
            self.attempts += 1
            calls.append(kw)
            if kw.get("database") and self.attempts == 1:
                raise FakeError((1049, "Unknown database 'badminton'"))
            return FakeConn()

    fake = FakePymysql()
    monkeypatch.setitem(sys.modules, "pymysql", fake)
    monkeypatch.setattr(get_settings(), "db_backend", "mysql")
    monkeypatch.setattr(get_settings(), "mysql_db", "badminton")
    monkeypatch.setattr(get_settings(), "mysql_password", "")

    conn = _new_mysql_conn()
    assert isinstance(conn, _MySQLConn)
    # 带库连接失败(1) → 免库连接建库(2) → 重试成功(3)
    assert fake.attempts == 3
    assert any(not kw.get("database") for kw in calls)  # 有一次不指定库的连接（建库用）
    assert any(
        "CREATE DATABASE IF NOT EXISTS `badminton` CHARACTER SET utf8mb4" in s for s in sqls
    )
    # 建库后照常执行 schema
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS") for s in sqls)
