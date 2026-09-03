"""路由 Agent 测试：LLM 分类 + 关键词兜底。"""

from app.agent.router import classify, is_multi_signal


class StubLLM:
    """可控返回 route 的 stub。"""

    def __init__(self, route: str):
        self._route = route

    def complete(self, messages, *, json_mode=False) -> str:
        return f'{{"route": "{self._route}"}}'


def test_classify_llm_route():
    for route in ("equipment", "rules", "technique", "chitchat", "multi"):
        assert classify("任意问题", StubLLM(route)) == route


def test_classify_invalid_route_falls_back():
    # LLM 返回非法 route → 关键词兜底
    result = classify("推荐一款4U的进攻拍", StubLLM("bogus"))
    assert result == "equipment"


def test_heuristic_equipment():
    assert classify("推荐一款4U的进攻拍", None) == "equipment"
    assert classify("哪款球线耐打", None) == "equipment"


def test_heuristic_rules():
    assert classify("发球时击球点高度限制", None) == "rules"
    assert classify("单双打场地边界区别", None) == "rules"


def test_heuristic_technique():
    assert classify("正手握拍要领", None) == "technique"
    assert classify("拉吊突击战术怎么打", None) == "technique"


def test_heuristic_chitchat():
    assert classify("你好", None) == "chitchat"
    assert classify("谢谢", None) == "chitchat"


def test_heuristic_multi():
    assert classify("夏天和冬天分别选什么球速", None) == "multi"
    assert classify("新手该买什么拍先练什么步法", None) == "multi"


def test_classify_llm_document_route():
    assert classify("任意问题", StubLLM("document")) == "document"


def test_heuristic_document():
    assert classify("上传的规则手册里发球怎么判", None) == "document"
    assert classify("这个文档里写了什么", None) == "document"
    assert classify("图里那个动作叫什么", None) == "document"
    # 文档信号优先于跨类混合/单类（手册+规则 → document，不是 rules/multi）
    assert classify("规则手册里发球怎么判罚", None) == "document"
    assert classify("图片里的技术要点", None) == "document"


def test_heuristic_document_not_override_rules_without_signal():
    # 无文档信号的纯规则问题仍走 rules
    assert classify("发球时击球点高度限制", None) == "rules"


def test_multi_signal():
    assert is_multi_signal("全软木和双拼球头对比") is True
    assert is_multi_signal("推荐一款球拍") is False
