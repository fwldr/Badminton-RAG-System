"""Repository 层：文档元数据 + 审计日志 + 用户/会话/收藏/动态/纠错/通知的纯 SQL 访问。

隔离 DB 细节，可换 MySQL 实现；全部为无状态静态方法（数据库单连接见 database.get_conn）。
"""

from __future__ import annotations

import json
import secrets
from typing import Iterable

from app.core.security import hash_password
from app.db.database import get_conn, ts_expr


def user_to_public(user: dict) -> dict:
    """剥离敏感字段（密码哈希/openid）的用户信息，附绑定状态，供 API 返回。"""
    keys = (
        "id", "username", "role", "nickname", "permissions", "is_active", "created_at",
        "last_active_at", "gender", "level", "racket_model", "avatar",
        "pref_style", "pref_show_sources",
    )
    out = {k: user[k] for k in keys if k in user}
    out["wx_bound"] = bool(user.get("openid"))
    out["phone_bound"] = bool(user.get("phone"))
    return out


class UserRepo:
    """用户账户（双角色：user / admin，支持模块级权限与个性化资料）。"""

    @staticmethod
    def create(username: str, password_hash: str, role: str = "user", nickname: str | None = None) -> int:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, nickname) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, nickname),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def get_by_username(username: str) -> dict | None:
        conn = get_conn()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_by_openid(openid: str) -> dict | None:
        """按微信 openid 查找用户（小程序登录绑定）。"""
        conn = get_conn()
        row = conn.execute("SELECT * FROM users WHERE openid = ?", (openid,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def bind_openid(user_id: int, openid: str | None) -> None:
        """绑定/解绑微信 openid（None=解绑；唯一性由 UNIQUE 索引兜底）。"""
        conn = get_conn()
        conn.execute("UPDATE users SET openid = ? WHERE id = ?", (openid, user_id))
        conn.commit()

    @staticmethod
    def get_by_phone(phone: str) -> dict | None:
        """按微信手机号查找用户（手机号绑定合并检查）。"""
        conn = get_conn()
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def bind_phone(user_id: int, phone: str | None) -> None:
        """绑定/解绑微信手机号（None=解绑；唯一性由 UNIQUE 索引兜底）。"""
        conn = get_conn()
        conn.execute("UPDATE users SET phone = ? WHERE id = ?", (phone, user_id))
        conn.commit()

    @staticmethod
    def create_wx(openid: str, nickname: str | None = None) -> int:
        """创建微信账号：用户名 wx_<openid 前 29 位>，随机密码哈希（不可密码登录）。"""
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, nickname, openid) VALUES (?, ?, 'user', ?, ?)",
            (f"wx_{openid[:29]}", hash_password(secrets.token_hex(16)), nickname or "微信用户", openid),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def get_by_id(user_id: int) -> dict | None:
        conn = get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def list_all(limit: int = 50, offset: int = 0) -> list[dict]:
        conn = get_conn()
        rows = conn.execute(
            "SELECT id, username, role, nickname, permissions, is_active, created_at, last_active_at "
            "FROM users ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def count() -> int:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def update_last_active(user_id: int) -> None:
        conn = get_conn()
        conn.execute(
            f"UPDATE users SET last_active_at = {ts_expr()} WHERE id = ?",
            (user_id,),
        )
        conn.commit()

    @staticmethod
    def set_role(user_id: int, role: str) -> None:
        conn = get_conn()
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        conn.commit()

    @staticmethod
    def set_active(user_id: int, active: bool) -> None:
        conn = get_conn()
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if active else 0, user_id))
        conn.commit()

    @staticmethod
    def set_permissions(user_id: int, permissions: str | None) -> None:
        conn = get_conn()
        conn.execute("UPDATE users SET permissions = ? WHERE id = ?", (permissions, user_id))
        conn.commit()

    @staticmethod
    def update_profile(user_id: int, fields: dict) -> dict:
        """更新个性化资料/偏好（白名单字段，None 不修改），返回最新用户。"""
        allowed = {
            "nickname", "gender", "level", "racket_model", "avatar",
            "pref_style", "pref_show_sources",
        }
        sets, values = [], []
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            sets.append(f"{k} = ?")
            values.append(v)
        if sets:
            conn = get_conn()
            conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", (*values, user_id))
            conn.commit()
        return UserRepo.get_by_id(user_id)


