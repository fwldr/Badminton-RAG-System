"""golden set 离线评估测试：对 golden.json 子集跑通并断言（不触网）。

覆盖：golden 结构（30 题 + 每题 reference_answer）、reference_answer 校验、
repeat 语义（正例多数次 PASS / 负例全部次 PASS）、per_try 明细。
"""

import json

import pytest

from app.core.config import BASE_DIR
from scripts.eval_ask import (
    aggregate_try_results,
    build_offline_retriever,
    evaluate,
    load_golden,
    pass_rate,
)

GOLDEN_FILE = BASE_DIR / "data" / "eval" / "golden.json"


@pytest.fixture(scope="module")
def golden():
    return load_golden(GOLDEN_FILE)


@pytest.fixture(scope="module")
def retriever():
    # 离线环境只建一次：FakeEmbedder + 内存库，灌入真实 processed 数据
    return build_offline_retriever()


def _try(passed: bool) -> dict:
    """构造一条 per_try 明细（三项子检查与 passed 一致，简化聚合断言）。"""
    return {
        "fallback_ok": passed,
        "collections_ok": passed,
        "keywords_ok": passed,
        "hit_collections": [],
        "answer": "x" if passed else "",
        "passed": passed,
    }


def test_golden_has_required_shape(golden):
    assert len(golden) == 30
    for item in golden:
        assert item["id"].startswith("q")
        assert isinstance(item["expect_collections"], list)
        assert isinstance(item["expect_keywords"], list)
        assert isinstance(item["expect_fallback"], bool)
        assert "reference_answer" in item and isinstance(item["reference_answer"], str)
        assert "note" in item


def test_golden_covers_expected_questions(golden):
    ids = {item["id"] for item in golden}
    assert {"q01", "q07", "q11", "q12", "q30"} <= ids
    assert len(ids) == 30


def test_load_golden_requires_reference_answer(tmp_path):
    bad = tmp_path / "golden_bad.json"
    bad.write_text(
        json.dumps(
            [
                {
                    "id": "q01",
                    "question": "测试题",
                    "expect_collections": [],
                    "expect_keywords": [],
                    "expect_fallback": False,
                    "note": "缺 reference_answer",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="reference_answer"):
        load_golden(bad)


def test_evaluate_requires_positive_repeat(golden):
    with pytest.raises(ValueError, match="repeat"):
        evaluate(golden, retriever=None, repeat=0)


def test_repeat_positive_majority_passes():
    # 正例：3 次里 2 次通过 → 多数 PASS；1 次通过 → FAIL
    assert aggregate_try_results(
        [_try(True), _try(True), _try(False)], expect_fallback=False
    )["passed"]
    assert not aggregate_try_results(
        [_try(True), _try(False), _try(False)], expect_fallback=False
    )["passed"]
    # 单次（repeat=1）语义：1 次通过即 PASS
    assert aggregate_try_results([_try(True)], expect_fallback=False)["passed"]
    assert not aggregate_try_results([_try(False)], expect_fallback=False)["passed"]


def test_repeat_negative_requires_all_passes():
    # 负例：任何一次没兜底都算 FAIL，必须全部次 PASS（避免「3 次里 1 次兜底算过」）
    assert aggregate_try_results(
        [_try(True), _try(True), _try(True)], expect_fallback=True
    )["passed"]
    assert not aggregate_try_results(
        [_try(True), _try(True), _try(False)], expect_fallback=True
    )["passed"]
    assert not aggregate_try_results(
        [_try(False), _try(True), _try(True)], expect_fallback=True
    )["passed"]


def test_aggregate_reports_try_passed_count():
    agg = aggregate_try_results([_try(True), _try(False), _try(True)], expect_fallback=False)
    assert agg["try_passed"] == 2
    assert agg["repeat"] == 3


def test_offline_eval_subset_runs_and_returns_results(golden, retriever):
    # 子集：2 个正例 + 2 个负例，离线跑通（repeat=3 记录 per_try 明细）
    subset = [g for g in golden if g["id"] in {"q07", "q10", "q11", "q12"}]
    results = evaluate(subset, retriever=retriever, repeat=3)
    assert len(results) == len(subset)
    for r in results:
        assert r["id"] in {"q07", "q10", "q11", "q12"}
        for key in (
            "fallback_ok",
            "collections_ok",
            "keywords_ok",
            "hit_collections",
            "passed",
            "repeat",
            "try_passed",
            "per_try",
        ):
            assert key in r
        assert isinstance(r["passed"], bool)
        assert r["repeat"] == 3
        assert len(r["per_try"]) == 3
        for t in r["per_try"]:
            for key in (
                "fallback_ok",
                "collections_ok",
                "keywords_ok",
                "hit_collections",
                "answer",
                "passed",
            ):
                assert key in t
        assert 0 <= r["try_passed"] <= 3
    rate = pass_rate(results)
    assert 0.0 <= rate <= 1.0


def test_repeat_one_is_single_shot(golden, retriever):
    # repeat=1 保持旧单次语义：per_try 只有 1 条，结果结构与通过率口径不变
    subset = [g for g in golden if g["id"] in {"q07", "q11"}]
    results = evaluate(subset, retriever=retriever, repeat=1)
    for r in results:
        assert r["repeat"] == 1
        assert len(r["per_try"]) == 1


def test_negative_cases_always_fall_back(golden, retriever):
    # 负例：期望兜底且无期望表，离线链路必然兜底 → 三项全过，且 repeat 下全部次 PASS
    subset = [g for g in golden if g["id"] in {"q11", "q12"}]
    results = evaluate(subset, retriever=retriever, repeat=3)
    assert len(results) == 2
    for r in results:
        assert r["fallback_ok"] is True
        assert r["collections_ok"] is True
        assert r["keywords_ok"] is True
        assert r["try_passed"] == 3
        assert r["passed"], f"{r['id']} 负例应通过：{r}"
