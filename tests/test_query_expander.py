"""查询改写（同义词扩展）单测：命中扩展 / 中文数字桥接 / 无命中回退 / 保序去重。

纯函数测试，不触网。
"""

from app.rag.query_expander import SYNONYMS, expand


def test_synonym_hit_expands_to_original_plus_variants():
    # 杀球/扣杀/劈杀 组：命中「杀球」→ 原查询 + 扣杀/劈杀 变体（追加不改原查询）
    assert expand("怎么杀球") == ["怎么杀球", "怎么扣杀", "怎么劈杀"]


def test_synonym_hit_keeps_original_query_first():
    # 命中同义词时原查询恒在首位（追加不替换，避免改变语义）
    queries = expand("推荐一款耐打的羽毛球")
    assert queries[0] == "推荐一款耐打的羽毛球"
    assert "推荐一款耐用的羽毛球" in queries
    assert "推荐一款结实的羽毛球" in queries


def test_multiple_groups_expand_without_cross_product():
    # 多组命中时逐个追加，不做组间笛卡尔积（避免查询数爆炸）
    queries = expand("耐打球拍的平衡点")
    assert queries[0] == "耐打球拍的平衡点"
    # 耐打组 → 耐用/结实 变体
    assert "耐用球拍的平衡点" in queries
    assert "结实球拍的平衡点" in queries
    # 平衡点组 → 重心/头重 变体
    assert "耐打球拍的重心" in queries
    assert "耐打球拍的头重" in queries
    assert len(queries) == 5, "2 组 × (组内-1) 变体 + 原查询，无交叉组合"


def test_chinese_numeral_bridging():
    # 4U/四U 中文数字桥接：数字写法 ↔ 中文写法互相扩展
    assert expand("4U球拍") == ["4U球拍", "四U球拍"]
    assert expand("四U球拍") == ["四U球拍", "4U球拍"]


def test_no_hit_falls_back_to_single_original():
    # 未命中任何同义词 → 返回 [原查询] 单元素列表（保持行为不变）
    assert expand("今天天气如何") == ["今天天气如何"]
    assert expand("推荐一款红色的手胶") == ["推荐一款红色的手胶"]


def test_all_synonym_groups_defined():
    # 用户约定的六组同义词全部在册（组内首个词为锚点）
    assert {g[0] for g in SYNONYMS} == {"杀球", "搓球", "4U", "平衡点", "磅数", "耐打"}
