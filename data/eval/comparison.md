# 检索对比：纯向量 vs BM25 混合（Phase 1 提示词 B）

> 记录时间：2026-08-20
> 脚本：`scripts/eval_ask.py`（`--bm25` 开关）；题目：`data/eval/golden.json`；明细：`data/eval/result_vector.json`、`data/eval/result_bm25.json`

## 通过率

| 模式 | 通过率 | 变化 |
|---|---|---|
| 纯向量（bge-m3，per_table_k=8，无 BM25） | **7/12 = 58.3%** | 基线 |
| BM25 混合（向量主排序 + BM25 词法补充 + 颜色过滤） | **8/12 = 66.7%** | +1 题 |

逐题对比：**q10「推荐一款红色的手胶」从 FAIL → PASS**（向量召回不到 GP203，BM25 词法精确命中「红」+ 颜色过滤后进入生成窗口）。

## 实现要点

- `app/rag/bm25.py`：`Bm25Index` 按表建 `BM25Okapi` 索引；分词规则——文档侧 jieba 词、查询侧 jieba 词 + CJK 单字（桥接「红色」↔ 枚举里的「红」）+ 停用词过滤（避免「的/一款/推荐」等污染词法命中）。
- `app/rag/retriever.py`：`retrieve(use_bm25=...)` 返回**候选池** = 向量主结果（按距离排序，保语义精度）+ BM25 词法补充（主结果未覆盖的 BM25 命中，供属性过滤窄化）；两者各自受 `max_per_table=4` 约束。
- 生产链路 `app/api/routes/ask.py` 默认开启 BM25 混合。

## 设计取舍（实测依据）

- **未用纯 RRF 融合**：早期版本按 phase 文档做 RRF（k=60），实测 BM25 对「单打/双打」等高频词过召回，会拉低语义问题的精度（「单双打场地」从可答退回兜底）。改为向量主排序 + BM25 独立补充后，语义问答仍由向量主导、词法召回喂给属性过滤，两全。
- **查询侧单字桥 + 停用词**：jieba 把「红色」切成整词而 metadata 枚举是单字「红」，查询侧补单字才能命中；同时过滤功能词，否则「的/胶」等会给无关手胶虚假高分（实测 GP203 被挤到 BM25 表内第 4 → 过滤后回第 1）。
- 剩余离线 FAIL（q02/q05/q07/q08）主因是离线 FakeEmbedder 弱语义召回 + 不模拟颜色之外的属性过滤；真实 bge-m3 下 q07「单双打场地」已能作答（见 baseline 文档），q10 已实测答出 GP203。
- **真实服务波动观测**：q10 连测 6 次，5 次答出 GP203、1 次兜底（~1/6）。诊断确认候选池含 GP203、`extract_filters` 稳定抽到 `{"颜色":["红色"]}`、属性过滤后恰好剩 GP203——兜底仅发生在 DeepSeek `generate_answer` 对单条上下文偶发自我判定「不足以回答」，属 LLM 非确定性，非检索缺陷。

## 复现

```bash
.venv/Scripts/python.exe -m scripts.eval_ask                    # 纯向量 58.3%
.venv/Scripts/python.exe -m scripts.eval_ask --bm25             # 混合 66.7%
.venv/Scripts/python.exe -m scripts.eval_ask --online --bm25    # 真实服务混合
```
