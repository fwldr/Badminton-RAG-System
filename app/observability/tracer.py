"""可观测性 tracer：Trace/Span 抽象 + NullTracer（内存记账，不触网）+ LangfuseTracer（v4，lazy import）。

设计（见 badminton-rag-phase4-plan.md Step 1~2 与 langfuse/skills 的 v4-project-migration 工作流）：
- 每个 /chat 请求一条 trace：route → 工具 → generate → verify →（retry）→ 结束；
- v4 中 trace 由 trace_id 隐式标识，根 observation 承载整体 input/output；
  `propagate_attributes` 把 session_id/tags/trace_name 传播到所有 span；
- 每个 agent 节点一个 span，记录输入/输出摘要、耗时（duration_ms）、LLM token（归因到当前 span）；
- token 归因：`attach_llm(llm)` 把 usage_hook 挂到 LLMClient，LLM 响应的 usage 累计到
  contextvar 栈顶的 span（多线程/异步互不串扰）；
- `NullTracer` 默认使用：只在内存记账（spans/token_summary），不加载 langfuse SDK、不触网，
  对生产链路是旁路（usage_hook 异常吞掉）；
- `LangfuseTracer` 构造时才 `import langfuse`（lazy import），保证离线测试进程不加载第三方 SDK。
"""

from __future__ import annotations

import atexit
import contextvars
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# 当前 span 栈（token 归因用）。注：同步调用栈内有效；若未来把 agent 挪到线程池，
# 需用 contextvars.copy_context() 显式传递。
_current_span: contextvars.ContextVar["Span | None"] = contextvars.ContextVar(
    "dsh_current_span", default=None
)


class Span:
    """一次 span 句柄：end() 时记录耗时/输出/token，交给 tracer 收尾。"""

    def __init__(self, tracer: "LocalTracer", name: str, input=None, metadata=None) -> None:
        self._tracer = tracer
        self.name = name
        self.input = input
        self.metadata = metadata or {}
        self.output = None
        self.duration_ms = 0.0
        self.tokens: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self._started = time.monotonic()
        self._ended = False
        self._prev = _current_span.get()
        _current_span.set(self)

    def accumulate(self, usage: dict) -> None:
        """累加一次 LLM usage（异常值忽略）。"""
        for key in self.tokens:
            try:
                self.tokens[key] += int(usage.get(key, 0) or 0)
            except (TypeError, ValueError):
                pass

    def end(self, output=None) -> None:
        """结束 span：恢复上下文栈，交 tracer 收尾（LangfuseSpan 会在此后上报）。"""
        if self._ended:
            return
        self._ended = True
        self.output = output
        self.duration_ms = (time.monotonic() - self._started) * 1000
        _current_span.set(self._prev)
        self._tracer._finish(self)


class LocalTracer:
    """内存版 tracer 基类：跟踪 span 顺序与 token，不加载任何第三方 SDK。

    子类差异只在「上报」：NullTracer 不上报；LangfuseTracer 上报到 Langfuse。
    """

    def __init__(self) -> None:
        self._closed: list[dict] = []  # 当前 trace 内的已结束 span 摘要
        self._lock = threading.Lock()

    def start_trace(
        self,
        name: str,
        input=None,
        metadata=None,
        session_id=None,
        tags=None,
        trace_id=None,
    ) -> None:
        """开启一条新 trace（重置本 trace 的 span 账本）。"""
        with self._lock:
            self._closed = []
            self._current_trace = {
                "name": name,
                "input": input,
                "metadata": metadata,
                "session_id": session_id,
                "tags": tags,
                "trace_id": trace_id,
            }

    def end_trace(self, output=None, tags=None) -> None:
        self._current_trace = None

    def flush(self) -> None:
        """把待上报的 trace 刷出（进程关闭时调用；NullTracer 为 no-op）。"""

    def trace_url(self, trace_id: str) -> str:
        """返回 trace 在 Langfuse 中的 URL（NullTracer 返回空串）。"""
        return ""

    def span(self, name: str, input=None, metadata=None) -> Span:
        return Span(self, name, input, metadata)

    def attach_llm(self, llm) -> None:
        """把 usage_hook 挂到 LLMClient，LLM 响应 token 归因到当前 span（旁路，失败不影响主链路）。"""
        try:
            llm.usage_hook = self._on_usage
        except Exception:
            logger.exception("attach_llm 失败（旁路，忽略）")

    def _on_usage(self, usage: dict) -> None:
        span = _current_span.get()
        if span is not None:
            span.accumulate(usage)

    def _finish(self, span: Span) -> None:
        with self._lock:
            self._closed.append(
                {
                    "name": span.name,
                    "input": span.input,
                    "output": span.output,
                    "duration_ms": round(span.duration_ms, 3),
                    "tokens": dict(span.tokens),
                }
            )

    def spans(self) -> list[dict]:
        """当前 trace 内已结束的 span 摘要列表（测试断言 / 排查用）。"""
        with self._lock:
            return list(self._closed)

    def token_summary(self) -> dict[str, dict[str, int]]:
        """当前 trace 内按 span 名聚合的 token（供成本统计按 route 上报）。"""
        out: dict[str, dict[str, int]] = {}
        with self._lock:
            for rec in self._closed:
                row = out.setdefault(
                    rec["name"],
                    {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "spans": 0},
                )
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    row[key] += rec["tokens"].get(key, 0)
                row["spans"] += 1
        return out


