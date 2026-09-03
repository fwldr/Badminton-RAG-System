"""行级序列化：把 data/processed/ 下每张规格表的每一行拼成一句自然语言描述。

规则（见 phase_0.md 行级序列化模板）：
- 只拼非空字段；
- 每个字段值前后用空格分隔（字段自带连接词）；
- 中文为主、数字带单位；
- metadata 存可过滤的原始字段（品牌/型号/U数/最高磅数/平衡点类别/打法类型/适合水平/参考价/来源等）。

说明：模板样例中的「参考价格」对应 CSV 实际列名「参考价」，以实际列名为准；
「重量克重」列不存在，整段跳过。
"""

from __future__ import annotations

from app.models.spec import SpecTable


def _clean(value) -> str:
    """去空白；None/空值统一返回空串。"""
    if value is None:
        return ""
    return str(value).strip()


def _join(*clauses: str) -> str:
    """过滤空段后按中文逗号连接成一句。"""
    return "，".join(c for c in clauses if c)


def _clauses(*parts: str, sep: str = "。") -> str:
    """知识表通用拼接：过滤空段后按指定分隔符（默认句号）连接成一句。"""
    return sep.join(p for p in parts if p)


def serialize_racket(row: dict) -> str:
    """球拍：{品牌} {型号}({别名})，重量{拍身重量(U)}，拍柄{拍柄粗细}，中杆{中管韧度}，
    拉线磅数{拉线磅数}，平衡点{平衡点}（{平衡点类别}），{打法类型}，适合{适合水平}。{适合人群}。参考价格：{参考价}"""
    brand, model = _clean(row.get("品牌")), _clean(row.get("型号"))
    alias = _clean(row.get("别名"))
    weight = _clean(row.get("拍身重量(U)"))
    handle = _clean(row.get("拍柄粗细"))
    shaft = _clean(row.get("中管韧度"))
    tension = _clean(row.get("拉线磅数"))
    balance = _clean(row.get("平衡点"))
    balance_type = _clean(row.get("平衡点类别"))
    playstyle = _clean(row.get("打法类型"))
    level = _clean(row.get("适合水平"))
    crowd = _clean(row.get("适合人群"))
    price = _clean(row.get("参考价"))

    head = f"{brand} {model}".strip() if (brand or model) else ""
    if head and alias:
        head += f"({alias})"
    clauses = [head]
    if weight:
        clauses.append(f"重量{weight}")
    if handle:
        clauses.append(f"拍柄{handle}")
    if shaft:
        clauses.append(f"中杆{shaft}")
    if tension:
        clauses.append(f"拉线磅数{tension}")
    if balance:
        bal = f"平衡点{balance}"
        if balance_type:
            bal += f"（{balance_type}）"
        clauses.append(bal)
    if playstyle:
        clauses.append(playstyle)
    # 模板：适合{适合水平}。{适合人群}。参考价格：{参考价格}（三者紧邻，不以逗号分隔）
    tail_parts = []
    if level:
        tail_parts.append(f"适合{level}。")
    if crowd:
        tail_parts.append(f"{crowd}。")
    if price:
        tail_parts.append(f"参考价格：{price}")
    if tail_parts:
        clauses.append("".join(tail_parts))
    return _join(*clauses)


def serialize_shuttlecock(row: dict) -> str:
    """羽毛球：{品牌} {名称}，球速{球速}（适合{适用温度}），
    {羽毛类别}{毛片级别}{毛片等级}（{毛片原始}），球头{球头类别}（{球头原始}）"""
    brand, name = _clean(row.get("品牌")), _clean(row.get("名称"))
    speed = _clean(row.get("球速"))
    temp = _clean(row.get("适用温度"))
    feather = _clean(row.get("羽毛类别"))
    feather_grade = _clean(row.get("毛片级别"))
    feather_level = _clean(row.get("毛片等级"))
    feather_raw = _clean(row.get("毛片原始"))
    cork = _clean(row.get("球头类别"))
    cork_raw = _clean(row.get("球头原始"))

    clauses = [f"{brand} {name}".strip() if (brand or name) else ""]
    if speed:
        sp = f"球速{speed}"
        if temp:
            sp += f"（适合{temp}）"
        clauses.append(sp)
    if feather or feather_grade or feather_level:
        fl = f"{feather}{feather_grade}{feather_level}"
        if feather_raw:
            fl += f"（{feather_raw}）"
        clauses.append(fl)
    if cork:
        ck = f"球头{cork}"
        if cork_raw:
            ck += f"（{cork_raw}）"
        clauses.append(ck)
    return _join(*clauses)


