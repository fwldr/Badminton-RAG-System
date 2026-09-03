"""Langfuse trace 验证工具：按 trace_id 查询或列出最近的 /chat trace（调试/演示用）。

用法：
    .venv/Scripts/python.exe -m scripts.check_langfuse --trace-id <id>
    .venv/Scripts/python.exe -m scripts.check_langfuse --latest 5
"""

from __future__ import annotations

import argparse
import sys

from app.core.config import get_settings


def _client():
    from langfuse import Langfuse

    s = get_settings()
    if not (s.langfuse_enabled and s.langfuse_public_key and s.langfuse_secret_key):
        print("Langfuse 未启用或缺少 key（LANGFUSE_ENABLED / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY）")
        sys.exit(1)
    return Langfuse(
        public_key=s.langfuse_public_key,
        secret_key=s.langfuse_secret_key,
        host=s.langfuse_host,
    )


def _obs_dict(obs) -> dict:
    """observation 字段取值兜底：可能是 pydantic 模型或 dict。"""
    if hasattr(obs, "model_dump"):
        return obs.model_dump()
    if hasattr(obs, "dict"):
        return obs.dict()
    return dict(obs) if isinstance(obs, dict) else {}


def _print_trace(t) -> None:
    print(f"trace: {getattr(t, 'id', '?')}  name={getattr(t, 'name', '')}  "
          f"timestamp={getattr(t, 'timestamp', '')}")
    print(f"  input: {str(getattr(t, 'input', ''))[:120]}")
    print(f"  output: {str(getattr(t, 'output', ''))[:200]}")
    for obs in getattr(t, "observations", None) or []:
        d = _obs_dict(obs)
        meta = d.get("metadata") or {}
        tokens = meta.get("tokens") if isinstance(meta, dict) else None
        dur = meta.get("duration_ms") if isinstance(meta, dict) else None
        print(
            f"  span: {d.get('name', '?')}  type={d.get('type', '')}  "
            f"parent={d.get('parent_observation_id', '')}  dur={dur}ms  tokens={tokens}"
        )


def _get_trace_with_retry(client, trace_id: str, tries: int = 12, sleep: float = 5.0):
    """Langfuse 读取是最终一致：trace 壳先出现、observations 后补齐，重试直到两者就绪。"""
    import time

    last: Exception | None = None
    for i in range(tries):
        try:
            t = client.api.trace.get(trace_id)
            if getattr(t, "observations", None):
                return t
            last = None
        except Exception as exc:  # noqa: BLE001 - 网络/一致性异常都重试
            last = exc
        print(f"trace 尚未就绪（{i + 1}/{tries}），{sleep}s 后重试 ...")
        time.sleep(sleep)
    if last is not None:
        raise last
    return None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Langfuse trace 验证")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trace-id", type=str, help="按 trace_id 查询")
    group.add_argument("--latest", type=int, default=0, help="列出最近 N 条 trace")
    args = parser.parse_args(argv)

    client = _client()
    if args.trace_id:
        t = _get_trace_with_retry(client, args.trace_id)
        _print_trace(t)
    else:
        page = client.api.trace.list(page=1, limit=args.latest)
        traces = getattr(page, "data", None) or page
        print(f"最近 {len(traces)} 条 trace：")
        for t in traces:
            _print_trace(t)


if __name__ == "__main__":
    main()
