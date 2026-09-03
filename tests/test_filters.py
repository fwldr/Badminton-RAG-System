"""属性过滤单元测试。"""

from app.rag.filters import FILTERABLE_FIELDS, apply_filters


def _rec(meta: dict) -> dict:
    return {"metadata": meta}


def test_exact_value_substring_match():
    records = [
        _rec({"拍身重量(U)": "3U,4U", "品牌": "尤尼克斯 YONEX"}),
        _rec({"拍身重量(U)": "5U", "品牌": "李宁"}),
    ]
    out = apply_filters(records, {"拍身重量(U)": ["4U"]})
    assert len(out) == 1
    assert out[0]["metadata"]["品牌"] == "尤尼克斯 YONEX"


def test_color_red_matches_comma_list():
    # 「红色」去尾「色」→「红」，命中多色枚举里的「红」
    records = [_rec({"颜色": "黑、红、白、黄"}), _rec({"颜色": "黑、白、黄"})]
    out = apply_filters(records, {"颜色": ["红色"]})
    assert len(out) == 1
    assert out[0]["metadata"]["颜色"] == "黑、红、白、黄"


def test_color_blue_matches_single_blue():
    records = [_rec({"颜色": "蓝"}), _rec({"颜色": "绿"})]
    out = apply_filters(records, {"颜色": ["蓝色"]})
    assert len(out) == 1
    assert out[0]["metadata"]["颜色"] == "蓝"


def test_color_fluorescent_green_no_normalization():
    # 「荧光绿」不以「色」结尾，不归一化，直接双向包含
    records = [_rec({"颜色": "荧光绿"}), _rec({"颜色": "红"})]
    out = apply_filters(records, {"颜色": ["荧光绿"]})
    assert len(out) == 1
    assert out[0]["metadata"]["颜色"] == "荧光绿"


def test_color_white_matches_enum():
    records = [_rec({"颜色": "黑、红、白、黄"}), _rec({"颜色": "蓝"})]
    out = apply_filters(records, {"颜色": ["白色"]})
    assert len(out) == 1
    assert out[0]["metadata"]["颜色"] == "黑、红、白、黄"


def test_color_not_present_fails():
    records = [_rec({"颜色": "黑、白、黄"})]
    assert apply_filters(records, {"颜色": ["红色"]}) == []


def test_numeric_operator_gte():
    records = [_rec({"最高磅数": "30"}), _rec({"最高磅数": "26"}), _rec({})]
    out = apply_filters(records, {"最高磅数>=": 28})
    assert len(out) == 1
    assert out[0]["metadata"]["最高磅数"] == "30"


def test_numeric_unparseable_skipped():
    records = [_rec({"最高磅数": "未知"})]
    assert apply_filters(records, {"最高磅数>=": 28}) == []


def test_missing_field_fails():
    records = [_rec({"品牌": "尤尼克斯"})]
    assert apply_filters(records, {"适合水平": ["专业级"]}) == []


def test_unknown_field_ignored():
    records = [_rec({"品牌": "尤尼克斯"})]
    out = apply_filters(records, {"不存在的字段": ["x"]})
    assert out == records


def test_multiple_conditions_and():
    records = [
        _rec({"拍身重量(U)": "4U", "品牌": "尤尼克斯"}),
        _rec({"拍身重量(U)": "4U", "品牌": "李宁"}),
        _rec({"拍身重量(U)": "5U", "品牌": "尤尼克斯"}),
    ]
    out = apply_filters(records, {"拍身重量(U)": ["4U"], "品牌": ["尤尼克斯"]})
    assert len(out) == 1
    assert out[0]["metadata"]["品牌"] == "尤尼克斯"


def test_single_string_value_normalized_to_list():
    records = [_rec({"拍身重量(U)": "4U"}), _rec({"拍身重量(U)": "5U"})]
    out = apply_filters(records, {"拍身重量(U)": "4U"})
    assert len(out) == 1


def test_conditions_none_returns_all():
    records = [_rec({"品牌": "A"})]
    assert apply_filters(records, None) == records


def test_filterable_fields_include_key_fields():
    assert "拍身重量(U)" in FILTERABLE_FIELDS
    assert "最高磅数" in FILTERABLE_FIELDS
    assert "别名" in FILTERABLE_FIELDS
    assert "来源" in FILTERABLE_FIELDS
    assert "来源文件" in FILTERABLE_FIELDS