def serialize_string(row: dict) -> str:
    """球线：{品牌} {名称}，线径{直径mm}mm{竖横线直径}，材质：{材质}"""
    brand, name = _clean(row.get("品牌")), _clean(row.get("名称"))
    dia = _clean(row.get("直径mm"))
    strand = _clean(row.get("竖横线直径"))
    material = _clean(row.get("材质"))

    clauses = [f"{brand} {name}".strip() if (brand or name) else ""]
    if dia:
        sp = f"线径{dia}mm"
        if strand:
            sp += strand
        clauses.append(sp)
    if material:
        clauses.append(f"材质：{material}")
    return _join(*clauses)


def serialize_grip(row: dict) -> str:
    """手胶：{品牌} {名称}，{材质类别}手胶（{材质}）{颜色}"""
    brand, name = _clean(row.get("品牌")), _clean(row.get("名称"))
    mat_type = _clean(row.get("材质类别"))
    material = _clean(row.get("材质"))
    color = _clean(row.get("颜色"))

    clauses = [f"{brand} {name}".strip() if (brand or name) else ""]
    if mat_type or material or color:
        parts = []
        if mat_type:
            parts.append(f"{mat_type}手胶")
        if material:
            parts.append(f"（{material}）")
        if color:
            parts.append(color)
        clauses.append("".join(parts))
    return _join(*clauses)


def serialize_shoe(row: dict) -> str:
    """球鞋：{品牌} {名称}，鞋面：{鞋面}；中底：{中底}；大底：{大底}"""
    brand, name = _clean(row.get("品牌")), _clean(row.get("名称"))
    upper = _clean(row.get("鞋面"))
    mid = _clean(row.get("中底"))
    out = _clean(row.get("大底"))

    head = f"{brand} {name}".strip() if (brand or name) else ""
    tail = "；".join(
        p
        for p in (
            f"鞋面：{upper}" if upper else "",
            f"中底：{mid}" if mid else "",
            f"大底：{out}" if out else "",
        )
        if p
    )
    return f"{head}，{tail}" if tail else head


# 5 张规格表注册表（顺序即入库顺序）
# primary_key：行级稳定主键（增量入库的行 id 由其哈希派生）；重复出现的组按出现序加后缀消歧
SPEC_TABLES: tuple[SpecTable, ...] = (
    SpecTable(
        name="球拍",
        csv_file="球拍.csv",
        serializer=serialize_racket,
        metadata_fields=(
            "品牌",
            "型号",
            "别名",
            "拍身重量(U)",
            "最高磅数",
            "平衡点类别",
            "打法类型",
            "适合水平",
            "参考价",
            "来源",
        ),
        primary_key=("品牌", "型号"),
    ),
    SpecTable(
        name="羽毛球",
        csv_file="羽毛球.csv",
        serializer=serialize_shuttlecock,
        metadata_fields=("品牌", "名称", "球速", "羽毛类别", "毛片等级", "球头类别", "来源文件"),
        primary_key=("名称",),
    ),
    SpecTable(
        name="球线",
        csv_file="球线.csv",
        serializer=serialize_string,
        metadata_fields=("品牌", "名称", "直径mm", "来源文件"),
        primary_key=("品牌", "名称"),
    ),
    SpecTable(
        name="手胶",
        csv_file="手胶.csv",
        serializer=serialize_grip,
        metadata_fields=("品牌", "名称", "材质类别", "颜色", "来源文件"),
        primary_key=("名称",),
    ),
    SpecTable(
        name="球鞋",
        csv_file="球鞋.csv",
        serializer=serialize_shoe,
        metadata_fields=("品牌", "名称", "来源文件"),
        primary_key=("名称",),
    ),
)


# ============ 11 张文本知识表（data/processed/knowledge/，Phase 1 接入） ============
# 通用模板：{列名}：{值} 按行拼成一句，只拼非空字段，句号/逗号连接。
# metadata 每张表必存 来源文件（回退为表名），有分类/主题名列的表加存该列作「主题名」。


def serialize_bwf_rule(row: dict) -> str:
    """BWF官方规则：规则类别：{规则内容}。详细说明：{详细说明}"""
    cat, content, detail = (
        _clean(row.get("规则类别")),
        _clean(row.get("规则内容")),
        _clean(row.get("详细说明")),
    )
    head = f"{cat}：{content}" if content else ""
    return _clauses(head, f"详细说明：{detail}" if detail else "")


def serialize_penalty(row: dict) -> str:
    """常见判罚：{判罚类型}：{违例描述}，判罚结果：{判罚结果}"""
    typ, desc, result = (
        _clean(row.get("判罚类型")),
        _clean(row.get("违例描述")),
        _clean(row.get("判罚结果")),
    )
    head = f"{typ}：{desc}" if desc else typ
    return _clauses(head, f"判罚结果：{result}" if result else "", sep="，")


