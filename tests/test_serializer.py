"""行级序列化单元测试：只拼非空字段。"""

from app.ingest.serializer import (
    ALL_TABLES,
    KNOWLEDGE_TABLES,
    SPEC_TABLES,
    serialize_bwf_rule,
    serialize_cork,
    serialize_durability,
    serialize_feather_grade,
    serialize_feather_type,
    serialize_flight,
    serialize_grip,
    serialize_penalty,
    serialize_racket,
    serialize_shoe,
    serialize_shuttlecock,
    serialize_speed_grade,
    serialize_string,
    serialize_tactic,
    serialize_technique,
)


def test_serialize_racket_full_row():
    row = {
        "品牌": "尤尼克斯 YONEX",
        "型号": "天斧99",
        "别名": "ASTROX 99",
        "拍身重量(U)": "3U,4U",
        "拍柄粗细": "G4,G5,G6",
        "中管韧度": "硬",
        "拉线磅数": "3U（21-29LBS）",
        "平衡点": "305mm",
        "平衡点类别": "头重(进攻)",
        "打法类型": "进攻型",
        "适合水平": "专业级",
        "适合人群": "适合力量好、追求一锤定音重杀的进攻型选手",
        "参考价": "1980元",
    }
    out = serialize_racket(row)
    assert out.startswith("尤尼克斯 YONEX 天斧99(ASTROX 99)")
    assert "重量3U,4U" in out
    assert "平衡点305mm（头重(进攻)）" in out
    assert "适合专业级。适合力量好、追求一锤定音重杀的进攻型选手。参考价格：1980元" in out


def test_serialize_racket_sparse_row_skips_empty():
    row = {"品牌": "其他 OTHER", "型号": "击破M2.0", "拍身重量(U)": "4U", "来源": "球拍.csv"}
    out = serialize_racket(row)
    assert out == "其他 OTHER 击破M2.0，重量4U"
    assert "拍柄" not in out
    assert "中杆" not in out
    assert "参考价格" not in out


def test_serialize_racket_alias_empty_no_parens():
    row = {"品牌": "李宁", "型号": "雷霆90", "别名": "", "拍身重量(U)": "4U"}
    assert serialize_racket(row) == "李宁 雷霆90，重量4U"


def test_serialize_shuttlecock_full_row():
    row = {
        "品牌": "亚狮龙 RSL",
        "名称": "TOURNEY NO.5",
        "球速": "74,75,76,77",
        "适用温度": "中温(20-25°C)",
        "羽毛类别": "鸭毛",
        "毛片级别": "",
        "毛片等级": "大方",
        "毛片原始": "鹚鸪鸭大方",
        "球头类别": "双拼复合",
        "球头原始": "双拼复合软木",
    }
    out = serialize_shuttlecock(row)
    assert out.startswith("亚狮龙 RSL TOURNEY NO.5")
    assert "球速74,75,76,77（适合中温(20-25°C)）" in out
    assert "鸭毛大方（鹚鸪鸭大方）" in out
    assert "球头双拼复合（双拼复合软木）" in out


def test_serialize_string_full_row():
    row = {"品牌": "尤尼克斯 YONEX", "名称": "BG-80", "直径mm": "0.68", "竖横线直径": "竖线0.67、横线0.61", "材质": "高分子尼龙"}
    out = serialize_string(row)
    assert out == "尤尼克斯 YONEX BG-80，线径0.68mm竖线0.67、横线0.61，材质：高分子尼龙"


def test_serialize_string_sparse_skips_material():
    row = {"品牌": "傲时威 ASHAWAY", "名称": "Zymax 62", "直径mm": "0.62"}
    assert serialize_string(row) == "傲时威 ASHAWAY Zymax 62，线径0.62mm"


def test_serialize_grip_full_row():
    row = {"品牌": "尤尼克斯 YONEX", "名称": "AC102EX", "材质类别": "PU/聚氨酯", "材质": "聚氨酯", "颜色": "白色"}
    out = serialize_grip(row)
    assert out == "尤尼克斯 YONEX AC102EX，PU/聚氨酯手胶（聚氨酯）白色"


def test_serialize_grip_no_material():
    row = {"品牌": "其他 OTHER", "名称": "105EX", "材质类别": "", "材质": "", "颜色": ""}
    assert serialize_grip(row) == "其他 OTHER 105EX"


def test_serialize_shoe_full_row():
    row = {
        "品牌": "李宁 LINING",
        "名称": "贴地飞行（城势版）",
        "鞋面": "合成革+纺织品",
        "中底": "全掌beng + 碳板",
        "大底": "生胶 + 实色橡胶",
    }
    out = serialize_shoe(row)
    assert out == "李宁 LINING 贴地飞行（城势版），鞋面：合成革+纺织品；中底：全掌beng + 碳板；大底：生胶 + 实色橡胶"


def test_serialize_shoe_partial_skips_empty():
    row = {"品牌": "李宁", "名称": "X", "鞋面": "合成革", "中底": "", "大底": ""}
    assert serialize_shoe(row) == "李宁 X，鞋面：合成革"


