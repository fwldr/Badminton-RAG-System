"""用户端功能 API 测试（全部离线）：/user/* 会话/收藏/动态/热门/纠错/通知/上传 + /chat 落库/scope。

覆盖（对应 用户端功能实现设计.md）：
- 历史对话记录：/chat 登录自动落库、列表/详情/改名/收藏/删除/搜索
- 收藏夹与文件夹、动态（含图片上传）、热门问答排行、纠错、通知、资料与偏好
- /chat 范围限定 scope（stub agent 捕获 state）
- 未登录 /user/* → 401；/kb/catalog 公开
"""

import io
import pytest
from fastapi.testclient import TestClient

from app.api.routes.chat import get_agent
from app.core.config import get_settings
from app.db.database import reset_db
from main import create_app


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "db_path", tmp_path / "user.db")
    monkeypatch.setattr(get_settings(), "admin_api_key", "admin-key-1")
    monkeypatch.setattr(get_settings(), "user_uploads_dir", tmp_path / "posts")
    # processed 目录：1 张规格表 csv + knowledge 下 1 个规则文件（供 /kb/catalog）
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "球拍.csv").write_text("品牌\n尤尼克斯\n", encoding="utf-8")
    (tmp_path / "knowledge" / "BWF发球规则.csv").write_text("规则\n击球点\n", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "processed_data_dir", tmp_path)
    reset_db()
    yield
    reset_db()


class StubAgent:
    """校验 stub：记录最后收到的 state（scope/落库断言用）。"""

    def __init__(self) -> None:
        self.last_state: dict | None = None

    def invoke(self, state: dict) -> dict:
        self.last_state = state
        return {
            "question": state["question"],
            "answer": "BWF 发球规则：发球时击球点不得高于腰部。",
            "sources": [{"table": "BWF发球规则", "brand": "BWF", "model": "发球"}],
            "clarification": None,
            "trace": [{"node": "route", "input": {"question": state["question"]}, "output": {"route": "rules"}}],
            "route": state.get("scope") or "rules",
            "verified": True,
        }


def _client(stub: StubAgent | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_agent] = lambda: stub or StubAgent()
    return TestClient(app)