class NullTracer(LocalTracer):
    """未开启 Langfuse 时的默认 tracer：内存记账 + token 归因，不加载 SDK、不触网。"""


class RecordingTracer(LocalTracer):
    """测试用 tracer：行为与 NullTracer 相同，`spans()` 可断言 span 顺序与内容。"""


class LangfuseSpan(Span):
    """Langfuse v4 span 包装：持有 `start_as_current_observation` 的上下文管理器。

    v4（OpenTelemetry 基座）中，span 创建时 `__enter__` 会把它设为 OTel current span，
    子 span 自动挂到它下面；`end()` 时先把耗时/token 写进 metadata，再退出上下文
    （自动结束 observation，并把 current span 恢复为父 span）。
    """

    def __init__(self, tracer: "LangfuseTracer", name: str, input=None, metadata=None, cm=None, lf_span=None) -> None:
        super().__init__(tracer, name, input, metadata)
        self._cm = cm          # start_as_current_observation 上下文管理器
        self._lf_span = lf_span

    def _upload(self) -> None:
        cm, lf_span = self._cm, self._lf_span
        self._cm = self._lf_span = None
        if lf_span is not None:
            try:
                lf_span.update(
                    output=self.output,
                    metadata={"duration_ms": round(self.duration_ms, 3), "tokens": self.tokens},
                )
            except Exception:
                logger.exception("langfuse span.update 失败（旁路，忽略）")
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                logger.exception("langfuse span 上下文退出失败（旁路，忽略）")


