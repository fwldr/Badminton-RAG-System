"""BM25 词法检索索引：jieba 分词 + rank_bm25，按表建索引、按表检索。

向量检索对「红色」这类长文本里的短属性词天然弱（GP203 排不进候选池），
但 BM25 对精确词是硬命中——这正是混合检索的召回补充。
分词规则：文档侧用 jieba 词；查询侧额外补 CJK 单字，桥接「红色」↔ 枚举里的「红」。
"""

from __future__ import annotations

from typing import Callable, Iterable

import jieba
from rank_bm25 import BM25Okapi

from app.models.spec import SpecTable

# 预加载 jieba 内置词典，避免首次检索时的分词卡顿
jieba.initialize()


def _jieba_words(text: str) -> list[str]:
    """jieba 分词，去掉标点/空白，保留含字母数字或汉字的词。"""
    return [t for t in jieba.lcut(text) if t.strip() and any(c.isalnum() for c in t)]


# 查询侧停用词：功能词/疑问词/泛动词（空格分隔的整词），不参与 BM25 打分，避免污染词法命中。
# 注意必须定义为「词」集合（split 成整词），不能是单字串——否则整词过滤永远失效。
_QUERY_STOPWORDS: frozenset[str] = frozenset(
    "的 了 吗 呢 吧 啊 哦 在 是 有 和 与 及 或 就 都 只 把 被 这 那 哪 些 一 "
    "什么 怎么 如何 为何 为什么 请问 推荐 介绍 一下 一款 个 种 应该 可以 帮我 我想 "
    "区别 对比 有什么 没有 多少 高 不高 低 不低 行 不行".split()
)


def tokenize(text: str) -> list[str]:
    """文档侧分词：jieba 词。metadata 枚举字段本身已是单字（如「黑、红、白」）。"""
    return _jieba_words(text)


def tokenize_query(text: str) -> list[str]:
    """查询侧分词：jieba 词（去停用词）+ 补充 CJK 单字，桥接「红色」↔ 枚举里的「红」。

    只在查询侧补充单字：文档侧不加，避免常见字（球/拍/手/胶）在全库产生噪声；
    查询「红色」→ 红/色 与文档枚举里的单字「红」精确命中。
    """
    words = [w for w in _jieba_words(text) if w not in _QUERY_STOPWORDS]
    extra: list[str] = []
    for w in words:
        if len(w) > 1 and any("一" <= c <= "鿿" for c in w):
            extra.extend(c for c in w if "一" <= c <= "鿿" and c not in _QUERY_STOPWORDS)
    return words + extra


class Bm25Index:
    """每张表一个 BM25Okapi 索引；query 按同样分词后对每表取 top-k 排名。"""

    def __init__(self) -> None:
        self._tables: dict[str, BM25Okapi] = {}
        self._records: dict[str, list[tuple[str, str]]] = {}

    def build(
        self,
        tables: Iterable[str],
        load_docs: Callable[[str], list[tuple[str, str]]],
    ) -> None:
        """为每张表建索引；tables 为表名（含文档类 collection），load_docs(table) 返回 [(id, document), ...]。"""
        self._tables.clear()
        self._records.clear()
        for name in tables:
            docs = load_docs(name)
            if not docs:
                continue
            self._records[name] = docs
            tokenized = [tokenize(text) for _, text in docs]
            self._tables[name] = BM25Okapi(tokenized)

    def search(self, question: str, per_table_k: int) -> dict[str, int]:
        """返回 {record_id: 表内排名}，rank 从 1 开始；分数为 0 的命中不计。"""
        tokens = tokenize_query(question)
        if not tokens:
            return {}
        ranks: dict[str, int] = {}
        for table, bm in self._tables.items():
            scores = bm.get_scores(tokens)
            order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            for rank, idx in enumerate(order[:per_table_k], 1):
                if scores[idx] <= 0:
                    continue
                rid = self._records[table][idx][0]
                ranks[rid] = rank
        return ranks
