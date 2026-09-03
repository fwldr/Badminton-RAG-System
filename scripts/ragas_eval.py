"""手写 RAGAS 四指标评测（LLM-as-judge，不装 ragas 库）。

四指标（口径见 badminton-rag-phase1-tail.md 第 2 节）：

    faithfulness      回答是否忠于检索上下文（不编造）
        → 把回答按句子拆成「事实主张」→ 逐个问 judge 能否由上下文支撑
        → 支撑数 / 总主张数
    answer_relevancy  回答与问题的相关度
        → judge 对「是否切题、信息充分」打分 0~1
    context_precision 检索上下文中相关条目的占比
        → 每条上下文问 judge 是否与问题相关 → 相关数 / 总条数
    context_recall    标准答案的信息有多少被检索到
        → 把标准答案按句子拆成「信息点主张」→ 逐个问 judge 是否出现在上下文
        → 出现数 / 总主张数

judge = 复用 app.rag.llm.LLMClient（百炼 DashScope，OpenAI 兼容）作 LLM-as-judge；
FakeJudge（固定打分）供离线测试。不修改任何生产代码（judge 只用 LLMClient.complete()）。

用法：
    .venv/Scripts/python.exe -m scripts.ragas_eval                # 离线 FakeJudge
    .venv/Scripts/python.exe -m scripts.ragas_eval --online       # 真实千问 judge
    .venv/Scripts/python.exe -m scripts.ragas_eval --json-out data/eval/ragas_result.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
from pathlib import Path

from app.core.config import BASE_DIR, get_settings
from app.rag.llm import LLMClient
from app.rag.service import AskService
from scripts.eval_ask import (
    GOLDEN_FILE,
    OfflineStubLLM,
    build_offline_retriever,
    build_online_service,
    load_golden,
)

logger = logging.getLogger(__name__)

DEFAULT_RESULT_FILE = BASE_DIR / "data" / "eval" / "ragas_result.json"

METRIC_KEYS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


# ---------- judge 接口 ----------


def _parse_json_obj(text: str) -> dict:
    """解析 judge 输出的 JSON 对象（兼容 ```json 围栏、前后杂文本）；失败返回空 dict。"""
    if not text:
        return {}
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            data = json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def _context_block(contexts: list[str]) -> str:
    """上下文块：带 [n] 编号逐条列出，供 judge 引用。"""
    return "\n".join(f"[{i}] {c}" for i, c in enumerate(contexts, 1))


def _as_flag(value) -> int:
    """把 judge 的 0/1 判断转成 int 标志，兼容 bool/数字/文字（是/支撑/相关/出现…）。"""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("1", "true", "yes", "是", "支撑", "相关", "出现"):
            return 1
        if s in ("0", "false", "no", "否", "不支撑", "不相关", "未出现"):
            return 0
        try:
            return 1 if float(s) else 0
        except ValueError:
            return 0
    return 0


def _num_judge(judge, *, system: str, user: str, key: str, expected: int) -> list[int]:
    """逐项 0/1 判断：judge 返回 {key: [0/1,...]}；缺项补 0、超项截断到 expected 长度。"""
    try:
        raw = judge.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            json_mode=True,
        )
    except Exception:
        logger.exception("judge 逐项判断调用失败")
        raw = ""
    data = _parse_json_obj(raw)
    flags = [_as_flag(v) for v in data.get(key, [])]
    return (flags + [0] * expected)[:expected]


def _split_claims(judge, text: str, *, role: str) -> list[str]:
    """把 text 按句子拆成「主张」列表（role=回答 用于 faithfulness，标准答案 用于 context_recall）。

    拆分粒度限定「按句子」：每句一个主张，不拆碎片、不合并多句（避免分母爆炸、分数不稳）。
    """
    if not text.strip():
        return []
    label = "事实主张" if role == "回答" else "信息点主张"
    system = (
        "你是评测助手。把给定的" + role + "按句子拆分成若干「" + label + "」。\n"
        "规则：\n"
        "1. 每个主张必须是一个完整的陈述句，表达一个独立事实；\n"
        "2. 严格按句子拆分，不要把一句拆成碎片，也不要合并多个句子；\n"
        '3. 只输出 JSON，格式 {"claims": ["主张1", "主张2", ...]}。'
    )
    user = f"{role}：{text}"
    try:
        raw = judge.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            json_mode=True,
        )
    except Exception:
        logger.exception("judge 主张拆分调用失败")
        raw = ""
    data = _parse_json_obj(raw)
    return [str(c).strip() for c in data.get("claims", []) if str(c).strip()]


# ---------- 四指标 ----------


def judge_faithfulness(judge, answer: str, contexts: list[str]) -> float:
    """faithfulness = 能由上下文支撑的主张数 / 总主张数（主张按句子拆分）。"""
    claims = _split_claims(judge, answer, role="回答")
    if not claims or not contexts:
        return 0.0
    system = (
        "你是评测助手。判断以下每个「事实主张」能否由给定的检索上下文支撑。\n"
        "上下文是唯一事实来源；只要主张的核心信息能由上下文直接或合理推导出，就算能支撑（1），否则 0。\n"
        '只输出 JSON，格式 {"supported": [0, 1, ...]}，长度与主张数量一致。'
    )
    user = "主张：\n" + "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
    user += f"\n\n检索上下文：\n{_context_block(contexts)}"
    flags = _num_judge(judge, system=system, user=user, key="supported", expected=len(claims))
    return sum(flags) / len(claims)


def judge_answer_relevancy(judge, question: str, answer: str) -> float:
    """answer_relevancy = judge 对「回答是否切题、信息充分」的 0~1 打分。"""
    system = (
        "你是评测助手。对「回答是否切题、信息充分」打分 0~1 之间的小数。\n"
        "打分依据：是否直接回答了用户问题；信息是否充分；是否答非所问。\n"
        '只输出 JSON，格式 {"score": 0.9}。'
    )
    user = f"问题：{question}\n\n回答：{answer}"
    try:
        raw = judge.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            json_mode=True,
        )
    except Exception:
        logger.exception("judge 相关度打分调用失败")
        raw = ""
    data = _parse_json_obj(raw)
    try:
        score = float(data.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, score))


def judge_context_relevance(judge, question: str, contexts: list[str]) -> list[int]:
    """逐条判定检索条目与问题是否相关（1/0）。

    独立成函数是为了让 wiki 的 `context_precision_strict` 口径复用**同一个 judge 提示**：
    它把每条上下文展开回原始 record 后再判相关性（见 scripts.eval_agent_quality）。
    """
    if not contexts:
        return []
    system = (
        "你是评测助手。判断以下每条检索条目是否与用户问题相关。\n"
        "只要条目的信息对回答该问题有用（哪怕是背景或对比信息），就算相关（1），否则 0。\n"
        '只输出 JSON，格式 {"relevant": [1, 0, ...]}，长度与条目数量一致。'
    )
    user = f"问题：{question}\n\n" + "\n".join(
        f"条目{i}：{c}" for i, c in enumerate(contexts, 1)
    )
    return _num_judge(judge, system=system, user=user, key="relevant", expected=len(contexts))


def judge_context_precision(judge, question: str, contexts: list[str]) -> float:
    """context_precision = 检索上下文中相关条目的占比。"""
    if not contexts:
        return 0.0
    return sum(judge_context_relevance(judge, question, contexts)) / len(contexts)


def judge_context_recall(judge, reference_answer: str, contexts: list[str]) -> float:
    """context_recall = 标准答案的信息点主张出现在检索上下文中的比例。"""
    claims = _split_claims(judge, reference_answer, role="标准答案")
    if not claims or not contexts:
        return 0.0
    system = (
        "你是评测助手。判断以下每个「信息点主张」的信息是否出现在给定的检索上下文中。\n"
        "只要主张的核心信息被上下文覆盖（直接或等价表述），就算出现（1），否则 0。\n"
        '只输出 JSON，格式 {"found": [0, 1, ...]}，长度与主张数量一致。'
    )
    user = "主张：\n" + "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
    user += f"\n\n检索上下文：\n{_context_block(contexts)}"
    flags = _num_judge(judge, system=system, user=user, key="found", expected=len(claims))
    return sum(flags) / len(claims)


# ---------- FakeJudge（离线测试） ----------


class FakeJudge:
    """离线测试用 judge：按系统提示特征返回固定 JSON，产出确定性分数。

    固定口径：
        faithfulness      1.0   （2 条主张全部能支撑）
        answer_relevancy  0.9   （固定打分）
        context_precision ceil(n/2)/n（n 条上下文中奇数位相关，如 3 条 → 2/3）
        context_recall    1.0   （2 条参考主张全部出现）
    """

    def complete(self, messages: list[dict], *, json_mode: bool = False) -> str:
        system = messages[0].get("content", "") if messages else ""
        user = messages[-1].get("content", "") if messages else ""
        if "按句子拆分成" in system:
            role = "回答" if "回答" in system else "标准答案"
            return json.dumps({"claims": [f"{role}主张一", f"{role}主张二"]}, ensure_ascii=False)
        if "能否由给定的检索上下文支撑" in system:
            n = len(re.findall(r"^\d+\.", user, flags=re.M))
            return json.dumps({"supported": [1] * n})
        if "是否出现在给定的检索上下文中" in system:
            n = len(re.findall(r"^\d+\.", user, flags=re.M))
            return json.dumps({"found": [1] * n})
        if "是否与用户问题相关" in system:
            n = len(re.findall(r"条目\d+：", user))
            return json.dumps({"relevant": [1 if i % 2 == 0 else 0 for i in range(n)]})
        if "是否切题" in system:
            return json.dumps({"score": 0.9})
        return "{}"


# ---------- 评测编排 ----------


def build_judge() -> LLMClient:
    """真实 judge：复用 LLMClient（百炼 DashScope，OpenAI 兼容）。"""
    settings = get_settings()
    return LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "",
        model=settings.llm_model,
    )


def run_ragas(
    golden: list[dict],
    *,
    online: bool = False,
    judge=None,
    retriever=None,
    vector_top_k: int = 10,
    filter_top_k: int = 5,
) -> list[dict]:
    """对 golden 逐题跑「RAG 问答 + 四指标」，返回每题分数 dict 列表。

    - 上下文口径：retriever.retrieve(question, top_k=vector_top_k) 的 top-N 检索结果
      （检索级候选池，近似生成阶段所见上下文；不修改生产代码，未暴露过滤后的生成窗口）；
    - offline：FakeEmbedder + 内存库 + OfflineStubLLM（弱语义代理，FakeJudge 固定打分，仅验证链路）；
    - online：真实千问 judge + 真实服务（百炼 embedding + 千问 LLM + data/chroma，开启 BM25，
      与生产链路一致）。
    """
    if online:
        retriever, service = build_online_service(use_bm25=True)
        judge = judge or build_judge()
    else:
        retriever = retriever or build_offline_retriever(use_bm25=False)
        service = None
        judge = judge or FakeJudge()

    results: list[dict] = []
    for item in golden:
        question = item["question"]
        # RAG 问答：拿 answer（与 top 上下文，上下文由 retriever 复查得出）
        if service is not None:
            ask_result = service.ask(question)
        else:
            stub = OfflineStubLLM(sorted(item.get("expect_collections") or []))
            ask_result = AskService(
                retriever, stub, vector_top_k=vector_top_k, filter_top_k=filter_top_k
            ).ask(question)
        answer = ask_result.answer
        contexts = [r.text for r in retriever.retrieve(question, top_k=vector_top_k)]

        results.append(
            {
                "id": item["id"],
                "question": question,
                "answer": answer,
                "num_contexts": len(contexts),
                "faithfulness": round(judge_faithfulness(judge, answer, contexts), 4),
                "answer_relevancy": round(judge_answer_relevancy(judge, question, answer), 4),
                "context_precision": round(judge_context_precision(judge, question, contexts), 4),
                "context_recall": round(
                    judge_context_recall(judge, item.get("reference_answer") or "", contexts), 4
                ),
            }
        )
    return results


def average_metrics(results: list[dict]) -> dict[str, float]:
    """各指标平均分；结果为空返回全 0。"""
    if not results:
        return {key: 0.0 for key in METRIC_KEYS}
    return {key: round(sum(r[key] for r in results) / len(results), 4) for key in METRIC_KEYS}


def print_report(results: list[dict], *, online: bool) -> None:
    """逐题四指标表 + 汇总平均。"""
    mode = "online" if online else "offline"
    print(f"RAGAS 四指标（{mode}，手写 LLM-as-judge）: {len(results)} 题")
    print(f"{'id':>4} {'faith':>6} {'relev':>6} {'prec':>6} {'recall':>6}")
    for r in results:
        print(
            f"{r['id']:>4} {r['faithfulness']:>6.3f} {r['answer_relevancy']:>6.3f} "
            f"{r['context_precision']:>6.3f} {r['context_recall']:>6.3f}"
        )
    avg = average_metrics(results)
    print("平均：" + "  ".join(f"{k}={v:.3f}" for k, v in avg.items()))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="手写 RAGAS 四指标评测（LLM-as-judge，不装 ragas 库）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--online", action="store_true", help="用真实千问作为 judge（默认离线 FakeJudge）")
    group.add_argument("--offline", action="store_true", help="用 FakeJudge 离线评测（默认）")
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help=f"把结果写到 JSON 文件（默认 {DEFAULT_RESULT_FILE}）",
    )
    parser.add_argument("--top-k", type=int, default=10, help="喂给 judge 的上下文条数（默认 10）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    online = args.online
    if online and not get_settings().llm_api_key:
        print("未配置 LLM_API_KEY，无法在线评测；请设置后重试，或用默认离线 FakeJudge。")
        return

    golden = load_golden(GOLDEN_FILE)
    results = run_ragas(golden, online=online, vector_top_k=args.top_k)
    print_report(results, online=online)

    out_path = args.json_out or DEFAULT_RESULT_FILE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": "online" if online else "offline",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "total": len(results),
        "average": average_metrics(results),
        "results": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"结果已写入 {out_path}")


if __name__ == "__main__":
    main()
