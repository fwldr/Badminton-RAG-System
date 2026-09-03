"""TokenCounter 单元测试：按 route 聚合、坏值忽略、空报表。"""

from app.observability.usage import TokenCounter


def test_counter_add_and_report():
    c = TokenCounter()
    c.add("equipment", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    c.add("equipment", {"prompt_tokens": 20, "total_tokens": 30})
    c.add("multi", {"total_tokens": 9})
    rows = c.report()
    assert len(rows) == 2
    eq = [r for r in rows if r["route"] == "equipment"][0]
    assert eq["prompt_tokens"] == 30
    assert eq["completion_tokens"] == 5
    assert eq["total_tokens"] == 45
    assert eq["calls"] == 2
    multi = [r for r in rows if r["route"] == "multi"][0]
    assert multi["total_tokens"] == 9
    assert multi["calls"] == 1


def test_counter_empty_report():
    assert TokenCounter().report() == []


def test_counter_ignores_bad_usage():
    c = TokenCounter()
    c.add("x", None)
    c.add("x", {"total_tokens": "abc"})  # 坏值忽略字段，但调用次数仍计数
    rows = c.report()
    assert len(rows) == 1
    assert rows[0]["route"] == "x"
    assert rows[0]["total_tokens"] == 0
    assert rows[0]["calls"] == 1


def test_counter_reset():
    c = TokenCounter()
    c.add("a", {"total_tokens": 1})
    c.reset()
    assert c.report() == []
