"""回答清洗：还原 \\n 转义 + 剥离末尾「来源：…」标注（Phase 10 用户反馈修复）。"""

from app.agent.graph import _DEFAULT_GENERATE_SYSTEM, _clean_answer


def test_unescape_literal_newlines():
    """模型把换行写成字面 \\n（双转义）→ 还原为真实换行；内部换行保留。"""
    raw = "反手高远球动作要领。\\n\\n转体发力。\\n\\n来源：手法技术 反手技术"
    out = _clean_answer(raw)
    assert "\n" in out
    assert "\n\n" in out
    assert "\\n" not in out
    assert "来源" not in out


def test_strip_trailing_source_line():
    """末尾独立成行的「来源：…」标注整行去掉（多来源用；分隔）。"""
    raw = "握拍时拇指顶在拍柄内侧。\n\n来源：手法技术 反手技术；来源：手法技术 握拍基础"
    out = _clean_answer(raw)
    assert out == "握拍时拇指顶在拍柄内侧。"


def test_strip_inline_trailing_source():
    """无换行、紧贴正文的末尾「来源：…」也去掉。"""
    raw = "适合进攻型打法。来源：品牌 A 型号 X"
    out = _clean_answer(raw)
    assert out == "适合进攻型打法。"


def test_keep_normal_text():
    """普通回答不误伤（含「来源文件」等无关词）。"""
    raw = "4U 球拍重量约 80-84 克。来源文件：球拍.csv"
    assert _clean_answer(raw) == "4U 球拍重量约 80-84 克。来源文件：球拍.csv"


def test_unescape_tab_and_quotes():
    """\\t 与 \\" 转义一并还原。"""
    raw = '第一点\\t第二点 \\"引号\\"'
    out = _clean_answer(raw)
    assert "\t" in out
    assert '"' in out
    assert "\\" not in out


def test_default_prompt_no_source_in_answer():
    """默认生成 prompt 不再要求（且明确禁止）在回答正文输出来源标注。"""
    assert "末尾附来源" not in _DEFAULT_GENERATE_SYSTEM
    assert "不要输出「来源：…」等引用标注" in _DEFAULT_GENERATE_SYSTEM
