"""RAG 调优中心测试：运行时参数 / Prompt 模板 / 词典 / 沙箱（全离线，stub LLM + 内存库）。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.routes.chat import get_agent
from app.core.config import get_settings
from app.core.security import create_token, hash_password
from app.db.database import init_db, reset_db
from app.db.repos import (
    PromptTemplateRepo,
    RagDictRepo,
    RagSettingsRepo,
    UserRepo,
)
from app.ingest.embedder import FakeEmbedder
from app.ingest.store import VectorStore
from app.rag.llm import LLMClient
from app.rag.retriever import Retriever
from main import create_app


class StubLLM(LLMClient):
    """可控 LLM：路由/过滤/生成走 JSON（debug 沙箱与 build 共用）。"""

    def __init__(self) -> None:
        pass  # 不初始化 OpenAI 客户端（测试不触网）

    def complete(self, messages, *, json_mode=False) -> str:
        system = messages[0]["content"]
        if "路由助手" in system:
            return '{"route": "equipment"}'
        if "羽毛球装备检索助手" in system:
            return "{}"
        if "回答校验员" in system:
            return '{"supported": true}'
        # 生成节点（沙箱 with_answer / graph）
        return '{"answer": "沙箱生成：推荐 4U 球拍。来源：球拍 测试拍", "used": [1]}'


class StubAgent:
    """debug 端点依赖：复用真实 Retriever（FakeEmbedder + 内存库）+ StubLLM。"""

    def __init__(self) -> None:
        store = VectorStore()
        embedder = FakeEmbedder()
        store.add("racket_specs", ["racket_specs:0"], ["品牌:李宁 型号:GP203 拍身重量(U):4U"],
                  [{"品牌": "李宁", "型号": "GP203", "拍身重量(U)": "4U"}],
                  embedder.embed(["品牌:李宁 型号:GP203 拍身重量(U):4U"]))
        self._retriever = Retriever(store, embedder)
        self._llm = StubLLM()
        self._vision_embed = None
        self._generate_system = None


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "db_path", tmp_path / "admin.db")
    monkeypatch.setattr(get_settings(), "admin_api_key", "admin-key-1")
    reset_db()
    init_db()  # 建表 + 播种预置 Prompt 模板（lifespan 不自动运行，测试显式初始化）
    yield
    reset_db()


def _admin_headers(permissions=None) -> dict:
    existing = UserRepo.get_by_username("qa_admin")
    uid = existing["id"] if existing else UserRepo.create("qa_admin", hash_password("pw"), role="admin")
    if permissions is not None:
        UserRepo.set_permissions(uid, json.dumps(permissions, ensure_ascii=False))
    s = get_settings()
    token = create_token({"sub": str(uid), "role": "admin"}, s.auth_token_secret, 3600)
    return {"Authorization": f"Bearer {token}"}


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_agent] = lambda: StubAgent()
    return TestClient(app)


# -------------------- 运行时参数 --------------------


def test_rag_settings_roundtrip():
    client = _client()
    h = _admin_headers()
    data = client.get("/admin/rag/settings", headers=h).json()["data"]
    assert data["settings"]["vector_top_k"] == "10"  # 默认合并
    resp = client.put("/admin/rag/settings", headers=h,
                      json={"vector_top_k": 12, "blacklist_enabled": True})
    assert resp.status_code == 200
    assert resp.json()["data"]["updated"]["vector_top_k"] == "12"
    # 持久化重读
    assert RagSettingsRepo.get_all()["vector_top_k"] == "12"
    data2 = client.get("/admin/rag/settings", headers=h).json()["data"]
    assert data2["settings"]["vector_top_k"] == "12"
    assert data2["settings"]["blacklist_enabled"] == "true"


def test_rag_settings_invalid_value_422():
    client = _client()
    resp = client.put("/admin/rag/settings", headers=_admin_headers(), json={"vector_top_k": 0})
    assert resp.status_code == 422


# -------------------- Prompt 模板 --------------------


def test_prompt_templates_crud_and_activate():
    client = _client()
    h = _admin_headers()
    seeded = client.get("/admin/rag/prompts", headers=h).json()["data"]["templates"]
    assert len(seeded) >= 3  # 种子模板（幂等播种）
    resp = client.post("/admin/rag/prompts", headers=h, json={
        "name": "教练员2", "system_prompt": "你是教练，只输出 JSON。", "description": "测试",
    })
    tpl_id = resp.json()["data"]["id"]
    assert PromptTemplateRepo.get_active() is None

    act = client.post(f"/admin/rag/prompts/{tpl_id}/activate", headers=h)
    assert act.status_code == 200
    active = PromptTemplateRepo.get_active()
    assert active["name"] == "教练员2"
    assert active["system_prompt"] == "你是教练，只输出 JSON。"

    # 激活另一个 → 唯一 active 保持
    other = seeded[0]["id"]
    client.post(f"/admin/rag/prompts/{other}/activate", headers=h)
    assert (PromptTemplateRepo.get_active() or {})["id"] == other

    upd = client.put(f"/admin/rag/prompts/{tpl_id}", headers=h, json={"name": "教练员3"})
    assert upd.status_code == 200
    assert PromptTemplateRepo.get(tpl_id)["name"] == "教练员3"

    dele = client.delete(f"/admin/rag/prompts/{tpl_id}", headers=h)
    assert dele.status_code == 200
    assert PromptTemplateRepo.get(tpl_id) is None
    assert client.delete(f"/admin/rag/prompts/99999", headers=h).status_code == 404


# -------------------- 词典（同义词/敏感词） --------------------


def test_synonyms_and_blacklist_crud():
    client = _client()
    h = _admin_headers()
    resp = client.post("/admin/rag/synonyms", headers=h,
                       json={"word": "高远球", "values": ["高球", "后场球"]})
    assert resp.status_code == 200
    entry_id = resp.json()["data"]["id"]
    items = client.get("/admin/rag/synonyms", headers=h).json()["data"]["items"]
    assert items[0]["values"] == ["高球", "后场球"]
    # 词表集成：Retriever 构造可注入（synonyms_groups 供 build 用）
    groups = RagDictRepo.synonyms_groups()
    assert ("高远球", "高球", "后场球") in groups
    # 重复词 → 422
    dup = client.post("/admin/rag/synonyms", headers=h, json={"word": "高远球", "values": []})
    assert dup.status_code == 422

    assert client.delete(f"/admin/rag/synonyms/{entry_id}", headers=h).status_code == 200
    assert client.get("/admin/rag/synonyms", headers=h).json()["data"]["items"] == []

    bl = client.post("/admin/rag/blacklist", headers=h, json={"word": "赌博"})
    assert bl.status_code == 200
    assert RagDictRepo.blacklist_words() == ["赌博"]


# -------------------- 沙箱 --------------------


def test_rag_debug_pipeline_replay():
    client = _client()
    resp = client.post("/admin/rag/debug", headers=_admin_headers(),
                       json={"question": "推荐4U球拍", "with_answer": True})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["question"] == "推荐4U球拍"
    assert data["route"] == "equipment"
    assert data["expanded_queries"][0] == "推荐4U球拍"
    assert data["candidates"] and data["candidates"][0]["table"] == "racket_specs"
    assert data["candidates"][0]["score"] is not None
    assert "推荐 4U" in data["answer"]
    assert data["context_block"]


def test_rag_debug_without_answer_skips_llm():
    client = _client()
    resp = client.post("/admin/rag/debug", headers=_admin_headers(),
                       json={"question": "推荐4U球拍", "with_answer": False})
    data = resp.json()["data"]
    assert data["answer"] is None
    assert data["candidates"]