class DocRepo:
    """上传文档元数据（状态机：parsing → ready / failed；version 随重索引递增）。"""

    @staticmethod
    def create(filename: str, doc_type: str) -> int:
        """插入记录（status=parsing, version=1），返回自增 id。"""
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO documents (filename, doc_type) VALUES (?, ?)",
            (filename, doc_type),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def get(doc_id: int) -> dict | None:
        conn = get_conn()
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def list_all() -> list[dict]:
        conn = get_conn()
        return [dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY id DESC")]

    @staticmethod
    def update_status(doc_id: int, status: str, chunk_count: int = 0, error_msg: str | None = None) -> None:
        conn = get_conn()
        conn.execute(
            "UPDATE documents SET status = ?, chunk_count = ?, error_msg = ? WHERE id = ?",
            (status, chunk_count, error_msg, doc_id),
        )
        conn.commit()

    @staticmethod
    def bump_version(doc_id: int) -> int:
        """版本 +1，返回新版本号。"""
        conn = get_conn()
        conn.execute("UPDATE documents SET version = version + 1 WHERE id = ?", (doc_id,))
        conn.commit()
        row = conn.execute("SELECT version FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return int(row["version"]) if row else 0

    @staticmethod
    def delete(doc_id: int) -> bool:
        conn = get_conn()
        cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def set_tags(doc_id: int, tags: str | None) -> None:
        """更新文档元数据标签（逗号分隔字符串，None 清空）。"""
        conn = get_conn()
        conn.execute("UPDATE documents SET tags = ? WHERE id = ?", (tags, doc_id))
        conn.commit()

    @staticmethod
    def count_by_type() -> dict[str, int]:
        """按文档类型计数（pdf/image/txt/md/csv…）。"""
        conn = get_conn()
        rows = conn.execute(
            "SELECT doc_type, COUNT(*) AS n FROM documents GROUP BY doc_type"
        ).fetchall()
        return {str(r["doc_type"]): int(r["n"]) for r in rows}

    @staticmethod
    def count_by_status(status: str) -> int:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM documents WHERE status = ?", (status,)).fetchone()
        return int(row["c"]) if row else 0


class AuditRepo:
    """问答审计日志。"""

    @staticmethod
    def insert(client_ip: str | None, question: str, answer: str | None, sources_json: str | None, latency_ms: int | None) -> int:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO audit_logs (client_ip, question, answer, sources_json, latency_ms) VALUES (?, ?, ?, ?, ?)",
            (client_ip, question, answer, sources_json, latency_ms),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def query(limit: int = 50, offset: int = 0) -> list[dict]:
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def count() -> int:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM audit_logs").fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def export_all() -> Iterable[dict]:
        """全量导出（CSV 流式）。"""
        conn = get_conn()
        for r in conn.execute("SELECT * FROM audit_logs ORDER BY id"):
            yield dict(r)


class FeedbackRepo:
    """用户反馈（点赞/点踩 + 评论），在线 bad case 来源。"""

    @staticmethod
    def insert(
        session_id: str,
        question: str,
        answer: str | None,
        rating: int,
        comment: str | None,
        trace_id: str | None,
        user_id: int = 0,
    ) -> int:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO feedback (session_id, question, answer, rating, comment, trace_id, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, question, answer, rating, comment, trace_id, user_id),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def query(limit: int = 50, offset: int = 0) -> list[dict]:
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM feedback ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def count() -> int:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM feedback").fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def count_dislikes() -> int:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM feedback WHERE rating = -1").fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def bad_questions(limit: int = 20) -> list[dict]:
        """点踩聚合：同一问题被多次点踩的列表（低质量回答干预提示）。

        返回 {question, dislike_count, last_comment, last_trace_id, last_at}，
        按点踩次数降序；last_* 取该问题最近一条点踩记录（子查询，跨后端无窗口函数）。
        """
        conn = get_conn()
        rows = conn.execute(
            "SELECT f.question, COUNT(*) AS dislike_count, MAX(f.created_at) AS last_at, "
            "(SELECT comment FROM feedback f2 WHERE f2.question = f.question AND f2.rating = -1 "
            " ORDER BY f2.id DESC LIMIT 1) AS last_comment, "
            "(SELECT trace_id FROM feedback f3 WHERE f3.question = f.question AND f3.rating = -1 "
            " ORDER BY f3.id DESC LIMIT 1) AS last_trace_id "
            "FROM feedback f WHERE f.rating = -1 "
            "GROUP BY f.question ORDER BY dislike_count DESC, last_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


class ConversationRepo:
    """用户会话（历史对话记录）：按 (user_id, session_id) 唯一，保存问答双方消息。"""

    @staticmethod
    def upsert(user_id: int, session_id: str, title: str | None = None) -> int:
        """按 (user_id, session_id) 取或建会话；title 非空且原为默认标题时更新标题。"""
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM conversations WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()
        if row:
            conv_id = int(row["id"])
            if title and (row["title"] == "新会话" or not row["title"]):
                conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conv_id))
                conn.commit()
            conn.execute(
                f"UPDATE conversations SET updated_at = {ts_expr()} WHERE id = ?",
                (conv_id,),
            )
            conn.commit()
            return conv_id
        cur = conn.execute(
            "INSERT INTO conversations (user_id, session_id, title) VALUES (?, ?, ?)",
            (user_id, session_id, title or "新会话"),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def get(user_id: int, conv_id: int) -> dict | None:
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def list_user(user_id: int, q: str = "", tag: str = "", favorite: bool = False,
                  limit: int = 50, offset: int = 0) -> list[dict]:
        conn = get_conn()
        where = ["user_id = ?"]
        params: list = [user_id]
        if q:
            where.append("(title LIKE ? OR session_id LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if tag:
            where.append("tag = ?")
            params.append(tag)
        if favorite:
            where.append("is_favorite = 1")
        params += [limit, offset]
        rows = conn.execute(
            f"SELECT c.*, "
            f"(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS msg_count, "
            f"(SELECT content FROM messages m WHERE m.conversation_id = c.id AND m.role='assistant' "
            f" ORDER BY m.id DESC LIMIT 1) AS last_answer "
            f"FROM conversations c WHERE {' AND '.join(where)} "
            f"ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def count_user(user_id: int) -> int:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM conversations WHERE user_id = ?", (user_id,)).fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def count_all() -> int:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM conversations").fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def update(user_id: int, conv_id: int, fields: dict) -> dict | None:
        """白名单更新：title / tag / is_favorite。"""
        conn = get_conn()
        sets, values = [], []
        for k in ("title", "tag", "is_favorite"):
            if k in fields and fields[k] is not None:
                sets.append(f"{k} = ?")
                values.append(fields[k])
        if not sets:
            return None
        values += [user_id, conv_id]
        conn.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ? AND user_id = ?", values)
        conn.commit()
        return ConversationRepo.get(user_id, conv_id)

    @staticmethod
    def delete(user_id: int, conv_id: int) -> bool:
        conn = get_conn()
        cur = conn.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id))
        conn.commit()
        return cur.rowcount > 0