def test_spec_tables_registry():
    names = {t.name for t in SPEC_TABLES}
    assert names == {"球拍", "羽毛球", "球线", "手胶", "球鞋"}


def test_serialize_bwf_rule():
    row = {"规则类别": "场地尺寸", "规则内容": "标准羽毛球场为矩形", "详细说明": "单打宽5.18m"}
    out = serialize_bwf_rule(row)
    assert "场地尺寸：标准羽毛球场为矩形" in out
    assert "详细说明：单打宽5.18m" in out


def test_serialize_penalty():
    row = {"判罚类型": "发球过高", "违例描述": "击球点超过1.15m", "判罚结果": "对方得分"}
    out = serialize_penalty(row)
    assert out == "发球过高：击球点超过1.15m，判罚结果：对方得分"


def test_serialize_tactic():
    row = {"战术名称": "四方球战术", "类型": "单打", "战术描述": "调动对手跑动", "适用场景": "单打比赛", "战术要点": "落点要深"}
    out = serialize_tactic(row)
    assert out == "四方球战术（单打）：调动对手跑动。适用场景：单打比赛。战术要点：落点要深"


def test_serialize_technique():
    row = {
        "技术名称": "正手握拍",
        "分类": "握拍基础",
        "技术描述": "最基础的握拍方式",
        "动作要领": "虎口对准拍柄",
        "常见错误": "握拍过紧",
        "训练方法": "空挥拍练习",
    }
    out = serialize_technique(row)
    assert out == "正手握拍（握拍基础）：最基础的握拍方式。动作要领：虎口对准拍柄。常见错误：握拍过紧。训练方法：空挥拍练习"


def test_serialize_feather_grade():
    row = {"等级": "一级", "描述": "最高等级毛片", "外观特征": "毛片笔直", "耐打性": "优秀", "适用场景": "高端比赛用球"}
    out = serialize_feather_grade(row)
    assert out == "一级毛片：最高等级毛片。外观：毛片笔直。耐打性：优秀。适用场景：高端比赛用球"


def test_serialize_feather_type():
    row = {
        "毛片名称": "鹅刀翎(鹅全圆)",
        "来源": "鹅翅膀最优质毛片",
        "特性描述": "飞行性能和耐打性能俱佳",
        "飞行稳定性": "优秀(最高)",
        "耐打性": "优秀",
        "适用场景": "国际比赛用球",
    }
    out = serialize_feather_type(row)
    assert "鹅刀翎(鹅全圆)：飞行性能和耐打性能俱佳" in out
    assert "来源：鹅翅膀最优质毛片" in out
    assert "耐打性：优秀" in out


def test_serialize_cork():
    row = {"球头类型": "全软木(天然软木)", "结构": "纯天然软木制成", "打感": "弹性足", "耐打性": "优秀", "价格区间": "高", "适用人群": "专业球员"}
    out = serialize_cork(row)
    assert "全软木(天然软木)：纯天然软木制成。打感：弹性足。耐打性：优秀。价格：高。适用人群：专业球员" in out


def test_serialize_durability():
    row = {"影响因素": "毛片韧性", "说明": "韧性越高越耐打", "评估标准": "鹅刀翎＞鸭大方"}
    out = serialize_durability(row)
    assert out == "影响毛片韧性：韧性越高越耐打。评估标准：鹅刀翎＞鸭大方"


def test_serialize_speed_grade():
    row = {"速度等级": "75(慢速)", "飞行特点": "飞行速度较慢", "适用温度": "高温(25°C以上)", "适用场景": "热带地区", "备注": "避免球速过快出界"}
    out = serialize_speed_grade(row)
    assert out == "75(慢速)球速：飞行速度较慢。适用温度：高温(25°C以上)。适用场景：热带地区。备注：避免球速过快出界"


def test_serialize_flight():
    row = {"影响因素": "重量", "说明": "球的重量影响飞行轨迹", "评估标准": "重量均匀分布更稳定"}
    out = serialize_flight(row)
    assert out == "影响重量：球的重量影响飞行轨迹。评估标准：重量均匀分布更稳定"


def test_serialize_knowledge_sparse_skips_empty():
    assert serialize_flight({"影响因素": "重量", "说明": "", "评估标准": ""}) == "影响重量"


def test_knowledge_tables_registry():
    assert len(KNOWLEDGE_TABLES) == 12
    assert len(ALL_TABLES) == 17
    assert ALL_TABLES == SPEC_TABLES + KNOWLEDGE_TABLES
    assert {t.name for t in KNOWLEDGE_TABLES} >= {"BWF官方规则", "常见判罚", "战术", "规格常识"}


def test_serialize_spec_knowledge():
    from app.ingest.serializer import serialize_spec_knowledge

    row = {"规格项": "拍身重量U数", "规格值": "4U", "含义说明": "重量约80-84克", "适用建议": "适合女性球友"}
    text = serialize_spec_knowledge(row)
    assert "4U" in text and "80-84克" in text and "女性" in text
