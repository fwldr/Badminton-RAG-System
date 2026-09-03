"""`scripts/build_wiki` CLI 测试（全离线：临时源目录 + 临时输出目录）。

只验证命令行的编排行为（编译→校验→落盘、--check 的新鲜度判定），
编译本身的忠实性由 tests/test_wiki_compile.py 覆盖。
"""

import csv
from pathlib import Path

from app.ingest.serializer import SPEC_TABLES
from scripts.build_wiki import build, check

RACKET = next(t for t in SPEC_TABLES if t.name == "球拍")
TABLES = (RACKET,)
ROWS = [
    {"品牌": "尤尼克斯 YONEX", "型号": "天斧99", "别名": "ASTROX 99", "拍身重量(U)": "4U",
     "最高磅数": "35", "平衡点类别": "头重", "打法类型": "进攻型", "适合水平": "进阶级",
     "适合人群": "适合力量好的选手", "参考价": "1350元", "来源": "淘宝"},
    {"品牌": "李宁 LINING", "型号": "战戟12", "别名": "", "拍身重量(U)": "3U",
     "最高磅数": "30", "平衡点类别": "均衡", "打法类型": "均衡型", "适合水平": "通用级",
     "适合人群": "", "参考价": "799元", "来源": "淘宝"},
]


def _data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "processed"
    path = root / RACKET.csv_file
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(ROWS[0].keys()))
        writer.writeheader()
        writer.writerows(ROWS)
    return root


def test_build_then_check_is_clean(tmp_path: Path, capsys):
    data_dir, wiki_dir = _data_dir(tmp_path), tmp_path / "wiki"

    assert build(wiki_dir, data_dir, tables=TABLES) == 0
    out = capsys.readouterr().out
    assert "2 条记录 → 2 个条目" in out and "写入 2" in out
    assert check(wiki_dir, data_dir, tables=TABLES) == 0
    assert "wiki 新鲜" in capsys.readouterr().out


def test_check_reports_stale_after_source_change(tmp_path: Path, capsys):
    data_dir, wiki_dir = _data_dir(tmp_path), tmp_path / "wiki"
    build(wiki_dir, data_dir, tables=TABLES)

    with open(data_dir / RACKET.csv_file, "a", encoding="utf-8", newline="") as f:
        f.write(",".join(["新品牌 NEW", "新拍", "", "4U", "30", "头重", "进攻型",
                          "通用级", "", "100元", "淘宝"]) + "\n")

    assert check(wiki_dir, data_dir, tables=TABLES) == 1
    assert "落后于源" in capsys.readouterr().err


def test_check_fails_when_not_built(tmp_path: Path, capsys):
    assert check(tmp_path / "empty", _data_dir(tmp_path), tables=TABLES) == 1
    assert "尚未编译" in capsys.readouterr().err
