"""golden set 评估：读 data/eval/golden.json 逐题评测 RAG 问答链路，输出逐题 PASS/FAIL + 总通过率。

默认离线模式：FakeEmbedder + 内存 VectorStore + stub LLM 的 AskService（不触网），
评估重点是检索链路；--online 走真实服务（百炼 embedding + 千问 LLM + data/chroma）。

用法：
    .venv/Scripts/python.exe -m scripts.eval_ask
    .venv/Scripts/python.exe -m scripts.eval_ask --online
    .venv/Scripts/python.exe -m scripts.eval_ask --bm25        # 开启 BM25 混合检索
    .venv/Scripts/python.exe -m scripts.eval_ask --expand      # 开启同义词查询扩展（CLI 默认关闭，便于对比）
    .venv/Scripts/python.exe -m scripts.eval_ask --repeat 3    # 每题连问 3 次（默认），多数次 PASS 判过
    .venv/Scripts/python.exe -m scripts.eval_ask --json-out data/eval/result.json

逐题检查三项（全部通过才计 PASS）：
    fallback_ok    兜底是否符合 expect_fallback；
    collections_ok 检索命中的 collection 是否覆盖 expect_collections；
    keywords_ok    答案是否包含全部 expect_keywords。

repeat 口径（--repeat N，默认 3）：
    每题连问 N 次，结果记录 per_try 明细；
    正例（expect_fallback=false）多数次（>N/2）PASS 即判过，吸收 LLM 单次非确定性的抽样失真
    （如 q01 偶发兜底导致单次误判）；
    负例（expect_fallback=true）要求全部次 PASS，避免「3 次里 1 次兜底算过」的假阳性。
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
from pathlib import Path

from app.core.config import BASE_DIR, get_settings
from app.ingest.embedder import FakeEmbedder, build_embedder
from app.ingest.pipeline import ingest_table
from app.ingest.serializer import ALL_TABLES
from app.ingest.store import VectorStore
from app.rag.llm import FALLBACK_ANSWER, LLMClient
from app.rag.reranker import build_reranker
from app.rag.retriever import Retriever
from app.rag.service import AskService

GOLDEN_FILE = BASE_DIR / "data" / "eval" / "golden.json"
DEFAULT_RESULT_FILE = BASE_DIR / "data" / "eval" / "result.json"

logger = logging.getLogger(__name__)

# golden 每题必含字段；reference_answer 为标准答案（供 context_recall 等后续指标使用）
_REQUIRED_GOLDEN_FIELDS = (
    "id",
    "question",
    "expect_collections",
    "expect_keywords",
    "expect_fallback",
    "reference_answer",
)


# 离线 stub 的颜色过滤近似：对应真实 LLM 对「颜色」类问题的过滤抽取（多字颜色放前，避免子串遮蔽）
_COLOR_WORDS: tuple[str, ...] = (
    "荧光绿",
    "玫红",
    "红色",
    "蓝色",
    "白色",
    "黑色",
    "黄色",
    "绿色",
    "紫色",
)


class OfflineStubLLM(LLMClient):
    """离线评估用 stub（不触网，不调 OpenAI）。

    generate_answer 依据「期望表是否进入检索上下文」决定是否兜底：
    - 期望表非空且命中 → 把命中文档原文拼成回答（便于关键词检查），并声明 used 编号；
    - 否则返回兜底文案。这样离线评估能反映检索链路的质量回归。

    extract_filters 用启发式模拟真实 LLM 的颜色过滤抽取：问题含颜色词 → 抽
    {"颜色": [颜色]}，用于验证「BM25 召回 + 属性过滤」链路（如红色手胶 GP203）。
    """

    def __init__(self, expected_collections: list[str]) -> None:
        self._expected = set(expected_collections)

    def extract_filters(self, question: str) -> dict:
        for color in _COLOR_WORDS:
            if color in question:
                return {"颜色": [color]}
        return {}

    def generate_answer(self, question: str, contexts: list[dict]) -> dict:
        hit = {c.get("table") for c in contexts}
        if self._expected and hit & self._expected:
            used = [i + 1 for i, c in enumerate(contexts) if c.get("table") in self._expected]
            answer = "；".join(
                c.get("document", "") for c in contexts if c.get("table") in self._expected
            )
            return {"answer": answer, "used": used}
        return {"answer": FALLBACK_ANSWER, "used": []}


def load_golden(path: Path | str) -> list[dict]:
    """读取 golden.json，校验每题含基本字段 + reference_answer（标准答案）。"""
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    for item in items:
        missing = [key for key in _REQUIRED_GOLDEN_FIELDS if key not in item]
        if missing:
            raise ValueError(f"golden 项缺少字段 {', '.join(missing)}: {item}")
    return items


def build_offline_retriever(use_bm25: bool = False, use_expansion: bool = True) -> Retriever:
    """离线环境：FakeEmbedder + 内存 VectorStore，用真实 processed 数据灌入 16 张表。"""
    settings = get_settings()
    # 抑制入库过程的 INFO 日志，评估输出只保留逐题结果
    logging.getLogger("app.ingest.pipeline").setLevel(logging.WARNING)
    store = VectorStore()
    embedder = FakeEmbedder()
    for table in ALL_TABLES:
        ingest_table(store, embedder, table, settings.processed_data_dir)
    return Retriever(store, embedder, use_bm25=use_bm25, use_expansion=use_expansion)


def build_online_service(
    use_bm25: bool = False, use_expansion: bool = True
) -> tuple[Retriever, AskService]:
    """真实服务：百炼 embedding + PersistentClient(data/chroma) + 千问 LLM。"""
    settings = get_settings()
    store = VectorStore(persist_dir=settings.chroma_dir)
    embedder = build_embedder(settings)
    retriever = Retriever(store, embedder, use_bm25=use_bm25, use_expansion=use_expansion)
    llm = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "",
        model=settings.llm_model,
    )
    service = AskService(
        retriever,
        llm,
        vector_top_k=settings.ask_vector_top_k,
        filter_top_k=settings.ask_filter_top_k,
        reranker=build_reranker(settings),
    )
    return retriever, service


def _evaluate_once(
    item: dict,
    *,
    retriever: Retriever,
    service: AskService | None,
    vector_top_k: int,
    filter_top_k: int,
) -> dict:
    """单次问答的逐项检查：兜底 / collection 覆盖 / 关键词。"""
    question = item["question"]
    expected = set(item.get("expect_collections") or [])

    # 命中 collection（检索级，top-10 候选池）
    records = retriever.retrieve(question, top_k=vector_top_k)
    hit_tables = {r.table for r in records}
    collections_ok = expected <= hit_tables

    # 问答链路
    if service is not None:
        result = service.ask(question)
    else:
        stub = OfflineStubLLM(sorted(expected))
        result = AskService(
            retriever, stub, vector_top_k=vector_top_k, filter_top_k=filter_top_k
        ).ask(question)

    answer = result.answer
    fallback_ok = (answer.strip() == FALLBACK_ANSWER) == bool(item.get("expect_fallback"))
    keywords = item.get("expect_keywords") or []
    keywords_ok = all(k in answer for k in keywords)

    return {
        "fallback_ok": fallback_ok,
        "collections_ok": collections_ok,
        "keywords_ok": keywords_ok,
        "hit_collections": sorted(hit_tables),
        "answer": answer,
        "passed": fallback_ok and collections_ok and keywords_ok,
    }


def aggregate_try_results(per_try: list[dict], *, expect_fallback: bool) -> dict:
    """从 per_try 明细聚合出题级结论（repeat 口径）。

    - 负例（expect_fallback=True）：全部次 PASS 才算 PASS，避免「3 次里 1 次兜底算过」的假阳性；
    - 正例：多数次（> repeat//2）PASS 即可，吸收 LLM 单次非确定性的抽样失真。
    聚合后的三项子检查与 passed 同口径（负例全过 / 正例多数），
    保持题级 passed == 三项子检查全过 的不变量。
    """
    repeat = len(per_try)

    def agg(key: str) -> bool:
        ok_count = sum(1 for t in per_try if t[key])
        return all(t[key] for t in per_try) if expect_fallback else ok_count > repeat // 2

    return {
        "repeat": repeat,
        "try_passed": sum(1 for t in per_try if t["passed"]),
        "fallback_ok": agg("fallback_ok"),
        "collections_ok": agg("collections_ok"),
        "keywords_ok": agg("keywords_ok"),
        "passed": agg("passed"),
    }


def evaluate(
    golden: list[dict],
    *,
    online: bool = False,
    use_bm25: bool = False,
    use_expansion: bool = True,
    repeat: int = 3,
    retriever: Retriever | None = None,
    vector_top_k: int = 10,
    filter_top_k: int = 5,
) -> list[dict]:
    """逐题评测（每题连问 repeat 次），返回每题结果 dict 列表。

    - offline：每问用 OfflineStubLLM（期望表来自本题），命中 collection 由
      retriever.retrieve 直接得出（top-10 候选池）；
    - online：走真实 AskService，命中 collection 同样由 retriever 复查得出；
    - use_bm25：开启 BM25 混合检索（默认纯向量）；
    - use_expansion：开启同义词查询扩展（默认开，与生产链路一致；CLI --expand 显式开关，
      默认关闭以便对比开启前后通过率）；
    - repeat：每题连问次数，结果含 per_try 明细与聚合结论（见 aggregate_try_results）。
    """
    if repeat < 1:
        raise ValueError(f"repeat 必须 >= 1，当前 {repeat}")
    if online:
        retriever, service = build_online_service(use_bm25=use_bm25, use_expansion=use_expansion)
    else:
        retriever = retriever or build_offline_retriever(
            use_bm25=use_bm25, use_expansion=use_expansion
        )
        service = None

    results: list[dict] = []
    for item in golden:
        per_try = [
            _evaluate_once(
                item,
                retriever=retriever,
                service=service,
                vector_top_k=vector_top_k,
                filter_top_k=filter_top_k,
            )
            for _ in range(repeat)
        ]
        agg = aggregate_try_results(per_try, expect_fallback=bool(item.get("expect_fallback")))
        results.append(
            {
                "id": item.get("id", ""),
                "question": item["question"],
                "repeat": agg["repeat"],
                "try_passed": agg["try_passed"],
                "per_try": per_try,
                "fallback_ok": agg["fallback_ok"],
                "collections_ok": agg["collections_ok"],
                "keywords_ok": agg["keywords_ok"],
                "hit_collections": sorted(
                    {table for t in per_try for table in t["hit_collections"]}
                ),
                "passed": agg["passed"],
            }
        )
    return results


def pass_rate(results: list[dict]) -> float:
    """总通过率；结果为空返回 0.0。"""
    if not results:
        return 0.0
    return sum(1 for r in results if r["passed"]) / len(results)


def _failed_reason(result: dict) -> str:
    """列出未通过的检查项（逗号分隔）。"""
    reasons = []
    if not result["fallback_ok"]:
        reasons.append("兜底不符预期")
    if not result["collections_ok"]:
        reasons.append("collection 未覆盖")
    if not result["keywords_ok"]:
        reasons.append("关键词缺失")
    return "；".join(reasons)


def print_report(
    results: list[dict],
    *,
    online: bool,
    use_bm25: bool,
    repeat: int = 1,
    use_expansion: bool = False,
) -> None:
    """逐题 PASS/FAIL 表 + 总通过率（repeat>1 时标注多数制口径与每题通过次数）。"""
    mode = "online" if online else "offline"
    mode += "+bm25" if use_bm25 else "+vector"
    mode += "+expand" if use_expansion else ""
    mode += f"+repeat{repeat}" if repeat > 1 else ""
    print(f"逐题结果（{mode}）: {len(results)} 题")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        detail = "" if r["passed"] else f"（{_failed_reason(r)}）"
        if r.get("repeat", 1) > 1:
            detail = f"{r['try_passed']}/{r['repeat']} 次通过" + detail
        print(f"  {r['id']:>4} {mark}  {r['question']} {detail}")
    rate = pass_rate(results)
    passed = sum(1 for r in results if r["passed"])
    print(f"总通过率：{passed}/{len(results)} = {rate * 100:.1f}%")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="golden set 评估：逐题 PASS/FAIL + 总通过率")
    parser.add_argument("--online", action="store_true", help="走真实服务（百炼 embedding + 千问 LLM），默认离线")
    parser.add_argument("--bm25", action="store_true", help="开启 BM25 混合检索（默认纯向量）")
    parser.add_argument(
        "--expand",
        action="store_true",
        help="开启同义词查询扩展（CLI 默认关闭，便于对比开启前后通过率）",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=3,
        help="每题连问次数（默认 3）：正例多数次 PASS 判过，负例需全部次 PASS；结果记录 per_try 明细",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help=f"把结果写到 JSON 文件（默认 {DEFAULT_RESULT_FILE}）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    golden = load_golden(GOLDEN_FILE)
    results = evaluate(
        golden,
        online=args.online,
        use_bm25=args.bm25,
        use_expansion=args.expand,
        repeat=args.repeat,
    )
    print_report(
        results,
        online=args.online,
        use_bm25=args.bm25,
        use_expansion=args.expand,
        repeat=args.repeat,
    )

    out_path = args.json_out or DEFAULT_RESULT_FILE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": ("online" if args.online else "offline")
        + ("+bm25" if args.bm25 else "+vector")
        + ("+expand" if args.expand else ""),
        "repeat": args.repeat,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "pass_rate": round(pass_rate(results), 4),
        "results": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"结果已写入 {out_path}")


if __name__ == "__main__":
    main()
