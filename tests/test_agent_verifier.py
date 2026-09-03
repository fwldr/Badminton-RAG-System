"""回答校验测试：LLM 支撑/不支撑判断。"""

from app.agent.verifier import verify


class _StubLLM:
    def __init__(self, supported: bool):
        self._supported = supported

    def complete(self, messages, *, json_mode=False) -> str:
        return f'{{"supported": {str(self._supported).lower()}, "reason": "test"}}'


def test_verify_supported():
    llm = _StubLLM(True)
    assert verify("问题", "回答", [{"document": "支撑内容"}], llm) is True


def test_verify_not_supported():
    llm = _StubLLM(False)
    assert verify("问题", "回答", [{"document": "不相关内容"}], llm) is False


def test_verify_empty_contexts_conservative():
    llm = _StubLLM(False)
    # 无上下文 → 保守放行（不阻断回答）
    assert verify("问题", "回答", [], llm) is True


def test_verify_exception_conservative():
    class _Broken:
        def complete(self, messages, *, json_mode=False):
            raise RuntimeError("boom")

    assert verify("问题", "回答", [{"document": "x"}], _Broken()) is True