def serialize_tactic(row: dict) -> str:
    """战术：{战术名称}（{类型}）：{战术描述}。适用场景：{适用场景}。战术要点：{战术要点}"""
    name, typ, desc, scene, point = (
        _clean(row.get("战术名称")),
        _clean(row.get("类型")),
        _clean(row.get("战术描述")),
        _clean(row.get("适用场景")),
        _clean(row.get("战术要点")),
    )
    label = f"{name}（{typ}）" if (name and typ) else (name or typ)
    head = f"{label}：{desc}" if desc else label
    return _clauses(
        head,
        f"适用场景：{scene}" if scene else "",
        f"战术要点：{point}" if point else "",
    )


def serialize_technique(row: dict) -> str:
    """手法/步法技术：{技术名称}（{分类}）：{技术描述}。动作要领：{动作要领}。常见错误：{常见错误}。训练方法：{训练方法}"""
    name, cat, desc, key, err, train = (
        _clean(row.get("技术名称")),
        _clean(row.get("分类")),
        _clean(row.get("技术描述")),
        _clean(row.get("动作要领")),
        _clean(row.get("常见错误")),
        _clean(row.get("训练方法")),
    )
    label = f"{name}（{cat}）" if (name and cat) else (name or cat)
    head = f"{label}：{desc}" if desc else label
    return _clauses(
        head,
        f"动作要领：{key}" if key else "",
        f"常见错误：{err}" if err else "",
        f"训练方法：{train}" if train else "",
    )


def serialize_feather_grade(row: dict) -> str:
    """毛片等级：{等级}毛片：{描述}。外观：{外观特征}。耐打性：{耐打性}。适用场景：{适用场景}"""
    grade, desc, look, dur, scene = (
        _clean(row.get("等级")),
        _clean(row.get("描述")),
        _clean(row.get("外观特征")),
        _clean(row.get("耐打性")),
        _clean(row.get("适用场景")),
    )
    head = f"{grade}毛片：{desc}" if desc else f"{grade}毛片" if grade else ""
    return _clauses(
        head,
        f"外观：{look}" if look else "",
        f"耐打性：{dur}" if dur else "",
        f"适用场景：{scene}" if scene else "",
    )


def serialize_feather_type(row: dict) -> str:
    """毛片类型：{毛片名称}：{特性描述}。来源：{来源}。飞行稳定性：{飞行稳定性}。耐打性：{耐打性}。适用场景：{适用场景}"""
    name, source, desc, stable, dur, scene = (
        _clean(row.get("毛片名称")),
        _clean(row.get("来源")),
        _clean(row.get("特性描述")),
        _clean(row.get("飞行稳定性")),
        _clean(row.get("耐打性")),
        _clean(row.get("适用场景")),
    )
    head = f"{name}：{desc}" if desc else name
    return _clauses(
        head,
        f"来源：{source}" if source else "",
        f"飞行稳定性：{stable}" if stable else "",
        f"耐打性：{dur}" if dur else "",
        f"适用场景：{scene}" if scene else "",
    )


def serialize_cork(row: dict) -> str:
    """球头材质：{球头类型}：{结构}。打感：{打感}。耐打性：{耐打性}。价格：{价格区间}。适用人群：{适用人群}"""
    typ, struct, feel, dur, price, crowd = (
        _clean(row.get("球头类型")),
        _clean(row.get("结构")),
        _clean(row.get("打感")),
        _clean(row.get("耐打性")),
        _clean(row.get("价格区间")),
        _clean(row.get("适用人群")),
    )
    head = f"{typ}：{struct}" if struct else typ
    return _clauses(
        head,
        f"打感：{feel}" if feel else "",
        f"耐打性：{dur}" if dur else "",
        f"价格：{price}" if price else "",
        f"适用人群：{crowd}" if crowd else "",
    )


def serialize_durability(row: dict) -> str:
    """耐打度影响因素：影响{影响因素}：{说明}。评估标准：{评估标准}"""
    factor, desc, std = (
        _clean(row.get("影响因素")),
        _clean(row.get("说明")),
        _clean(row.get("评估标准")),
    )
    head = f"影响{factor}：{desc}" if desc else f"影响{factor}" if factor else ""
    return _clauses(head, f"评估标准：{std}" if std else "")


