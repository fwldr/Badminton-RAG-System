"""规格表数据模型：描述 5 张清洗后规格表的序列化方式与可过滤元数据字段。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# 行 → 一句自然语言描述（只拼非空字段）
RowSerializer = Callable[[dict], str]


@dataclass(frozen=True)
class SpecTable:
    """一张规格表（对应 data/processed/ 下的一张 CSV 与一个 Chroma collection）。"""

    name: str                            # 表名 = Chroma collection 名
    csv_file: str                        # data/processed/ 下的文件名
    serializer: RowSerializer            # 行 dict → 一句话描述
    metadata_fields: tuple[str, ...]     # 可过滤字段（metadata 键，使用 CSV 实际列名）
    # 行级稳定主键（CSV 实际列名）：增量入库的行 id 由其哈希派生，与行在文件中的位置无关。
    # 留空 = 该表无合适业务主键，行 id 退化为「整行内容哈希」（内容一变即新记录）。
    primary_key: tuple[str, ...] = ()