class LangfuseTracer(LocalTracer):
    """Langfuse v4 上报 tracer（OpenTelemetry 基座；构造时才 import langfuse）。

    v4 模型（见 langfuse/skills 的 v4-project-migration 工作流）：
    - trace 由 trace_id 隐式标识（无独立 trace 对象）；**根 observation 承载整体 input/output**；
    - `propagate_attributes` 在 context 内把 session_id/tags/trace_name 传播给所有 span；
    - `start_as_current_observation` 维护 OTel current span：根 span 之后创建的节点 span
      自动挂到根下（兄弟节点，与 state["trace"] 节点一一对应）。

    trace 结构（与 state["trace"] 节点一一对应）：
        trace:  {id: trace_id, name: "/chat {trace_id}", input, output, session_id, tags}
        span:   每个 agent 节点一个，metadata 含 duration_ms / tokens。
    """

    def __init__(self, public_key: str, secret_key: str, host: str | None = None) -> None:
        super().__init__()
        from langfuse import Langfuse  # lazy import：离线测试进程不加载

        kwargs: dict[str, Any] = {"public_key": public_key, "secret_key": secret_key}
        if host:
            kwargs["host"] = host
        self._langfuse = Langfuse(**kwargs)
        self._host = (host or "https://cloud.langfuse.com").rstrip("/")
        self._trace_id: str | None = None
        self._root_cm = None  # 根 observation 的上下文管理器
        self._root = None     # 根 observation（LangfuseSpan SDK 对象）
        self._prop_ctx = None  # propagate_attributes 上下文
        atexit.register(self.flush)  # 进程退出兜底刷出（避免丢 trace）

    def start_trace(
        self,
        name: str,
        input=None,
        metadata=None,
        session_id=None,
        tags=None,
        trace_id=None,
    ) -> None:
        super().start_trace(name, input, metadata, session_id, tags, trace_id)
        self._trace_id = trace_id or self._langfuse.create_trace_id()
        self._root_cm = self._root = self._prop_ctx = None
        try:
            from langfuse import propagate_attributes
            from langfuse.types import TraceContext

            # 传播作用域必须在根 span 创建前建立：session_id/tags/trace_name 传播给所有 span
            if session_id or tags:
                self._prop_ctx = propagate_attributes(
                    session_id=session_id,
                    tags=tags,
                    trace_name=name,
                    metadata=metadata,
                )
                self._prop_ctx.__enter__()
            # 根 observation：承载整体 input/output
            self._root_cm = self._langfuse.start_as_current_observation(
                trace_context=TraceContext(trace_id=self._trace_id),
                name=name,
                as_type="span",
                input=input,
                metadata=metadata,
            )
            self._root = self._root_cm.__enter__()
        except Exception:
            logger.exception("langfuse trace 初始化失败（旁路，忽略）")
            self._safe_exit_prop()
            self._root_cm = None
            self._root = None

    def span(self, name: str, input=None, metadata=None) -> Span:
        if self._root_cm is None:
            return super().span(name, input, metadata)
        try:
            cm = self._langfuse.start_as_current_observation(
                name=name, as_type="span", input=input, metadata=metadata
            )
            lf_span = cm.__enter__()
        except Exception:
            logger.exception("langfuse span 创建失败（旁路，忽略）")
            return super().span(name, input, metadata)
        return LangfuseSpan(self, name, input, metadata, cm=cm, lf_span=lf_span)

    def end_trace(self, output=None, tags=None) -> None:
        super().end_trace(output)
        root_cm, root, prop = self._root_cm, self._root, self._prop_ctx
        self._root_cm = self._root = self._prop_ctx = None
        if root is not None:
            try:
                root.update(output=output)
                if root_cm is not None:
                    root_cm.__exit__(None, None, None)  # 退出根上下文（自动结束）
                else:
                    root.end()
            except Exception:
                logger.exception("langfuse trace 收尾失败（旁路，忽略）")
        elif root_cm is not None:
            try:
                root_cm.__exit__(None, None, None)
            except Exception:
                logger.exception("langfuse trace 收尾失败（旁路，忽略）")
        if prop is not None:
            try:
                prop.__exit__(None, None, None)
            except Exception:
                logger.exception("langfuse propagate_attributes 退出失败（旁路，忽略）")

    def _safe_exit_prop(self) -> None:
        prop = self._prop_ctx
        self._prop_ctx = None
        if prop is not None:
            try:
                prop.__exit__(None, None, None)
            except Exception:
                pass

    def flush(self) -> None:
        """把 SDK 队列中的事件刷出（应用关闭 / 手动验证时调用）。"""
        try:
            self._langfuse.flush()
        except Exception:
            logger.exception("langfuse flush 失败（旁路，忽略）")

    def trace_url(self, trace_id: str) -> str:
        """返回 trace 在 Langfuse 中的 URL（标准格式 {host}/trace/{id}）。"""
        return f"{self._host}/trace/{trace_id}"

    def _finish(self, span: Span) -> None:
        super()._finish(span)
        if isinstance(span, LangfuseSpan):
            span._upload()


def build_tracer(settings) -> LocalTracer:
    """按配置构建 tracer：未开启或缺 key 降级 NullTracer（内存记账，不触网）。"""
    if not getattr(settings, "langfuse_enabled", False):
        return NullTracer()
    public_key = getattr(settings, "langfuse_public_key", None)
    secret_key = getattr(settings, "langfuse_secret_key", None)
    if not public_key or not secret_key:
        logger.warning("langfuse_enabled=True 但缺少 LANGFUSE_PUBLIC_KEY/SECRET_KEY，降级 NullTracer")
        return NullTracer()
    try:
        return LangfuseTracer(public_key, secret_key, getattr(settings, "langfuse_host", None))
    except Exception:
        logger.exception("初始化 LangfuseTracer 失败，降级 NullTracer")
        return NullTracer()