def serialize_speed_grade(row: dict) -> str:
    """速度等级：{速度等级}球速：{飞行特点}。适用温度：{适用温度}。适用场景：{适用场景}。备注：{备注}"""
    grade, flight, temp, scene, note = (
        _clean(row.get("速度等级")),
        _clean(row.get("飞行特点")),
        _clean(row.get("适用温度")),
        _clean(row.get("适用场景")),
        _clean(row.get("备注")),
    )
    head = f"{grade}球速：{flight}" if flight else f"{grade}球速" if grade else ""
    return _clauses(
        head,
        f"适用温度：{temp}" if temp else "",
        f"适用场景：{scene}" if scene else "",
        f"备注：{note}" if note else "",
    )


def serialize_flight(row: dict) -> str:
    """飞行稳定性影响因素：影响{影响因素}：{说明}。评估标准：{评估标准}"""
    factor, desc, std = (
        _clean(row.get("影响因素")),
        _clean(row.get("说明")),
        _clean(row.get("评估标准")),
    )
    head = f"影响{factor}：{desc}" if desc else f"影响{factor}" if factor else ""
    return _clauses(head, f"评估标准：{std}" if std else "")


def serialize_spec_knowledge(row: dict) -> str:
    """规格常识：{规格项} {规格值}：{含义说明}。适用建议：{适用建议}"""
    item, value, meaning, advice = (
        _clean(row.get("规格项")),
        _clean(row.get("规格值")),
        _clean(row.get("含义说明")),
        _clean(row.get("适用建议")),
    )
    head = f"{item} {value}：{meaning}" if meaning else f"{item} {value}" if (item and value) else ""
    return _clauses(head, f"适用建议：{advice}" if advice else "")


# 12 张知识表注册表（顺序即入库顺序；metadata 首列为「主题名」，供来源展示）
KNOWLEDGE_TABLES: tuple[SpecTable, ...] = (
    SpecTable(
        name="BWF官方规则",
        csv_file="knowledge/BWF官方规则.csv",
        serializer=serialize_bwf_rule,
        metadata_fields=("规则类别", "来源文件"),
        primary_key=("规则类别", "规则内容"),
    ),
    SpecTable(
        name="常见判罚",
        csv_file="knowledge/常见判罚.csv",
        serializer=serialize_penalty,
        metadata_fields=("判罚类型", "来源文件"),
        primary_key=("判罚类型", "违例描述"),
    ),
    SpecTable(
        name="战术",
        csv_file="knowledge/战术.csv",
        serializer=serialize_tactic,
        metadata_fields=("类型", "来源文件"),
        primary_key=("战术名称",),
    ),
    SpecTable(
        name="手法技术",
        csv_file="knowledge/手法技术.csv",
        serializer=serialize_technique,
        metadata_fields=("分类", "来源文件"),
        primary_key=("技术名称",),
    ),
    SpecTable(
        name="步法技术",
        csv_file="knowledge/步法技术.csv",
        serializer=serialize_technique,
        metadata_fields=("分类", "来源文件"),
        primary_key=("技术名称",),
    ),
    SpecTable(
        name="毛片等级",
        csv_file="knowledge/毛片等级.csv",
        serializer=serialize_feather_grade,
        metadata_fields=("等级", "来源文件"),
        primary_key=("等级",),
    ),
    SpecTable(
        name="毛片类型",
        csv_file="knowledge/毛片类型.csv",
        serializer=serialize_feather_type,
        metadata_fields=("毛片名称", "来源文件"),
        primary_key=("毛片名称", "来源"),
    ),
    SpecTable(
        name="球头材质",
        csv_file="knowledge/球头材质.csv",
        serializer=serialize_cork,
        metadata_fields=("球头类型", "来源文件"),
        primary_key=("球头类型",),
    ),
    SpecTable(
        name="耐打度影响因素",
        csv_file="knowledge/耐打度影响因素.csv",
        serializer=serialize_durability,
        metadata_fields=("来源文件",),
        primary_key=("影响因素",),
    ),
    SpecTable(
        name="速度等级",
        csv_file="knowledge/速度等级.csv",
        serializer=serialize_speed_grade,
        metadata_fields=("速度等级", "来源文件"),
        primary_key=("速度等级",),
    ),
    SpecTable(
        name="飞行稳定性影响因素",
        csv_file="knowledge/飞行稳定性影响因素.csv",
        serializer=serialize_flight,
        metadata_fields=("来源文件",),
        primary_key=("影响因素",),
    ),
    SpecTable(
        name="规格常识",
        csv_file="knowledge/规格常识.csv",
        serializer=serialize_spec_knowledge,
        metadata_fields=("规格项", "规格值", "来源文件"),
        primary_key=("规格项", "规格值"),
    ),
)

# 全量注册表：5 张规格表 + 12 张知识表
ALL_TABLES: tuple[SpecTable, ...] = SPEC_TABLES + KNOWLEDGE_TABLES
