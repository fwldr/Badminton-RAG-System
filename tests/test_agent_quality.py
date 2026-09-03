"""Agent 版四指标评测测试：FakeJudge + OfflineAgentLLM 离线跑通（不触网）。

覆盖：_context_texts 口径、四指标在 [0,1]、FakeJudge 固定打分、route_averages 分组，
以及 wiki 引入的 `_strict` 口径（上下文展开回原始 record）与 token_efficiency 的区间。
"""

import pytest

from app.core.config import BASE_DIR
from scripts.eval_agent import build_offline_agent, load_golden
from scripts.eval_agent_quality import (
    EXTRA_METRIC_KEYS,
    _context_texts,
    _expanded_records,
    route_averages,
    run_agent_quality,
)
from scripts.ragas_eval import FakeJudge, METRIC_KEYS

GOLDEN_FILE = BASE_DIR / "data" / "eval" / "agent_golden.json"


class _StubStore:
    """按 id 回原文的假 store（只服务 _expanded_records）。"""

    def get(self, collection, ids):
        return [{"id": i, "document": f"text-{i}"} for i in ids if i != "spec_knowledge:404"]


@pytest.fixture(scope="module")
def agent():
    # 离线只建一次：FakeEmbedder + 内存库 + 全表入库 + OfflineAgentLLM("multi")
    return build_offline_agent()


def test_context_texts_filters_empty():
    assert _context_texts({}) == []
    assert _context_texts({"contexts": [{"document": "a"}, {"document": ""}, {}]}) == ["a"]


def test_expanded_records_classic_one_to_one():
    contexts = [{"id": "racket_specs:1", "document": "行1", "metadata": {"品牌": "A"}}]
    texts, spans = _expanded_records(contexts, _StubStore())
    assert texts == ["text-racket_specs:1"]
    assert spans == [(0, 1)]  # classic：一条上下文 = 一行，_strict 与原口径同单位


def test_expanded_records_maps_wiki_section_to_anchored_rows():
    contexts = [
        {"id": "con_spec_knowledge_x_1#row-2", "document": "《拍身重量U数》§4U\n...",
         "metadata": {"entry_title": "拍身重量U数", "records": ["spec_knowledge:2", "spec_knowledge:404"]}},
        {"id": "racket_specs:1", "document": "行1", "metadata": {}},
    ]
    texts, spans = _expanded_records(contexts, _StubStore())
    # 取不到原文的锚点（spec_knowledge:404）不占位
    assert texts == ["text-spec_knowledge:2", "text-racket_specs:1"]
    assert spans == [(0, 1), (1, 2)]


def test_run_agent_quality_offline(agent):
    golden = [g for g in load_golden(GOLDEN_FILE) if g["id"] in {"a01", "a05", "a17"}]
    results = run_agent_quality(golden, online=False, agent=agent, judge=FakeJudge())
    assert len(results) == 3
    for r in results:
        assert r["id"] in {"a01", "a05", "a17"}
        assert r["mode"] == "classic"
        for key in (*METRIC_KEYS, *EXTRA_METRIC_KEYS):
            assert key in r
            assert 0.0 <= r[key] <= 1.0
        # FakeJudge 固定口径：faithfulness=1.0、relevancy=0.9、recall=1.0（有 reference_answer）
        assert r["faithfulness"] == 1.0
        assert r["answer_relevancy"] == 0.9
        assert r["context_recall"] == 1.0
        assert r["num_contexts"] > 0
        assert r["strict_units"] == r["num_contexts"]  # classic 下每条上下文恰好一行
        assert isinstance(r["route"], str)


def test_run_agent_quality_empty_reference_recall_zero(agent):
    golden = [{"id": "x", "question": "测试", "expect_routes": ["multi"], "reference_answer": ""}]
    results = run_agent_quality(golden, online=False, agent=agent, judge=FakeJudge())
    assert results[0]["context_recall"] == 0.0


def test_route_averages_grouping(agent):
    golden = [g for g in load_golden(GOLDEN_FILE) if g["id"] in {"a01", "a17"}]
    results = run_agent_quality(golden, online=False, agent=agent, judge=FakeJudge())
    grouped = route_averages(results)
    assert grouped  # 至少一个 route 分组
    for row in grouped.values():
        assert all(0.0 <= v <= 1.0 for v in row.values())