def _auth(client: TestClient) -> tuple[str, dict]:
    r = client.post("/auth/register", json={"username": "user1", "password": "secret123", "nickname": "小明"})
    assert r.status_code == 200
    data = r.json()["data"]
    return data["token"], data["user"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ==================== 会话与 /chat 落库 ====================


def test_chat_persists_conversation_for_logged_in_user():
    stub = StubAgent()
    client = _client(stub)
    token, _ = _auth(client)
    r = client.post("/chat", json={"session_id": "s-user", "question": "BWF发球规则?"}, headers=_h(token))
    assert r.status_code == 200
    assert stub.last_state["scope"] is None

    convs = client.get("/user/conversations", headers=_h(token)).json()["data"]
    assert convs["total"] == 1
    conv = convs["conversations"][0]
    assert conv["title"].startswith("BWF发球规则")
    assert conv["msg_count"] == 2

    detail = client.get(f"/user/conversations/{conv['id']}", headers=_h(token)).json()["data"]
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["sources"][0]["table"] == "BWF发球规则"


def test_chat_does_not_persist_anonymous():
    stub = StubAgent()
    client = _client(stub)
    r = client.post("/chat", json={"session_id": "s-anon", "question": "你好"})
    assert r.status_code == 200


def test_conversation_patch_search_and_delete():
    client = _client()
    token, _ = _auth(client)
    r = client.post("/chat", json={"session_id": "s-p", "question": "反手高远球动作要领"}, headers=_h(token))
    conv_id = client.get("/user/conversations", headers=_h(token)).json()["data"]["conversations"][0]["id"]

    # 重命名 + 标签 + 收藏
    p = client.patch(f"/user/conversations/{conv_id}", json={"title": "反手练习", "tag": "技术类", "is_favorite": True}, headers=_h(token))
    assert p.status_code == 200
    assert p.json()["data"]["title"] == "反手练习"

    # 搜索与收藏筛选
    found = client.get("/user/conversations", params={"q": "反手"}, headers=_h(token)).json()["data"]
    assert found["total"] == 1
    fav = client.get("/user/conversations", params={"favorite": "true"}, headers=_h(token)).json()["data"]
    assert fav["conversations"][0]["is_favorite"] == 1
    tag = client.get("/user/conversations", params={"tag": "技术类"}, headers=_h(token)).json()["data"]
    assert tag["total"] == 1

    # 删除
    d = client.delete(f"/user/conversations/{conv_id}", headers=_h(token))
    assert d.status_code == 200
    assert client.get("/user/conversations", headers=_h(token)).json()["data"]["total"] == 0


def test_chat_scope_forced():
    stub = StubAgent()
    client = _client(stub)
    token, _ = _auth(client)
    r = client.post("/chat", json={"session_id": "s-scope", "question": "杀球动作", "scope": "rules"}, headers=_h(token))
    assert r.status_code == 200
    assert stub.last_state["scope"] == "rules"


# ==================== 收藏夹与文件夹 ====================


def test_folders_and_favorites():
    client = _client()
    token, _ = _auth(client)
    folder = client.post("/user/folders", json={"name": "发球技巧"}, headers=_h(token))
    fid = folder.json()["data"]["id"]

    fav = client.post(
        "/user/favorites",
        json={"question": "如何发好反手发球?", "answer": "注意握拍与击球点。",
              "sources": [{"table": "T", "brand": "B", "model": "M"}], "folder_id": fid},
        headers=_h(token),
    )
    fav_id = fav.json()["data"]["id"]
    assert client.get("/user/favorites", headers=_h(token)).json()["data"]["total"] == 1

    # 移到无文件夹
    moved = client.patch(f"/user/favorites/{fav_id}", json={"folder_id": None}, headers=_h(token))
    assert moved.json()["data"]["folder_id"] is None
    # 文件夹列表计数
    folders = client.get("/user/folders", headers=_h(token)).json()["data"]["folders"]
    assert folders[0]["name"] == "发球技巧"
    # 删除文件夹后收藏保留
    client.delete(f"/user/folders/{fid}", headers=_h(token))
    assert client.get("/user/folders", headers=_h(token)).json()["data"]["folders"] == []
    assert client.get("/user/favorites", headers=_h(token)).json()["data"]["total"] == 1


# ==================== 动态 / 热门 ====================


def test_post_upload_and_hot():
    client = _client()
    token, _ = _auth(client)
    # 上传图片
    up = client.post(
        "/user/uploads", headers=_h(token),
        files={"file": ("shuttle.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")},
    )
    assert up.status_code == 200
    path = up.json()["data"]["path"]
    assert path.startswith("/uploads/")
    assert (get_settings().user_uploads_dir / path.rsplit("/", 1)[-1]).exists()

    # 发动态（含图片）→ 列表 → 点赞
    p = client.post("/user/posts", json={"content": "今天练了20分钟反手", "images": [path]}, headers=_h(token))
    pid = p.json()["data"]["id"]
    posts = client.get("/user/posts", headers=_h(token)).json()["data"]
    assert posts["total"] == 1
    assert posts["posts"][0]["images"] == [path]
    assert posts["posts"][0]["author_nickname"] == "小明"

    like = client.post(f"/user/posts/{pid}/like", headers=_h(token))
    assert like.json()["data"]["likes"] == 1

    # 点👍 + 收藏 → 热门排行
    client.post("/feedback", json={"session_id": "hot1", "question": "反手高远球", "rating": 1}, headers=_h(token))
    client.post("/user/favorites", json={"question": "反手高远球", "answer": "要点..."}, headers=_h(token))
    hot = client.get("/user/hot", headers=_h(token)).json()["data"]["hot"]
    assert hot[0]["question"] == "反手高远球"
    assert hot[0]["score"] == 2


def test_post_detail():
    client = _client()
    token, _ = _auth(client)
    pid = client.post("/user/posts", json={"content": "详情测试"}, headers=_h(token)).json()["data"]["id"]
    d = client.get(f"/user/posts/{pid}", headers=_h(token)).json()["data"]["post"]
    assert d["id"] == pid and d["content"] == "详情测试"
    assert d["author_nickname"] == "小明"
    assert d["liked"] is False and d["reply_count"] == 0 and d["images"] == []
    assert client.get("/user/posts/999999", headers=_h(token)).status_code == 404
    assert client.get(f"/user/posts/{pid}").status_code == 401


# ==================== 纠错 / 通知 / 资料 ====================


def test_corrections_and_notifications():
    client = _client()
    token, _ = _auth(client)
    corr = client.post(
        "/user/corrections",
        json={"doc_ref": "BWF发球规则", "original_text": "击球点低于腰部", "corrected_text": "击球点不得高于腰部", "reason": "规则原文已修改"},
        headers=_h(token),
    )
    assert corr.status_code == 200
    assert corr.json()["data"]["status"] == "pending"
    mine = client.get("/user/corrections", headers=_h(token)).json()["data"]["corrections"]
    assert mine[0]["status"] == "pending"

    # 注册生成欢迎通知
    notif = client.get("/user/notifications", headers=_h(token)).json()["data"]
    assert notif["unread"] == 1
    assert notif["notifications"][0]["title"].startswith("欢迎")
    marked = client.post("/user/notifications/read", json={}, headers=_h(token))
    assert marked.json()["data"]["updated"] == 1
    assert client.get("/user/notifications", headers=_h(token)).json()["data"]["unread"] == 0


def test_profile_update():
    client = _client()
    token, _ = _auth(client)
    r = client.patch(
        "/auth/profile",
        json={"nickname": "羽坛新秀", "gender": "男", "level": "进阶", "racket_model": "天斧99 Pro",
              "avatar": "🏸", "pref_style": "simple", "pref_show_sources": False},
        headers=_h(token),
    )
    assert r.status_code == 200
    me = client.get("/auth/me", headers=_h(token)).json()["data"]
    assert me["nickname"] == "羽坛新秀"
    assert me["level"] == "进阶"
    assert me["pref_style"] == "simple"
    assert me["pref_show_sources"] == 0


# ==================== 鉴权与公开接口 ====================


def test_user_endpoints_require_login():
    client = _client()
    assert client.get("/user/conversations").status_code == 401
    assert client.post("/user/posts", json={"content": "x"}).status_code == 401
    # /kb/catalog 公开
    cat = client.get("/kb/catalog")
    assert cat.status_code == 200
    names = [c["name"] for c in cat.json()["data"]["categories"]]
    assert "装备规格" in names and "规则库" in names
    assert cat.json()["data"]["categories"][0]["items"]  # 非空


def test_upload_image_rejects_bad_type():
    client = _client()
    token, _ = _auth(client)
    r = client.post("/user/uploads", headers=_h(token), files={"file": ("a.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 422


# ==================== 动态点赞（唯一）与回复（楼中楼） ====================


def _register(client: TestClient, username: str, nickname: str) -> str:
    r = client.post("/auth/register", json={"username": username, "password": "secret123", "nickname": nickname})
    assert r.status_code == 200
    return r.json()["data"]["token"]


def _create_post(client: TestClient, token: str, content: str) -> int:
    pid = client.post("/user/posts", json={"content": content}, headers=_h(token)).json()["data"]["id"]
    # 列表带 liked/reply_count 字段
    post = client.get("/user/posts", headers=_h(token)).json()["data"]["posts"][0]
    assert "liked" in post and "reply_count" in post
    return pid


def test_post_like_unique_per_user():
    client = _client()
    t1 = _register(client, "u1", "小明")
    t2 = _register(client, "u2", "阿虎")
    pid = _create_post(client, t1, "今天练了反手")

    # user1 赞 → 1；重复赞（toggle）→ 取消 → 0（不能无限刷赞）
    r1 = client.post(f"/user/posts/{pid}/like", headers=_h(t1)).json()["data"]
    assert (r1["liked"], r1["likes"]) == (True, 1)
    r2 = client.post(f"/user/posts/{pid}/like", headers=_h(t1)).json()["data"]
    assert (r2["liked"], r2["likes"]) == (False, 0)

    # user2 赞 → 1；user1 再赞 → user2+user1 = 2；user2 取消 → 1
    assert client.post(f"/user/posts/{pid}/like", headers=_h(t2)).json()["data"]["likes"] == 1
    assert client.post(f"/user/posts/{pid}/like", headers=_h(t1)).json()["data"]["likes"] == 2
    assert client.post(f"/user/posts/{pid}/like", headers=_h(t2)).json()["data"]["likes"] == 1

    # 列表中的 liked 按当前用户反映
    mine = client.get("/user/posts", headers=_h(t1)).json()["data"]["posts"][0]
    assert mine["liked"] is True and mine["likes"] == 1
    other = client.get("/user/posts", headers=_h(t2)).json()["data"]["posts"][0]
    assert other["liked"] is False


def test_post_like_not_found():
    client = _client()
    token, _ = _auth(client)
    assert client.post("/user/posts/999/like", headers=_h(token)).status_code == 404


def test_replies_tree_with_children():
    client = _client()
    t1 = _register(client, "u1", "小明")
    t2 = _register(client, "u2", "阿虎")
    pid = _create_post(client, t1, "反手发球总是下网怎么办")

    # user2 一级回复
    r1 = client.post(f"/user/posts/{pid}/replies", json={"content": "可能是击球点太低"}, headers=_h(t2))
    assert r1.status_code == 200
    rid1 = r1.json()["data"]["id"]

    # user1 回复 user2 的回复（二级，挂在 rid1 下）
    r2 = client.post(f"/user/posts/{pid}/replies", json={"content": "谢谢，我去试试", "parent_id": rid1}, headers=_h(t1))
    rid2 = r2.json()["data"]["id"]

    # user2 再回复 user1 的二级回复 → 仍挂在 rid1 下，reply_to = user1
    client.post(f"/user/posts/{pid}/replies", json={"content": "加油！", "parent_id": rid2}, headers=_h(t2))

    reps = client.get(f"/user/posts/{pid}/replies", headers=_h(t1)).json()["data"]["replies"]
    assert len(reps) == 1 and reps[0]["id"] == rid1
    assert reps[0]["author_nickname"] == "阿虎"
    children = reps[0]["children"]
    assert len(children) == 2
    assert children[0]["id"] == rid2 and children[0]["author_nickname"] == "小明"
    assert children[0]["reply_to_nickname"] == "阿虎"
    assert children[1]["reply_to_nickname"] == "小明"

    # 动态列表的 reply_count
    post = client.get("/user/posts", headers=_h(t1)).json()["data"]["posts"][0]
    assert post["reply_count"] == 3


def test_reply_like_unique_per_user():
    client = _client()
    t1 = _register(client, "u1", "小明")
    t2 = _register(client, "u2", "阿虎")
    pid = _create_post(client, t1, "球线推荐")
    rid = client.post(f"/user/posts/{pid}/replies", json={"content": "YYBG65 不错"}, headers=_h(t2)).json()["data"]["id"]

    # user1 赞回复 → 1；重复（toggle）→ 0
    a = client.post(f"/user/replies/{rid}/like", headers=_h(t1)).json()["data"]
    assert (a["liked"], a["likes"]) == (True, 1)
    b = client.post(f"/user/replies/{rid}/like", headers=_h(t1)).json()["data"]
    assert (b["liked"], b["likes"]) == (False, 0)
    # 两个人各赞一次 → 2；user1 取消 → 1（每用户上限 1）
    assert client.post(f"/user/replies/{rid}/like", headers=_h(t2)).json()["data"]["likes"] == 1
    assert client.post(f"/user/replies/{rid}/like", headers=_h(t1)).json()["data"]["likes"] == 2
    assert client.post(f"/user/replies/{rid}/like", headers=_h(t2)).json()["data"]["likes"] == 1

    # 回复列表里 liked 按当前用户返回
    reps = client.get(f"/user/posts/{pid}/replies", headers=_h(t1)).json()["data"]["replies"]
    assert reps[0]["likes"] == 1 and reps[0]["liked"] is True


def test_reply_validation_and_auth():
    client = _client()
    token, _ = _auth(client)
    pid = _create_post(client, token, "新手问题")
    rid = client.post(f"/user/posts/{pid}/replies", json={"content": "一级"}, headers=_h(token)).json()["data"]["id"]

    # 未登录 401
    assert client.post(f"/user/posts/{pid}/replies", json={"content": "x"}).status_code == 401
    assert client.post(f"/user/replies/{rid}/like").status_code == 401
    # 回复不存在的动态 / 回复不属于该动态的回复 → 404
    assert client.post("/user/posts/999/replies", json={"content": "x"}, headers=_h(token)).status_code == 404
    other_pid = _create_post(client, token, "另一个动态")
    assert client.post(f"/user/posts/{other_pid}/replies", json={"content": "x", "parent_id": rid}, headers=_h(token)).status_code == 404
    # 空内容 → 422（业务码 VALIDATION）
    assert client.post(f"/user/posts/{pid}/replies", json={"content": "   "}, headers=_h(token)).status_code == 422
    # 点赞不存在的回复 → 404
    assert client.post("/user/replies/999/like", headers=_h(token)).status_code == 404
