"""Agent 版 RAGAS 四指标评测（LLM-as-judge，judge 函数复用 scripts.ragas_eval）。

与 scripts/ragas_eval.py（/ask 版）的区别：
- 上下文取 **agent 真实喂给生成节点的 state["contexts"]**（路由定向 + 过滤后的上下文），
  而非检索候选池 top-N；
- 每题附加 route / retry_count / 兜底 / 答案摘要，支撑 bad case 分类与按 route 的质量分析；
- 同一 golden 可跑 /ask 版（scripts.ragas_eval）对比：预期 /chat 的 context_precision 更高
  （路由 + 定向检索的证据）。

用法：
    .venv/Scripts/python.exe -m scripts.eval_agent_quality                 # 离线（FakeJudge + OfflineAgentLLM）
    .venv/Scripts/python.exe -m scripts.eval_agent_quality --online        # 真实千问 judge + 真实链路
    .venv/Scripts/python.exe -m scripts.eval_agent_quality --online --repeat 3 --json-out data/eval/quality_result.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
from pathlib import Path

from app.agent.graph import BadmintonAgent
from app.core.config import BASE_DIR
from app.rag.llm import FALLBACK_ANSWER
from scripts.eval_agent import (
    GOLDEN_FILE,
    build_offline_agent,
    build_online_agent,
    load_golden,
)
from scripts.ragas_eval import (
    METRIC_KEYS,
    FakeJudge,
    average_metrics,
    build_judge,
    judge_answer_relevancy,
    judge_context_precision,
    judge_context_relevance,
    judge_context_recall,
    judge_faithfulness,
)

logger = logging.getLogger(__name__)

DEFAULT_RESULT_FILE = BASE_DIR / "data" / "eval" / "quality_result.json"

# 追加口径（plan §6.1）：context 单元变化会机械抬高 precision，必须同时报展开回原始行的密度
EXTRA_METRIC_KEYS = ("context_precision_strict", "token_efficiency")


def _context_texts(state: dict) -> list[str]:
    """agent 真实喂给生成节点的上下文 → judge 文本（只取 document 字段，与 ragas_eval 口径一致）。"""
    return [
        str(rec.get("document", ""))
        for rec in (state.get("contexts") or [])
        if rec.get("document")
    ]


def _expanded_records(contexts: list[dict], store) -> tuple[list[str], list[tuple[int, int]]]:
    """每条上下文展开成它覆盖的**原始 record 文本**（`_strict` 的分母，两模式同单位）。

    返回 (record 文本列表, 每条上下文在列表中的区间)：区间用于把「record 级相关性」
    折算回「上下文级占比」，算 token_efficiency。
    classic 上下文的 id 本身就是 record id；wiki 章节靠 `metadata.records` 锚点回溯（plan §3.2 铁律）。
    """
    per_context: list[list[str]] = []
    for ctx in contexts:
        records = [str(r) for r in ((ctx.get("metadata") or {}).get("records") or []) if ":" in str(r)]
        own = str(ctx.get("id", ""))
        per_context.append(records or ([own] if ":" in own else []))

    by_collection: dict[str, list[str]] = {}
    for ids in per_context:
        for rid in ids:
            by_collection.setdefault(rid.rpartition(":")[0], []).append(rid)
    texts: dict[str, str] = {}
    for collection, ids in by_collection.items():
        for hit in store.get(collection, list(dict.fromkeys(ids))):
            texts[hit["id"]] = str(hit.get("document", ""))

    expanded: list[str] = []
    spans: list[tuple[int, int]] = []
    for ctx, ids in zip(contexts, per_context):
        start = len(expanded)
        expanded.extend(texts[rid] for rid in ids if texts.get(rid))
        spans.append((start, len(expanded)))
    return expanded, spans


def run_agent_quality(
    golden: list[dict],
    *,
    online: bool = False,
    judge=None,
    agent: BadmintonAgent | None = None,
    repeat: int = 1,
    mode: str = "classic",
) -> list[dict]:
    """对 golden 逐题跑 agent 问答 + 四指标（+ `_strict` 口径），返回每题分数 dict 列表。

    - offline：FakeJudge + OfflineAgentLLM（固定打分，仅验证链路，只支持 classic）；
    - online：真实千问 judge + 真实链路（百炼 embedding + 千问 LLM + data/chroma）；
    - mode="wiki" 走 LLM 导航式检索（需先 `python -m scripts.build_wiki --index`）；
    - repeat>1 时各指标取多次均值（缓解 LLM 非确定性）。
    """
    if mode == "wiki" and not online and agent is None:
        raise SystemExit("wiki 模式评测需要 --online（离线不含真实向量与 LLM 导航）")
    if agent is None:
        agent = build_online_agent(enable_wiki=(mode == "wiki")) if online else build_offline_agent()
    if judge is None:
        judge = build_judge() if online else FakeJudge()
    store = agent._retriever._store

    results: list[dict] = []
    for item in golden:
        attempts: list[dict] = []
        for _ in range(repeat):
            state = agent.invoke(
                {
                    "question": item["question"],
                    "session_id": "eval",
                    "mode": mode,
                    "history": [],
                    "contexts": [],
                    "sources": [],
                    "sub_questions": [],
                    "retry_count": 0,
                    "trace": [],
                }
            )
            answer = state.get("answer", "")
            contexts = _context_texts(state)
            raw_contexts = state.get("contexts") or []
            expanded, spans = _expanded_records(raw_contexts, store)
            flags = judge_context_relevance(judge, item["question"], expanded)
            # 每条上下文的相关字节 = 自身体积 × 其中相关 record 的占比（值域 [0,1]，两模式同单位）
            context_bytes = 0
            relevant_bytes = 0
            for index, ctx in enumerate(raw_contexts):
                size = len(str(ctx.get("document", "")))
                context_bytes += size
                start, end = spans[index]
                total = end - start
                if total:
                    relevant_bytes += size * sum(flags[start:end]) / total
            attempts.append(
                {
                    "faithfulness": judge_faithfulness(judge, answer, contexts),
                    "answer_relevancy": judge_answer_relevancy(judge, item["question"], answer),
                    "context_precision": judge_context_precision(judge, item["question"], contexts),
                    "context_recall": judge_context_recall(
                        judge, item.get("reference_answer") or "", contexts
                    ),
                    # 展开回原始行后的相关信息密度：wiki 的 context 单元更大，必须用这个口径 A/B
                    "context_precision_strict": (sum(flags) / len(expanded)) if expanded else 0.0,
                    "token_efficiency": (relevant_bytes / context_bytes) if context_bytes else 0.0,
                    "context_bytes": context_bytes,
                    "strict_units": len(expanded),
                    "route": state.get("route", ""),
                    "retry_count": state.get("retry_count", 0),
                    "fallback": answer.strip() == FALLBACK_ANSWER,
                    "answer": answer[:200],
                    "num_contexts": len(contexts),
                    "wiki_degraded": (state.get("wiki_trace") or {}).get("degraded", ""),
                }
            )
        base = attempts[0]
        if repeat > 1:
            for key in (*METRIC_KEYS, *EXTRA_METRIC_KEYS):
                base[key] = sum(a[key] for a in attempts) / len(attempts)
        results.append(
            {
                "id": item["id"],
                "question": item["question"],
                "expect_routes": item.get("expect_routes"),
                "mode": mode,
                **{key: round(base[key], 4) for key in METRIC_KEYS},
                **{key: round(base[key], 4) for key in EXTRA_METRIC_KEYS},
                "context_bytes": base["context_bytes"],
                "strict_units": base["strict_units"],
                "route": base["route"],
                "retry_count": base["retry_count"],
                "fallback": base["fallback"],
                "answer": base["answer"],
                "num_contexts": base["num_contexts"],
                "wiki_degraded": base["wiki_degraded"],
            }
        )
    return results


def averages(results: list[dict]) -> dict[str, float]:
    """四指标 + 追加口径的总体平均。"""
    summary = dict(average_metrics(results))
    for key in EXTRA_METRIC_KEYS:
        summary[key] = round(sum(r[key] for r in results) / len(results), 4) if results else 0.0
    return summary


def route_averages(results: list[dict]) -> dict[str, dict[str, float]]:
    """按 route 分组的四指标平均（可对比 equipment 结构化 vs technique 定向检索）。"""
    by_route: dict[str, list[dict]] = {}
    for r in results:
        by_route.setdefault(r.get("route") or "unknown", []).append(r)
    return {route: average_metrics(rows) for route, rows in sorted(by_route.items())}


def print_report(results: list[dict], *, online: bool) -> None:
    mode = "online" if online else "offline"
    print(f"Agent 版 RAGAS 四指标（{mode}，手写 LLM-as-judge）: {len(results)} 题")
    print(
        f"{'id':>4} {'route':>9} {'faith':>6} {'relev':>6} {'prec':>6} {'prec*':>6} "
        f"{'eff':>6} {'recall':>6} {'ctx':>3} {'unit':>4}"
    )
    for r in results:
        print(
            f"{r['id']:>4} {r.get('route', ''):>9} {r['faithfulness']:>6.3f} "
            f"{r['answer_relevancy']:>6.3f} {r['context_precision']:>6.3f} "
            f"{r['context_precision_strict']:>6.3f} {r['token_efficiency']:>6.3f} "
            f"{r['context_recall']:>6.3f} {r['num_contexts']:>3} {r['strict_units']:>4}"
        )
    avg = averages(results)
    print("平均：" + "  ".join(f"{k}={v:.3f}" for k, v in avg.items()))
    print("（prec*=context_precision_strict：上下文展开回原始 record 后的相关信息密度；"
          "eff=token_efficiency：上下文中相关信息占的字节比例，按 record 级相关性折算，值域 0~1）")
    print("按 route 分组：")
    for route, row in route_averages(results).items():
        print(
            f"  {route}: " + "  ".join(f"{k}={v:.3f}" for k, v in row.items())
        )


def print_comparison(classic: list[dict], wiki: list[dict]) -> None:
    """classic 与 wiki 并排对照（同 golden、同 seed、同 judge 口径）。"""
    a, b = averages(classic), averages(wiki)
    print(f"\n{'指标':>26} {'classic':>9} {'wiki':>9} {'Δ':>8}")
    for key in (*METRIC_KEYS, *EXTRA_METRIC_KEYS):
        print(f"{key:>26} {a[key]:>9.3f} {b[key]:>9.3f} {b[key] - a[key]:>+8.3f}")
    ctx_a = sum(r["num_contexts"] for r in classic) / max(len(classic), 1)
    ctx_b = sum(r["num_contexts"] for r in wiki) / max(len(wiki), 1)
    print(f"{'平均 context 条数':>26} {ctx_a:>9.2f} {ctx_b:>9.2f} {ctx_b - ctx_a:>+8.2f}")
    degraded = [r["id"] for r in wiki if r.get("wiki_degraded")]
    if degraded:
        print(f"wiki 降级题（orient 未选到知识单元，回落 classic）：{degraded}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Agent 版 RAGAS 四指标评测（LLM-as-judge）")
    parser.add_argument("--online", action="store_true", help="真实千问 judge + 真实链路（默认离线）")
    parser.add_argument(
        "--mode",
        choices=("classic", "wiki", "both"),
        default="classic",
        help="检索模式：classic / wiki / both（并排对照，需 --online）",
    )
    parser.add_argument("--repeat", type=int, default=1, help="每题连问次数，指标取均值（默认 1）")
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help=f"把结果写到 JSON 文件（默认 {DEFAULT_RESULT_FILE}）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    golden = load_golden(GOLDEN_FILE)

    runs: dict[str, list[dict]] = {}
    modes = ("classic", "wiki") if args.mode == "both" else (args.mode,)
    if args.mode == "both" and not args.online:
        raise SystemExit("--mode both 需要 --online（wiki 模式无离链路）")
    for retrieval_mode in modes:
        results = run_agent_quality(
            golden, online=args.online, repeat=args.repeat, mode=retrieval_mode
        )
        runs[retrieval_mode] = results
        print_report(results, online=args.online)
    if len(runs) == 2:
        print_comparison(runs["classic"], runs["wiki"])

    results = runs[modes[-1]]
    out_path = args.json_out or DEFAULT_RESULT_FILE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        # mode 沿用历史语义（online|offline），检索模式另起字段，避免旧报表读者误读
        "mode": "online" if args.online else "offline",
        "retrieval_mode": args.mode,
        "repeat": args.repeat,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "total": len(results),
        "average": averages(results),
        "route_averages": route_averages(results),
        "results": results,
    }
    if len(runs) == 2:
        report["runs"] = {
            key: {"average": averages(val), "results": val} for key, val in runs.items()
        }
        report["comparison"] = {
            key: {"classic": averages(runs["classic"])[key], "wiki": averages(runs["wiki"])[key]}
            for key in (*METRIC_KEYS, *EXTRA_METRIC_KEYS)
        }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"结果已写入 {out_path}")


if __name__ == "__main__":
    main()
