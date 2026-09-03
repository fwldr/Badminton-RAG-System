"""Agent 评估脚本测试：离线子集跑通（不触网）。"""

from app.agent.graph import BadmintonAgent
from app.core.config import BASE_DIR, get_settings
from app.ingest.embedder import FakeEmbedder
from app.ingest.pipeline import ingest_table
from app.ingest.serializer import ALL_TABLES
from app.ingest.store import VectorStore
from app.rag.retriever import Retriever
from scripts.eval_agent import OfflineAgentLLM, load_golden


def _make_agent(route="multi"):
    settings = get_settings()
    store = VectorStore()
    embedder = FakeEmbedder()
    for table in ALL_TABLES:
        ingest_table(store, embedder, table, settings.processed_data_dir)
    retriever = Retriever(store, embedder, use_bm25=False)
    return BadmintonAgent(retriever, OfflineAgentLLM(route), memory=None)


def test_load_golden():
    items = load_golden(BASE_DIR / "data" / "eval" / "agent_golden.json")
    assert len(items) == 20
    for it in items:
        assert it["expect_routes"]


def test_offline_evaluate_subset():
    golden = [
        {
            "id": "t1",
            "question": "夏天和冬天分别选什么球速",
            "expect_routes": ["multi"],
            "expect_keywords": ["75"],
            "expect_fallback": False,
        }
    ]
    agent = _make_agent("multi")
    # 直接跑 evaluate 内部逻辑（agent 已构建好）
    state = agent.invoke(
        {
            "question": golden[0]["question"],
            "session_id": "t",
            "history": [],
            "contexts": [],
            "sources": [],
            "sub_questions": [],
            "retry_count": 0,
            "trace": [],
        }
    )
    assert state.get("route") == "multi"
    assert "verify" in [t["node"] for t in state.get("trace", [])]
