"""路由 Agent：问题分类 → equipment / rules / technique / chitchat / multi。

LLM 输出 {"route": "..."}，解析失败时按关键词启发式兜底（默认 equipment）。
"""

from __future__ import annotations

import re

from app.rag.llm import LLMClient, parse_filter_json

# 合法路由
ROUTES = ("equipment", "rules", "technique", "chitchat", "multi", "document")

_ROUTE_SYSTEM = (
    "你是羽毛球问答系统的路由助手。判断用户问题属于哪一类，只输出 JSON：{\"route\": \"...\"}。\n"
    "分类规则：\n"
    "1. equipment：涉及球拍/球线/手胶/球鞋/羽毛球的品牌、型号、重量、磅数、平衡点、材质、价格等规格参数；\n"
    "   示例：推荐一款4U的进攻拍 / 哪款球线耐打 / 李宁有什么羽毛球鞋\n"
    "2. rules：涉及比赛规则、判罚、场地、发球、得分、边界等；\n"
    "   示例：发球时击球点高度限制 / 单双打场地边界区别 / 发球过高怎么判罚\n"
    "3. technique：涉及技术动作、战术、步法、手法、训练方法等；\n"
    "   示例：正手握拍要领 / 拉吊突击战术怎么打 / 新手先学什么步法\n"
    "4. document：问题指向知识库中上传的文档/资料/PDF/图片内容（含 文档/资料/手册/上传/PDF/图片/截图/图示/文件 等信号）；\n"
    "   示例：上传的规则手册里发球怎么判 / 这个文档里写了什么 / 图里那个动作叫什么\n"
    "5. chitchat：问候、感谢、闲聊、无检索意图；\n"
    "   示例：你好 / 谢谢 / 你是做什么的\n"
    "6. multi：需要组合多类知识才能回答，或含 和/与/对比/分别/同时 等多跳信号；\n"
    "   示例：夏天和冬天分别选什么球速 / 新手该买什么拍先练什么步法 / 全软木和双拼球头哪个适合比赛\n"
    "7. 若问题引用了之前对话（含 刚才/之前/前面/那个/这款 等指代词），按所指内容的类别分类，"
    "不要判为 chitchat；\n"
    "优先级：multi > document > rules > technique > equipment > chitchat。只输出一个 JSON 对象，不要输出其他文字。"
)


# 关键词启发式兜底（LLM 解析失败时用，按优先级从上到下）
_HEURISTIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("multi", ("和", "与", "对比", "分别", "同时", "以及", "都要")),
    ("document", ("文档", "资料", "手册", "上传", "pdf", "图片", "截图", "图示", "文件", "图里")),
    ("rules", ("规则", "判罚", "场地", "发球", "得分", "边界", "违例", "限制")),
    ("technique", ("技术", "战术", "步法", "手法", "要领", "怎么打", "怎么练", "训练")),
    ("equipment", ("球拍", "球线", "手胶", "球鞋", "羽毛球", "磅数", "重量", "平衡", "品牌", "推荐")),
    ("chitchat", ("你好", "您好", "谢谢", "感谢", "再见", "拜拜", "在吗", "哈喽", "嗨", "hello", "hi")),
)

# 跨类混合 → multi 的类别信号（同时命中两类以上即判 multi）
_MULTI_MIX_SIGNALS: dict[str, tuple[str, ...]] = {
    "equipment": ("球拍", "球线", "手胶", "球鞋", "羽毛球", "磅数", "重量", "平衡", "品牌", "买", "推荐"),
    "technique": ("技术", "战术", "步法", "手法", "要领", "怎么打", "怎么练", "训练", "练"),
    "rules": ("规则", "判罚", "场地", "发球", "得分", "边界"),
}


def _strong_multi_signal(question: str) -> bool:
    """强多跳信号：对比/选择词（还是/或/vs/区别）与追问词（影响/后果/分别/怎么选/哪个/该选）同时出现。

    覆盖 LLM 易误判为单类装备的问题，如「初学者应该选3U还是4U，太沉了会有什么影响」。
    注意范围必须收窄（不含「适合/推荐」等宽词），否则会把单类推荐问题劫持成 multi 反而劣化。
    """
    compare = any(k in question for k in ("还是", "或", "vs", "VS", "区别", "相较"))
    ask = any(k in question for k in ("影响", "后果", "分别", "怎么选", "哪个", "该选", "选哪"))
    return compare and ask


def classify(question: str, llm: LLMClient | None, history: list[dict] | None = None) -> str:
    """路由分类：LLM 优先（可带历史做指代消解），解析失败走关键词兜底，再兜底 equipment。

    强多跳信号（_strong_multi_signal）优先于 LLM 单类判定——保证「对比+追问」类问题走 multi。
    """
    if _strong_multi_signal(question):
        return "multi"
    if llm is not None:
        try:
            hist_block = ""
            if history:
                lines = [f"{m.get('role')}: {m.get('content', '')}" for m in history[-4:]]
                hist_block = "\n历史对话：\n" + "\n".join(lines)
            text = llm.complete(
                [
                    {"role": "system", "content": _ROUTE_SYSTEM},
                    {"role": "user", "content": f"{hist_block}\n当前问题：{question}"},
                ],
                json_mode=True,
            )
            data = parse_filter_json(text)
            route = str(data.get("route", "")).strip()
            if route in ROUTES:
                return route
        except Exception:
            pass
    return _heuristic_route(question)


def _heuristic_route(question: str) -> str:
    """关键词启发式兜底：先查 multi 直接信号，再查 document 信号，再查跨类混合，再查单类关键词。"""
    # 1. multi 直接关键词（和/与/对比/分别…）
    multi_kws = _HEURISTIC_RULES[0][1]
    if any(k in question for k in multi_kws):
        return "multi"
    # 2. document 信号（文档/资料/手册/上传/PDF/图片…）优先于跨类混合：
    #    「规则手册里发球怎么判」应路由到 document（查上传内容），而不是 multi
    doc_kws = _HEURISTIC_RULES[1][1]
    if any(k in question for k in doc_kws):
        return "document"
    # 3. 跨类混合：装备+技术 / 装备+规则 同时出现 → multi
    hit_classes = [cls for cls, kws in _MULTI_MIX_SIGNALS.items() if any(k in question for k in kws)]
    if len(hit_classes) >= 2:
        return "multi"
    # 4. 单类关键词
    for route, keywords in _HEURISTIC_RULES[2:]:
        if any(k in question for k in keywords):
            return route
    return "equipment"


def is_multi_signal(question: str) -> bool:
    """多跳信号快速判断（路由后仍可用）。"""
    return any(k in question for k in _HEURISTIC_RULES[0][1])
