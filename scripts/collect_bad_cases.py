"""bad case 收集与报告：读 quality_result.json + feedback 表（点踩）→ 分类 → data/eval/bad_cases.md。

分类规则（见 badminton-rag-phase4-plan.md 2.4）：
    router       路由不在 expect_routes（评估 FAIL）
    data         知识库无答案兜底「知识库中暂无相关信息」
    faithfulness faithfulness < 0.85（回答不忠于上下文 = 幻觉）
    relevancy     answer_relevancy < 0.8（答非所问）
    retrieval    context_precision < 0.6（检索到不相关内容）

用法：
    .venv/Scripts/python.exe -m scripts.collect_bad_cases
    .venv/Scripts/python.exe -m scripts.collect_bad_cases --quality data/eval/quality_result.json --out data/eval/bad_cases.md
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

from app.core.config import BASE_DIR
from app.db.database import get_conn, init_db
from app.rag.llm import FALLBACK_ANSWER

DEFAULT_QUALITY_FILE = BASE_DIR / "data" / "eval" / "quality_result.json"
DEFAULT_REPORT_FILE = BASE_DIR / "data" / "eval" / "bad_cases.md"

# 分类 → 改进方向（bad case 复盘的核心产出）
_FIX_DIRECTIONS = {
    "router": "路由 prompt 补示例、关键词表补词、多跳信号调整",
    "data": "补充知识表/规格表（如 Phase 3 补「规格常识」的先例），或修正语料",
    "faithfulness": "收紧生成 prompt（禁止编造）、校验阈值、上下文去噪（减少无关条目）",
    "relevancy": "增强查询（历史压缩质量）、decompose 拆解质量、重排精排",
    "retrieval": "调 top_k / max_per_table、补同义词（query_expander）、优化定向 collection 子集",
    "feedback": "人工复核后转以上分类",
}

_THRESHOLDS = {"faithfulness": 0.85, "answer_relevancy": 0.8, "context_precision": 0.6}


def classify(item: dict) -> tuple[str, str]:
    """返回 (分类, 现象描述)。优先级：路由错 > 无答案兜底 > 幻觉 > 答非所问 > 检索差。"""
    route = item.get("route", "")
    expect = item.get("expect_routes") or []
    if expect and route not in expect:
        return "router", f"路由 {route!r} 不在期望 {expect}"
    if FALLBACK_ANSWER in item.get("answer", ""):
        return "data", "知识库无答案兜底"
    reasons = []
    if item.get("faithfulness", 1.0) < _THRESHOLDS["faithfulness"]:
        reasons.append(f"faithfulness={item['faithfulness']}")
    if item.get("answer_relevancy", 1.0) < _THRESHOLDS["answer_relevancy"]:
        reasons.append(f"answer_relevancy={item['answer_relevancy']}")
    if item.get("context_precision", 1.0) < _THRESHOLDS["context_precision"]:
        reasons.append(f"context_precision={item['context_precision']}")
    if not reasons:
        return "retrieval", "答案质量不达标（关键词缺失或覆盖不足）"
    if "faithfulness" in reasons[0]:
        return "faithfulness", "；".join(reasons)
    if "answer_relevancy" in reasons[0]:
        return "relevancy", "；".join(reasons)
    return "retrieval", "；".join(reasons)


def load_quality(path: Path) -> tuple[str, list[dict]]:
    """读取 quality_result.json，返回 (mode, results)。"""
    with open(path, encoding="utf-8") as f:
        report = json.load(f)
    return report.get("mode", "offline"), list(report.get("results") or [])


def collect_feedback_cases(limit: int = 20) -> list[dict]:
    """从 SQLite 取最近的点踩记录作为在线 bad case 来源。"""
    init_db()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM feedback WHERE rating = -1 ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _case_row(index: int, source: str, item: dict) -> str:
    cat, reason = classify(item)
    return (
        f"| {index} | {item.get('question', '')[:60]} | {reason} | {cat} | "
        f"{_FIX_DIRECTIONS.get(cat, '')} | 待改 |"
    )


def build_report(mode: str, quality_items: list[dict], feedback_items: list[dict]) -> str:
    mode_label = "在线" if mode == "online" else "离线"
    lines = [
        "# Bad Case 清单",
        "",
        f"> 生成时间：{datetime.datetime.now().isoformat(timespec='seconds')}",
        f"> 来源：{mode_label}评测质量结果 {len(quality_items)} 条 + 用户点踩 {len(feedback_items)} 条",
        "",
        "| 编号 | 题目 | 现象 | 分类 | 改进方向 | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    index = 0
    for item in quality_items:
        index += 1
        lines.append(_case_row(index, "quality", item))
    for item in feedback_items:
        index += 1
        lines.append(
            f"| {index} | {item.get('question', '')[:60]} | 用户点踩（{item.get('comment') or '无评论'}） | feedback | "
            f"{_FIX_DIRECTIONS['feedback']} | 待改 |"
        )
    lines.append("")
    lines.append("> 状态流转：待改 → 已改 → 已复测（改进后重跑 eval_agent_quality 验证指标回升）。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="bad case 收集与报告生成")
    parser.add_argument("--quality", type=Path, default=None, help=f"quality_result.json 路径（默认 {DEFAULT_QUALITY_FILE}）")
    parser.add_argument("--out", type=Path, default=None, help=f"报告输出路径（默认 {DEFAULT_REPORT_FILE}）")
    parser.add_argument("--feedback-limit", type=int, default=20, help="最多收集多少条点踩记录")
    args = parser.parse_args()

    quality_path = args.quality or DEFAULT_QUALITY_FILE
    if not quality_path.exists():
        print(f"未找到质量结果文件 {quality_path}，请先运行 scripts.eval_agent_quality --online")
        return
    mode, quality_items = load_quality(quality_path)
    feedback_items = collect_feedback_cases(args.feedback_limit)

    out_path = args.out or DEFAULT_REPORT_FILE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_report(mode, quality_items, feedback_items), encoding="utf-8")
    print(f"bad case 报告已写入 {out_path}（{mode} {len(quality_items)} 条质量 + {len(feedback_items)} 条点踩）")


if __name__ == "__main__":
    main()
