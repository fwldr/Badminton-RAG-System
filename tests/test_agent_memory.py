"""多轮记忆测试：存储 / 压缩 / 会话隔离。"""

from app.agent.memory import MemoryStore, compress_history


def _stub_llm(summary: str):
    class _L:
        def complete(self, messages, *, json_mode=False) -> str:
            return f'{{"summary": "{summary}"}}'

    return _L()


def test_memory_append_get():
    m = MemoryStore()
    m.append("s1", {"role": "user", "content": "你好"})
    m.append("s1", {"role": "assistant", "content": "你好！"})
    assert len(m.get("s1")) == 2
    assert m.get("s1", limit=1)[0]["role"] == "assistant"


def test_memory_session_isolation():
    m = MemoryStore()
    m.append("a", {"role": "user", "content": "A"})
    m.append("b", {"role": "user", "content": "B"})
    assert len(m.get("a")) == 1
    assert m.get("a")[0]["content"] == "A"
    assert m.get("b")[0]["content"] == "B"


def test_memory_clear():
    m = MemoryStore()
    m.append("s", {"role": "user", "content": "x"})
    m.clear("s")
    assert m.get("s") == []


def test_compress_under_limit_noop():
    history = [{"role": "user", "content": f"q{i}"} for i in range(5)]
    assert compress_history(history, None, max_items=8) == history


def test_compress_with_llm():
    history = [{"role": "user", "content": f"问题{i}"} for i in range(10)]
    llm = _stub_llm("用户问了10个问题")
    result = compress_history(history, llm, max_items=4)
    assert result[0]["role"] == "system"
    assert "历史摘要" in result[0]["content"]
    assert len(result) == 1 + 4  # 摘要 + 最近 4 条


def test_compress_without_llm_drops_old():
    history = [{"role": "user", "content": f"q{i}"} for i in range(10)]
    result = compress_history(history, None, max_items=4)
    assert len(result) == 4
    assert result[0]["content"] == "q6"
