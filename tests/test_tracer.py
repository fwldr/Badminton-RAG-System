"""tracer 单元测试：NullTracer 记账 + attach_llm token 归因 + build_tracer 降级（不触网）。

覆盖：span 记录、嵌套 span token 归因、end 幂等、配置开关/缺 key 降级 NullTracer。
LangfuseTracer 不实例化（lazy import 需要真实 SDK）。
"""

from app.core.config import get_settings
from app.observability.tracer import (
    LangfuseTracer,
    LocalTracer,
    NullTracer,
    build_tracer,
)


class _StubLLM:
    """带 usage_hook 的桩 LLM：complete 触发 hook（模拟 LLM 响应 usage）。"""

    def __init__(self) -> None:
        self.usage_hook = None

    def complete(self, messages, *, json_mode=False) -> str:
        if self.usage_hook is not None:
            self.usage_hook({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        return "ok"


def test_null_tracer_records_spans():
    tracer = NullTracer()
    span = tracer.span("route", input={"question": "q"}, metadata={"a": 1})
    span.end(output={"route": "chitchat"})
    spans = tracer.spans()
    assert len(spans) == 1
    assert spans[0]["name"] == "route"
    assert spans[0]["output"] == {"route": "chitchat"}
    assert spans[0]["duration_ms"] >= 0
    assert spans[0]["tokens"]["total_tokens"] == 0


def test_attach_llm_token_attribution():
    tracer = NullTracer()
    llm = _StubLLM()
    tracer.attach_llm(llm)
    span = tracer.span("generate")
    llm.complete([{"role": "user", "content": "hi"}], json_mode=True)
    span.end()
    summary = tracer.token_summary()
    assert summary["generate"]["prompt_tokens"] == 10
    assert summary["generate"]["completion_tokens"] == 5
    assert summary["generate"]["total_tokens"] == 15


def test_nested_span_token_attribution():
    """嵌套 span：LLM 调用归因到最内层 span（contextvar 栈）。"""
    tracer = NullTracer()
    llm = _StubLLM()
    tracer.attach_llm(llm)
    outer = tracer.span("agent.invoke")
    inner = tracer.span("route")
    llm.complete([{"role": "user", "content": "x"}])
    inner.end()
    llm.complete([{"role": "user", "content": "y"}])
    outer.end()
    summary = tracer.token_summary()
    assert summary["route"]["total_tokens"] == 15
    assert summary["agent.invoke"]["total_tokens"] == 15  # 只累计自身期间的调用


def test_span_end_idempotent():
    tracer = NullTracer()
    span = tracer.span("route")
    span.end()
    span.end()
    assert len(tracer.spans()) == 1


def test_token_summary_aggregates_same_name():
    tracer = NullTracer()
    for _ in range(2):
        span = tracer.span("route")
        span.accumulate({"total_tokens": 5})
        span.end()
    summary = tracer.token_summary()
    assert summary["route"]["total_tokens"] == 10
    assert summary["route"]["spans"] == 2


def test_build_tracer_disabled_returns_null(monkeypatch):
    monkeypatch.setattr(get_settings(), "langfuse_enabled", False)
    tracer = build_tracer(get_settings())
    assert isinstance(tracer, NullTracer)


def test_build_tracer_missing_key_degrades(monkeypatch):
    monkeypatch.setattr(get_settings(), "langfuse_enabled", True)
    monkeypatch.setattr(get_settings(), "langfuse_public_key", None)
    monkeypatch.setattr(get_settings(), "langfuse_secret_key", None)
    tracer = build_tracer(get_settings())
    assert isinstance(tracer, NullTracer)


def test_start_trace_stores_session_tags_trace_id():
    """start_trace 的新增元信息（session_id/tags/trace_id）在内存账本中保留。"""
    tracer = NullTracer()
    tracer.start_trace("/chat xyz", input={"q": 1}, session_id="s1", tags=["chat"], trace_id="abc")
    assert tracer._current_trace["session_id"] == "s1"
    assert tracer._current_trace["tags"] == ["chat"]
    assert tracer._current_trace["trace_id"] == "abc"


def test_null_tracer_flush_and_trace_url_noop():
    tracer = NullTracer()
    tracer.flush()  # no-op，不抛异常
    assert tracer.trace_url("any") == ""


def test_langfuse_tracer_is_local_tracer_subclass():
    """LangfuseTracer 继承 LocalTracer（接口一致，便于替换）。"""
    assert issubclass(LangfuseTracer, LocalTracer)
