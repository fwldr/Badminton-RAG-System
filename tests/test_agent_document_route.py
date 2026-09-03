"""Agent document 路由端到端测试（离线：stub LLM + FakeEmbedder + 内存库）。"""

from app.agent.graph import BadmintonAgent
from app.ingest.doc_ingest import ingest_document
from app.ingest.embedder import FakeEmbedder
from app.ingest.ocr import FakeOcrEngine
from app.ingest.store import VectorStore
from app.ingest.vision_embed import FakeVisionEmbedder
from app.rag.llm import LLMClient
from app.rag.retriever import Retriever


class StubLLM(LLMClient):
    """可控回答的 stub：路由/生成/校验全走 JSON。"""

    def __init__(self) -> None:
        self.answered = 0

    def complete(self, messages, *, json_mode=False) -> str:
        sysp = messages[0]["content"]
        if "路由助手" in sysp:
            return '{"route": "document"}'
        if "回答校验员" in sysp:
            return '{"supported": true}'
        self.answered += 1
        return '{"answer": "根据上传的规则手册，发球时击球点不得高于腰部。来源：上传文档 发球规则.md", "used": [1]}'


def _make_agent() -> tuple[BadmintonAgent, VectorStore, StubLLM]:
    store = VectorStore()
    embedder = FakeEmbedder()
    ingest_document(
        "发球规则：发球时击球点不得高于腰部，双脚不得移动。".encode("utf-8"),
        "发球规则.md", 1, store, embedder,
    )
    llm = StubLLM()
    retriever = Retriever(store, embedder)
    agent = BadmintonAgent(retriever=retriever, llm=llm, use_verifier=False)
    return agent, store, llm


def test_agent_document_route_answers_from_doc():
    agent, store, llm = _make_agent()
    result = agent.invoke(
        {
            "question": "上传的规则手册里发球怎么判",
            "history": [],
            "session_id": "s1",
        }
    )
    assert result["route"] == "document"
    assert llm.answered >= 1
    assert "发球" in result.get("answer", "")
    assert result.get("sources")


def test_agent_document_route_no_docs_falls_back():
    """库中无文档 collection 时，document 路由检索为空 → 兜底文案。"""
    store = VectorStore()
    embedder = FakeEmbedder()
    llm = StubLLM()
    retriever = Retriever(store, embedder)
    agent = BadmintonAgent(retriever=retriever, llm=llm, use_verifier=False)
    result = agent.invoke(
        {"question": "这个文档里写了什么", "history": [], "session_id": "s2"}
    )
    assert result["route"] == "document"
    assert "暂无相关信息" in result.get("answer", "")


def test_agent_document_route_collects_images(tmp_path):
    """图片文档（img_*）命中 → generate 返回 images [{url, title}]（供前端内联展示）。"""
    store = VectorStore()
    embedder = FakeEmbedder()
    ingest_document(
        b"\x89PNG\r\n\x1a\n" + b"0" * 64, "握拍姿势.png", None, store, embedder,
        ocr=FakeOcrEngine(""), vision_embed=FakeVisionEmbedder(dim=4096),
        ocr_min_chars=20, image_dir=tmp_path,
    )
    llm = StubLLM()
    retriever = Retriever(store, embedder)
    agent = BadmintonAgent(
        retriever=retriever, llm=llm, use_verifier=False,
        vision_embed=FakeVisionEmbedder(dim=4096),
    )
    result = agent.invoke(
        {"question": "握拍姿势图", "history": [], "session_id": "s3"}
    )
    assert result["route"] == "document"
    assert llm.answered >= 1
    images = result.get("images") or []
    assert images
    assert images[0]["url"].startswith("/uploads/docs/")
    assert "握拍姿势" in images[0]["title"]
