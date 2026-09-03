"""查询改写：同义词扩展（query expansion）。

羽毛球语料里同一动作/属性常有多种写法（如「杀球/扣杀/劈杀」），直接检索
只命中一种写法会漏召回。expand() 命中同义词时返回「原查询 + 扩展查询」列表
（追加不替换，保持语义）；未命中返回 [原查询] 单元素列表，行为保持不变。

面试讲解点：查询改写做了同义词扩展——库里可能只出现一种写法，直接检索会漏；
扩展成多查询分别检索再按 id 合并去重（distance 取最优），召回更全。
多轮对话压缩是更进阶的改写，留给 Phase 3 Agentic 的记忆模块。
"""

from __future__ import annotations

# 同义词组：组内词互为同义/近义（按数据实际词频设计）。
# 扩展时以「查询中第一个出现的词」为锚点，对其余每个词各生成一个替换变体；
# 多组命中时逐个追加，不做组间笛卡尔积（避免查询数爆炸）。
SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("杀球", "扣杀", "劈杀"),
    ("搓球", "放网"),
    ("4U", "四U"),          # 数字-中文桥接
    ("平衡点", "重心", "头重"),
    ("磅数", "拉力"),
    ("耐打", "耐用", "结实"),
)


def expand(query: str, synonyms: tuple[tuple[str, ...], ...] = SYNONYMS) -> list[str]:
    """同义词扩展：命中返回 [原查询, 变体...]，未命中返回 [原查询]。

    - 以组内第一个命中词为锚点，对其余每个词生成一次替换变体（原查询恒在首位）；
    - 最终去重保序（同一变体可能由不同锚点生成，避免重复）；
    - synonyms 可注入（默认内置组；管理端 RAG 词典经 Retriever 追加，见 extra_synonyms）。
    """
    expanded = [query]
    for group in synonyms:
        anchor = next((t for t in group if t in query), None)
        if anchor is None:
            continue
        expanded.extend(query.replace(anchor, alt) for alt in group if alt != anchor)
    return list(dict.fromkeys(expanded))