class MessageRepo:
    """会话消息（user/assistant 成对落库，供工作台回放）。"""

    @staticmethod
    def add(conversation_id: int, role: str, content: str, sources_json: str | None = None,
            trace_id: str | None = None, cached: int = 0) -> int:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, sources_json, trace_id, cached) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, role, content, sources_json, trace_id, cached),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def list_conversation(conversation_id: int, limit: int = 200) -> list[dict]:
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def count_all() -> int:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def count_since(since: str) -> int:
        """计数 created_at >= since（如 'YYYY-MM-DD'；字符串前缀比较跨后端一致）。"""
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE created_at >= ?", (since,)).fetchone()
        return int(row["c"]) if row else 0


class FavoriteFolderRepo:
    """收藏文件夹：手动建立分类（如「发球技巧」「伤病康复计划」）。"""

    @staticmethod
    def create(user_id: int, name: str) -> int:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO favorite_folders (user_id, name) VALUES (?, ?)", (user_id, name)
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def list_user(user_id: int) -> list[dict]:
        conn = get_conn()
        rows = conn.execute(
            "SELECT f.*, (SELECT COUNT(*) FROM favorites v WHERE v.folder_id = f.id) AS fav_count "
            "FROM favorite_folders f WHERE f.user_id = ? ORDER BY f.id",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get(user_id: int, folder_id: int) -> dict | None:
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM favorite_folders WHERE id = ? AND user_id = ?", (folder_id, user_id)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def delete(user_id: int, folder_id: int) -> bool:
        conn = get_conn()
        # 把该文件夹下的收藏置为未分文件夹，再删文件夹
        conn.execute("UPDATE favorites SET folder_id = NULL WHERE folder_id = ?", (folder_id,))
        cur = conn.execute("DELETE FROM favorite_folders WHERE id = ? AND user_id = ?", (folder_id, user_id))
        conn.commit()
        return cur.rowcount > 0


class FavoriteRepo:
    """个人收藏夹：收藏有价值的 AI 回答。"""

    @staticmethod
    def create(user_id: int, question: str, answer: str, sources_json: str | None = None,
               folder_id: int | None = None) -> int:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO favorites (user_id, folder_id, question, answer, sources_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, folder_id, question, answer, sources_json),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def list_user(user_id: int, q: str = "", folder_id: int | None = None,
                  limit: int = 50, offset: int = 0) -> list[dict]:
        conn = get_conn()
        where = ["user_id = ?"]
        params: list = [user_id]
        if q:
            where.append("(question LIKE ? OR answer LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if folder_id is not None:
            where.append("folder_id = ?")
            params.append(folder_id)
        params += [limit, offset]
        rows = conn.execute(
            f"SELECT * FROM favorites WHERE {' AND '.join(where)} "
            f"ORDER BY id DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def count_user(user_id: int) -> int:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM favorites WHERE user_id = ?", (user_id,)).fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def get(user_id: int, fav_id: int) -> dict | None:
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM favorites WHERE id = ? AND user_id = ?", (fav_id, user_id)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def set_folder(user_id: int, fav_id: int, folder_id: int | None) -> bool:
        conn = get_conn()
        cur = conn.execute(
            "UPDATE favorites SET folder_id = ? WHERE id = ? AND user_id = ?",
            (folder_id, fav_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def delete(user_id: int, fav_id: int) -> bool:
        conn = get_conn()
        cur = conn.execute("DELETE FROM favorites WHERE id = ? AND user_id = ?", (fav_id, user_id))
        conn.commit()
        return cur.rowcount > 0


def _is_unique_violation(exc: Exception) -> bool:
    """判断是否为唯一约束冲突（sqlite3.IntegrityError / pymysql.err.IntegrityError）。"""
    return type(exc).__name__ == "IntegrityError"


class PostRepo:
    """用户动态：分享训练心得/比赛经验/提问（文本 + 图片），公开可浏览。

    点赞为 toggle（赞/取消）：「每用户每条动态只能 1 赞」由 post_likes 唯一约束保证，
    posts.likes 为冗余计数（靠约束防并发双加）；回复支持一层楼中楼（post_replies.parent_id）。
    """

    @staticmethod
    def create(user_id: int, content: str, images: list[str] | None = None) -> int:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO posts (user_id, content, images_json) VALUES (?, ?, ?)",
            (user_id, content, json.dumps(images or [], ensure_ascii=False)),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def get(post_id: int) -> dict | None:
        conn = get_conn()
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def list_feed(viewer_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
        """动态流（含作者信息 + 当前用户是否已赞 + 回复数）。"""
        conn = get_conn()
        rows = conn.execute(
            "SELECT p.*, COALESCE(NULLIF(u.nickname, ''), u.username) AS author_nickname, u.avatar AS author_avatar, "
            "EXISTS(SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = ?) AS liked, "
            "(SELECT COUNT(*) FROM post_replies r WHERE r.post_id = p.id) AS reply_count "
            "FROM posts p JOIN users u ON u.id = p.user_id "
            "ORDER BY p.id DESC LIMIT ? OFFSET ?",
            (viewer_id, limit, offset),
        ).fetchall()
        return [dict(r) | {"liked": bool(r["liked"])} for r in rows]

    @staticmethod
    def get_feed(post_id: int, viewer_id: int) -> dict | None:
        """单条动态详情（同一行形状，供详情页刷新用）。"""
        conn = get_conn()
        row = conn.execute(
            "SELECT p.*, COALESCE(NULLIF(u.nickname, ''), u.username) AS author_nickname, u.avatar AS author_avatar, "
            "EXISTS(SELECT 1 FROM post_likes pl WHERE pl.post_id = p.id AND pl.user_id = ?) AS liked, "
            "(SELECT COUNT(*) FROM post_replies r WHERE r.post_id = p.id) AS reply_count "
            "FROM posts p JOIN users u ON u.id = p.user_id WHERE p.id = ?",
            (viewer_id, post_id),
        ).fetchone()
        return (dict(row) | {"liked": bool(row["liked"])}) if row else None

    @staticmethod
    def count() -> int:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM posts").fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def toggle_like(post_id: int, user_id: int) -> dict:
        """点赞/取消点赞（幂等取反）；唯一约束保证每用户最多 1 赞。"""
        conn = get_conn()
        row = conn.execute(
            "SELECT id FROM post_likes WHERE post_id = ? AND user_id = ?", (post_id, user_id)
        ).fetchone()
        if row:
            conn.execute("DELETE FROM post_likes WHERE id = ?", (row["id"],))
            conn.execute("UPDATE posts SET likes = likes - 1 WHERE id = ? AND likes > 0", (post_id,))
            conn.commit()
            liked = False
        else:
            try:
                conn.execute(
                    "INSERT INTO post_likes (post_id, user_id) VALUES (?, ?)", (post_id, user_id)
                )
                conn.execute("UPDATE posts SET likes = likes + 1 WHERE id = ?", (post_id,))
                conn.commit()
                liked = True
            except Exception as exc:
                # 并发双击：唯一约束冲突 → 视为已赞，绝不双加
                if not _is_unique_violation(exc):
                    raise
                conn.rollback()
                liked = True
        cur = conn.execute("SELECT likes FROM posts WHERE id = ?", (post_id,))
        row = cur.fetchone()
        return {"liked": liked, "likes": int(row["likes"]) if row else 0}

    # -------------------- 回复（一层楼中楼） --------------------

    @staticmethod
    def get_reply(reply_id: int) -> dict | None:
        conn = get_conn()
        row = conn.execute("SELECT * FROM post_replies WHERE id = ?", (reply_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def add_reply(post_id: int, user_id: int, content: str,
                  parent_id: int | None = None, reply_to_user_id: int | None = None) -> int:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO post_replies (post_id, user_id, parent_id, reply_to_user_id, content) "
            "VALUES (?, ?, ?, ?, ?)",
            (post_id, user_id, parent_id, reply_to_user_id, content),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def list_replies(post_id: int, viewer_id: int) -> list[dict]:
        """回复树：一级回复列表，每个含 children（二级回复，带被回复者昵称）。"""
        conn = get_conn()
        rows = conn.execute(
            "SELECT r.*, COALESCE(NULLIF(u.nickname, ''), u.username) AS author_nickname, u.avatar AS author_avatar, "
            "COALESCE(NULLIF(u2.nickname, ''), u2.username, '') AS reply_to_nickname " 
            "FROM post_replies r "
            "JOIN users u ON u.id = r.user_id "
            "LEFT JOIN users u2 ON u2.id = r.reply_to_user_id "
            "WHERE r.post_id = ? ORDER BY r.id ASC",
            (post_id,),
        ).fetchall()
        likes_map = {
            r["reply_id"]: int(r["n"])
            for r in conn.execute(
                "SELECT reply_id, COUNT(*) AS n FROM reply_likes "
                "WHERE reply_id IN (SELECT id FROM post_replies WHERE post_id = ?) GROUP BY reply_id",
                (post_id,),
            ).fetchall()
        }
        liked_set = {
            r["reply_id"]
            for r in conn.execute(
                "SELECT reply_id FROM reply_likes WHERE user_id = ? "
                "AND reply_id IN (SELECT id FROM post_replies WHERE post_id = ?)",
                (viewer_id, post_id),
            ).fetchall()
        }
        first: list[dict] = []
        children: dict[int, list[dict]] = {}
        for r in rows:
            d = {
                "id": r["id"],
                "user_id": r["user_id"],
                "author_nickname": r["author_nickname"],
                "author_avatar": r["author_avatar"],
                "content": r["content"],
                "created_at": r["created_at"],
                "likes": likes_map.get(r["id"], 0),
                "liked": r["id"] in liked_set,
                "reply_to_nickname": r["reply_to_nickname"],
            }
            if r["parent_id"] is None:
                d["children"] = []
                first.append(d)
            else:
                children.setdefault(r["parent_id"], []).append(d)
        for p_id, subs in children.items():
            base = next((x for x in first if x["id"] == p_id), None)
            if base is None:
                continue  # 父回复不存在（异常数据）时丢弃
            for d in subs:
                d["parent_id"] = p_id
            base["children"] = subs
        return first

    @staticmethod
    def toggle_reply_like(reply_id: int, user_id: int) -> dict:
        """回复点赞/取消点赞；reply_likes 唯一约束保证每用户最多 1 赞。"""
        conn = get_conn()
        row = conn.execute(
            "SELECT id FROM reply_likes WHERE reply_id = ? AND user_id = ?", (reply_id, user_id)
        ).fetchone()
        if row:
            conn.execute("DELETE FROM reply_likes WHERE id = ?", (row["id"],))
            conn.commit()
            liked = False
        else:
            try:
                conn.execute(
                    "INSERT INTO reply_likes (reply_id, user_id) VALUES (?, ?)", (reply_id, user_id)
                )
                conn.commit()
                liked = True
            except Exception as exc:
                if not _is_unique_violation(exc):
                    raise
                conn.rollback()
                liked = True
        cur = conn.execute("SELECT COUNT(*) AS n FROM reply_likes WHERE reply_id = ?", (reply_id,))
        return {"liked": liked, "likes": int(cur.fetchone()["n"])}


class CorrectionRepo:
    """内容纠错工单（用户提交，后台审核）。"""

    @staticmethod
    def create(user_id: int, doc_ref: str | None, original_text: str | None,
               corrected_text: str, reason: str | None) -> int:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO corrections (user_id, doc_ref, original_text, corrected_text, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, doc_ref, original_text, corrected_text, reason),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def list_user(user_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM corrections WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get(user_id: int, corr_id: int) -> dict | None:
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM corrections WHERE id = ? AND user_id = ?", (corr_id, user_id)
        ).fetchone()
        return dict(row) if row else None

    # ---------- 管理端（审核工单池） ----------

    @staticmethod
    def get_any(corr_id: int) -> dict | None:
        """按 id 取工单（不限提交者；管理端用）。"""
        conn = get_conn()
        row = conn.execute("SELECT * FROM corrections WHERE id = ?", (corr_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def list_all(status: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
        """工单列表（JOIN 提交者用户名）；status 过滤（pending/accepted/rejected/discussion）。"""
        conn = get_conn()
        where = "WHERE c.status = ?" if status else ""
        params: list = [status] if status else []
        params += [limit, offset]
        rows = conn.execute(
            f"SELECT c.*, u.username, u.nickname FROM corrections c "
            f"LEFT JOIN users u ON u.id = c.user_id {where} "
            f"ORDER BY c.id DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def count_by_status(status: str) -> int:
        conn = get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM corrections WHERE status = ?", (status,)).fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def update(corr_id: int, status: str, admin_reply: str | None = None) -> bool:
        """更新审核状态与管理员回复（status: pending/accepted/rejected/discussion）。"""
        conn = get_conn()
        cur = conn.execute(
            "UPDATE corrections SET status = ?, admin_reply = ? WHERE id = ?",
            (status, admin_reply, corr_id),
        )
        conn.commit()
        return cur.rowcount > 0


class PromptTemplateRepo:
    """管理端 Prompt 模板（RAG 调优中心）；active 至多一个，激活时全表唯一。"""

    @staticmethod
    def list_all() -> list[dict]:
        conn = get_conn()
        rows = conn.execute("SELECT * FROM prompt_templates ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get(tpl_id: int) -> dict | None:
        conn = get_conn()
        row = conn.execute("SELECT * FROM prompt_templates WHERE id = ?", (tpl_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def get_active() -> dict | None:
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM prompt_templates WHERE is_active = 1 ORDER BY id LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def create(name: str, system_prompt: str, description: str | None = None) -> int:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO prompt_templates (name, description, system_prompt, is_active) "
            "VALUES (?, ?, ?, 0)",
            (name, description, system_prompt),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def update(tpl_id: int, name: str | None, system_prompt: str | None,
               description: str | None) -> bool:
        conn = get_conn()
        sets, values = [], []
        if name is not None:
            sets.append("name = ?")
            values.append(name)
        if system_prompt is not None:
            sets.append("system_prompt = ?")
            values.append(system_prompt)
        if description is not None:
            sets.append("description = ?")
            values.append(description)
        if not sets:
            return False
        values.append(tpl_id)
        cur = conn.execute(
            f"UPDATE prompt_templates SET {', '.join(sets)}, "
            f"updated_at = {ts_expr()} WHERE id = ?",
            values,
        )
        conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def delete(tpl_id: int) -> bool:
        conn = get_conn()
        cur = conn.execute("DELETE FROM prompt_templates WHERE id = ?", (tpl_id,))
        conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def set_active(tpl_id: int) -> bool:
        """激活模板（先清全表再置 1，保证唯一 active；幂等）。"""
        conn = get_conn()
        if conn.execute("SELECT id FROM prompt_templates WHERE id = ?", (tpl_id,)).fetchone() is None:
            return False
        conn.execute("UPDATE prompt_templates SET is_active = 0 WHERE is_active = 1")
        conn.execute("UPDATE prompt_templates SET is_active = 1 WHERE id = ?", (tpl_id,))
        conn.commit()
        return True


class RagSettingsRepo:
    """运行时检索参数（key-value）；缺省回退 config 默认，由调用方 merge。"""

    KEYS = ("vector_top_k", "filter_top_k", "rerank_enabled", "blacklist_enabled")

    @staticmethod
    def get_all() -> dict[str, str]:
        conn = get_conn()
        rows = conn.execute("SELECT setting_key, value FROM rag_settings").fetchall()
        return {str(r["setting_key"]): str(r["value"]) for r in rows}

    @staticmethod
    def set_many(values: dict[str, str]) -> None:
        """批量 upsert（UPDATE 不中则 INSERT，跨 sqlite/mysql 无方言差异）。"""
        conn = get_conn()
        for key, value in values.items():
            cur = conn.execute(
                f"UPDATE rag_settings SET value = ?, updated_at = {ts_expr()} WHERE setting_key = ?",
                (value, key),
            )
            if cur.rowcount == 0:
                conn.execute(
                    f"INSERT INTO rag_settings (setting_key, value, updated_at) VALUES (?, ?, {ts_expr()})",
                    (key, value),
                )
        conn.commit()


class RagDictRepo:
    """RAG 词典：同义词（synonym）/ 敏感词（blacklist）。"""

    @staticmethod
    def list_by_type(type_: str) -> list[dict]:
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM rag_dictionary WHERE type = ? ORDER BY id", (type_,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["values"] = json.loads(d.get("values_json") or "[]")
            except (TypeError, ValueError):
                d["values"] = []
            out.append(d)
        return out

    @staticmethod
    def add(type_: str, word: str, values: list[str] | None = None) -> int:
        conn = get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO rag_dictionary (type, word, values_json) VALUES (?, ?, ?)",
                (type_, word, json.dumps(values or [], ensure_ascii=False)),
            )
            conn.commit()
            return int(cur.lastrowid)
        except Exception:
            # 唯一约束冲突等失败：必须回滚，否则事务残留会锁死后续写（database is locked）
            conn.rollback()
            raise

    @staticmethod
    def delete(entry_id: int) -> bool:
        conn = get_conn()
        cur = conn.execute("DELETE FROM rag_dictionary WHERE id = ?", (entry_id,))
        conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def synonyms_groups() -> list[tuple[str, ...]]:
        """同义词组（word + values），供 Retriever 构造注入。"""
        groups = []
        for entry in RagDictRepo.list_by_type("synonym"):
            words = [entry["word"], *entry.get("values", [])]
            if len(words) >= 2:
                groups.append(tuple(words))
        return groups

    @staticmethod
    def blacklist_words() -> list[str]:
        return [str(e["word"]) for e in RagDictRepo.list_by_type("blacklist")]


class NotificationRepo:
    """消息通知中心（系统通知 / 知识库更新通知），用户侧可查可标记已读。"""

    @staticmethod
    def create(user_id: int, type_: str, title: str, content: str | None = None) -> int:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO notifications (user_id, type, title, content) VALUES (?, ?, ?, ?)",
            (user_id, type_, title, content),
        )
        conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def list_user(user_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def unread_count(user_id: int) -> int:
        conn = get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND is_read = 0", (user_id,)
        ).fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def mark_read(user_id: int, ids: list[int] | None = None) -> int:
        conn = get_conn()
        if ids:
            marks = ",".join("?" for _ in ids)
            cur = conn.execute(
                f"UPDATE notifications SET is_read = 1 WHERE user_id = ? AND id IN ({marks})",
                (user_id, *ids),
            )
        else:
            cur = conn.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount
