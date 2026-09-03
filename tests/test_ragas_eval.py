"""RAGAS 四指标评测测试：FakeJudge + 内存库跑通（不触网）。

覆盖：四指标对 FakeJudge 固定打分的解析、空输入守卫、均值计算、离线端到端跑通。
"""

import pytest

from app.core.config import BASE_DIR
from scripts.eval_ask import build_offline_retriever, load_golden
from scripts.ragas_eval import (
    FakeJudge,
    average_metrics,
    judge_answer_relevancy,
    judge_context_precision,
    judge_context_recall,
    judge_faithfulness,
    run_ragas,
)

GOLDEN_FILE = BASE_DIR / "data" / "eval" / "golden.json"


@pytest.fixture(scope="module")
def judge():
    return FakeJudge()


@pytest.fixture(scope="module")
def golden():
    return load_golden(GOLDEN_FILE)


@pytest.fixture(scope="module")
def retriever():
    # 离线环境只建一次：FakeEmbedder + 内存库，灌入真实 processed 数据
    return build_offline_retriever()


def test_judge_faithfulness_fixed_score(judge):
    # FakeJudge：回答拆成 2 条主张、全部能支撑 → 1.0
    score = judge_faithfulness(judge, "回答示例。第二句。", ["上下文甲", "上下文乙"])
    assert score == 1.0


def test_judge_answer_relevancy_fixed_score(judge):
    assert judge_answer_relevancy(judge, "问题", "回答") == 0.9


def test_judge_context_precision_fixed_score(judge):
    # FakeJudge：3 条上下文中奇数位相关 → 2/3
    score = judge_context_precision(judge, "问题", ["a", "b", "c"])
    assert round(score, 4) == 0.6667


def test_judge_context_recall_fixed_score(judge):
    # FakeJudge：标准答案拆成 2 条主张、全部出现 → 1.0
    score = judge_context_recall(judge, "标准答案。第二句。", ["上下文甲", "上下文乙"])
    assert score == 1.0


def test_empty_input_guards(judge):
    # 空回答 / 空上下文 / 空标准答案 → 0.0（避免除零）
    assert judge_faithfulness(judge, "", ["ctx"]) == 0.0
    assert judge_context_precision(judge, "q", []) == 0.0
    assert judge_context_recall(judge, "", ["ctx"]) == 0.0
    assert judge_context_recall(judge, "参考答案", []) == 0.0


def test_scores_bounded(judge):
    # 四指标分数恒在 [0,1]
    for score in (
        judge_faithfulness(judge, "回答", ["ctx"]),
        judge_answer_relevancy(judge, "q", "回答"),
        judge_context_precision(judge, "q", ["a", "b"]),
        judge_context_recall(judge, "参考答案", ["ctx"]),
    ):
        assert 0.0 <= score <= 1.0


def test_average_metrics():
    results = [
        {
            "faithfulness": 1.0,
            "answer_relevancy": 0.9,
            "context_precision": 0.5,
            "context_recall": 1.0,
        },
        {
            "faithfulness": 0.5,
            "answer_relevancy": 0.8,
            "context_precision": 0.25,
            "context_recall": 0.5,
        },
    ]
    avg = average_metrics(results)
    assert avg["faithfulness"] == 0.75
    assert avg["answer_relevancy"] == 0.85
    assert avg["context_precision"] == 0.375
    assert avg["context_recall"] == 0.75
    assert average_metrics([]) == {
        "faithfulness": 0.0,
        "answer_relevancy": 0.0,
        "context_precision": 0.0,
        "context_recall": 0.0,
    }


def test_run_ragas_offline_end_to_end(golden, judge, retriever):
    # 2 正例 + 2 负例，离线端到端跑通：FakeJudge 固定打分，正/负例口径分界
    subset = [g for g in golden if g["id"] in {"q01", "q06", "q11", "q12"}]
    results = run_ragas(subset, retriever=retriever, judge=judge)
    assert len(results) == len(subset)
    for r in results:
        assert r["id"] in {"q01", "q06", "q11", "q12"}
        for key in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
            assert key in r
            assert 0.0 <= r[key] <= 1.0
        assert isinstance(r["answer"], str)
        assert r["num_contexts"] > 0
    # FakeJudge 固定打分：faithfulness/relevancy 恒定；recall 正例 1.0、负例（空标准答案）0.0
    assert all(r["faithfulness"] == 1.0 for r in results)
    assert all(r["answer_relevancy"] == 0.9 for r in results)
    for r in results:
        if r["id"] in {"q01", "q06"}:
            assert r["context_recall"] == 1.0
        else:
            assert r["context_recall"] == 0.0
    avg = average_metrics(results)
    assert all(0.0 <= v <= 1.0 for v in avg.values())
    assert len(avg) == 4
