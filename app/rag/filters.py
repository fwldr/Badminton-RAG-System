"""属性过滤：对向量检索结果按 LLM 抽取的过滤条件做精确/数值匹配。"""

from __future__ import annotations

import re

from app.ingest.serializer import ALL_TABLES

# 数值比较操作符后缀（按长度优先匹配）
_OPS: tuple[str, ...] = (">=", "<=", ">", "<")

# 全表可过滤字段并集（规格表 + 知识表，用于校验 LLM 输出的字段名）
FILTERABLE_FIELDS: frozenset[str] = frozenset(
    field for table in ALL_TABLES for field in table.metadata_fields
)


def _matches_string(metadata_value: str, allowed: list[str]) -> bool:
    """任一允许值命中即通过（双向包含 + 尾部「色」归一化 + 拆词）。

    - 允许值以「色」结尾时去尾归一化（如「红色」→「红」，「荧光绿」不动）；
    - metadata 值按 、/,/空格 拆成词后与允许值做互相包含判断，
      因此「红色」能命中「黑、红、白、黄」，也能命中单独的「红」。
    """
    meta_tokens = [t for t in re.split(r"[、,，\s]+", str(metadata_value)) if t]
    for val in allowed:
        v = str(val)
        v_norm = v[:-1] if v.endswith("色") else v  # 红色 → 红
        if v in str(metadata_value) or v_norm in str(metadata_value):
            return True
        for tok in meta_tokens:
            if v in tok or v_norm in tok or tok in v:
                return True
    return False


def _numeric_compare(metadata_value: str, target: float, op: str) -> bool:
    try:
        value = float(metadata_value)
    except (TypeError, ValueError):
        return False
    target = float(target)
    if op == ">=":
        return value >= target
    if op == "<=":
        return value <= target
    if op == ">":
        return value > target
    return value < target


def _parse_condition(key: str) -> tuple[str, str | None]:
    """拆出字段名与数值操作符；无操作符返回 (key, None)。"""
    for op in _OPS:
        if key.endswith(op):
            return key[: -len(op)], op
    return key, None


def apply_filters(records: list[dict], conditions: dict | None) -> list[dict]:
    """按过滤条件过滤检索记录（AND 语义）。

    - 条件键以 >=/<=/>/< 结尾 → 数值比较；
    - 普通字段 → 取值归一化为列表，任一值被 metadata 值包含即通过；
    - 未知字段名忽略；metadata 缺失该字段视为不通过。
    """
    if not conditions:
        return list(records)

    result = []
    for rec in records:
        meta = rec.get("metadata") or {}
        ok = True
        for key, value in conditions.items():
            base, op = _parse_condition(str(key))
            if base not in FILTERABLE_FIELDS:
                continue  # LLM 幻想的字段，忽略
            meta_val = meta.get(base)
            if meta_val is None:
                ok = False
                break
            if op:
                if not _numeric_compare(str(meta_val), value, op):
                    ok = False
                    break
            else:
                allowed = value if isinstance(value, list) else [value]
                if not _matches_string(str(meta_val), [str(a) for a in allowed]):
                    ok = False
                    break
        if ok:
            result.append(rec)
    return result
