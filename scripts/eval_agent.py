"""Agent 多跳问题评估：读 data/eval/agent_golden.json 逐题跑 BadmintonAgent，输出通过率。

用法：
    .venv/Scripts/python.exe -m scripts.eval_agent                 # 离线（stub LLM + FakeEmbedder）
    .venv/Scripts/python.exe -m scripts.eval_agent --online        # 真实链路（百炼 embedding + 千问 LLM）
    .venv/Scripts/python.exe -m scripts.eval_agent --online --json-out data/eval/agent_result.json

逐题检查三项（全过才 PASS）：
    route_ok      路由命中 expect_route；
    fallback_ok   兜底是否符合 expect_fallback；
    keywords_ok   答案包含全部 expect_keywords。
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
from pathlib import Path

from app.agent.graph import BadmintonAgent
from app.core.config import BASE_DIR, get_settings
from app.ingest.embedder import FakeEmbedder, build_embedder
from app.ingest.pipeline import ingest_table
from app.ingest.serializer import ALL_TABLES
from app.ingest.store import VectorStore
from app.rag.llm import FALLBACK_ANSWER, LLMClient
from app.rag.retriever import Retriever

logger = logging.getLogger(__name__)

GOLDEN_FILE = BASE_DIR / "data" / "eval" / "agent_golden.json"
DEFAULT_RESULT_FILE = BASE_DIR / "data" / "eval" / "agent_result.json"


class OfflineAgentLLM(LLMClient):
    """离线评估用 stub：按期望 route 返回，不触网。"""

    def __init__(self, route: str) -> None:
        self._route = route

    def complete(self, messages, *, json_mode=False) -> str:
        system = messages[0]["content"] if messages else ""
        if "路由助手" in system:
            return f'{{"route": "{self._route}"}}'
        if "校验员" in system:
            return '{"supported": true}'
        if json_mode and "问答助手" in system:
            # 生成：拼上下文文本作为回答（便于关键词检查）
            user = messages[-1]["content"] if messages else ""
            ctx_text = user.split("检索内容：", 1)[-1] if "检索内容：" in user else ""
            return json.dumps({"answer": ctx_text, "used": []}, ensure_ascii=False)
        return "你好！"

    def extract_filters(self, question: str) -> dict:
        return {}


def build_offline_agent() -> BadmintonAgent:
    """离线：FakeEmbedder + 内存库 + 全表入库。"""
    settings = get_settings()
    logging.getLogger("app.ingest.pipeline").setLevel(logging.WARNING)
    store = VectorStore()
    embedder = FakeEmbedder()
    for table in ALL_TABLES:
        ingest_table(store, embedder, table, settings.processed_data_dir)
    retriever = Retriever(store, embedder, use_bm25=False)
    return BadmintonAgent(retriever, OfflineAgentLLM("multi"), memory=None)


def build_online_agent(enable_wiki: bool = False) -> BadmintonAgent:
    """在线：百炼 embedding + PersistentClient + 千问 LLM。

    enable_wiki=True 时额外装配 WikiNavigator（需先 `python -m scripts.build_wiki --index`）。
    """
    settings = get_settings()
    store = VectorStore(persist_dir=settings.chroma_dir)
    embedder = build_embedder(settings)
    retriever = Retriever(store, embedder, use_bm25=True)
    llm = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "",
        model=settings.llm_model,
    )
    wiki = None
    if enable_wiki:
        from app.wiki.navigator import build_navigator

        wiki = build_navigator(
            store, retriever, llm, settings.wiki_dir, settings.processed_data_dir, embedder=embedder
        )
        if wiki is None:
            raise SystemExit(
                "wiki 模式不可用：请先运行 python -m scripts.build_wiki --index（编译 + 建 wiki 索引）"
            )
    return BadmintonAgent(
        retriever, llm,
        vector_top_k=settings.ask_vector_top_k,
        filter_top_k=settings.ask_filter_top_k,
        wiki=wiki,
        default_mode="wiki" if enable_wiki else "classic",
    )


def load_golden(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    for item in items:
        for key in ("id", "question", "expect_keywords", "expect_fallback"):
            if key not in item:
                raise ValueError(f"golden 项缺少字段 {key}: {item}")
        # 兼容 expect_route（单值）与 expect_routes（允许集合）
        if "expect_routes" in item:
            item["expect_routes"] = [str(r) for r in item["expect_routes"]]
        elif "expect_route" in item:
            item["expect_routes"] = [str(item["expect_route"])]
        else:
            raise ValueError(f"golden 项缺少路由字段: {item.get('id')}")
    return items


def evaluate(golden: list[dict], *, online: bool, repeat: int = 1) -> list[dict]:
    agent = build_online_agent() if online else build_offline_agent()
    results: list[dict] = []
    for item in golden:
        # 每题连问 repeat 次，PASS 取多数（缓解 LLM 非确定性导致的单次抽样失真）
        attempts: list[dict] = []
        for _ in range(repeat):
            state = agent.invoke(
                {
                    "question": item["question"],
                    "session_id": "eval",
                    "history": [],
                    "contexts": [],
                    "sources": [],
                    "sub_questions": [],
                    "retry_count": 0,
                    "trace": [],
                }
            )
            answer = state.get("answer", "")
            route = state.get("route", "")
            route_ok = route in (item.get("expect_routes") or [])
            fallback_ok = (answer.strip() == FALLBACK_ANSWER) == bool(item["expect_fallback"])
            keywords_ok = all(k in answer for k in (item.get("expect_keywords") or []))
            attempts.append(
                {
                    "route": route,
                    "route_ok": route_ok,
                    "fallback_ok": fallback_ok,
                    "keywords_ok": keywords_ok,
                    "answer": answer[:120],
                    "passed": route_ok and fallback_ok and keywords_ok,
                }
            )
        # 多数制：过半数 PASS 即该题 PASS
        passed_count = sum(1 for a in attempts if a["passed"])
        passed = passed_count >= (repeat + 1) // 2 if repeat > 1 else bool(attempts[0]["passed"])
        results.append(
            {
                "id": item["id"],
                "question": item["question"],
                "route": attempts[0]["route"],
                "expect_routes": item.get("expect_routes"),
                "route_ok": any(a["route_ok"] for a in attempts),
                "fallback_ok": all(a["fallback_ok"] for a in attempts),
                "keywords_ok": any(a["keywords_ok"] for a in attempts),
                "answer": attempts[0]["answer"],
                "attempts": attempts,
                "passed": passed,
            }
        )
    return results


def print_report(results: list[dict], *, online: bool) -> None:
    mode = "online" if online else "offline"
    print(f"逐题结果（{mode}）: {len(results)} 题")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        reasons = []
        if not r["route_ok"]:
            reasons.append(f"路由{r['route']}不在{r['expect_routes']}")
        if not r["fallback_ok"]:
            reasons.append("兜底不符")
        if not r["keywords_ok"]:
            reasons.append("关键词缺失")
        detail = "" if r["passed"] else f"（{'；'.join(reasons)}）"
        print(f"  {r['id']:>4} {mark}  {r['question']}{detail}")
    passed = sum(1 for r in results if r["passed"])
    rate = passed / len(results) if results else 0
    print(f"总通过率：{passed}/{len(results)} = {rate * 100:.1f}%")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Agent 多跳问题评估")
    parser.add_argument("--online", action="store_true", help="真实链路（默认离线）")
    parser.add_argument("--repeat", type=int, default=1, help="每题连问次数，PASS 取多数（默认 1）")
    parser.add_argument("--json-out", type=Path, default=None, help="结果输出文件")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    golden = load_golden(GOLDEN_FILE)
    results = evaluate(golden, online=args.online, repeat=args.repeat)
    print_report(results, online=args.online)

    out_path = args.json_out or DEFAULT_RESULT_FILE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": "online" if args.online else "offline",
        "repeat": args.repeat,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "pass_rate": round((sum(1 for r in results if r["passed"]) / len(results)) if results else 0, 4),
        "results": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"结果已写入 {out_path}")


if __name__ == "__main__":
    main()
