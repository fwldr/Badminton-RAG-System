# Agent 版四指标基线报告（Phase 4）

> 生成：2026-08-23（`scripts/eval_agent_quality.py --online --repeat 1`，DeepSeek judge + 真实链路）
> 数据：`data/eval/quality_result.json`；配套：`data/eval/bad_cases.md`
> 口径：上下文 = agent 真实喂给生成节点的 `state["contexts"]`（路由定向 + 过滤 + retry 扩大），
> judge 函数复用 `scripts/ragas_eval.py`（手写 LLM-as-judge，不装 ragas 库）。

## 1. 平均四指标（20 题）

| 指标 | 平均 | 说明 |
|---|---|---|
| faithfulness | **0.812** | 回答主张能被检索上下文支撑的比例（≥0.85 为合格线） |
| answer_relevancy | **0.873** | 回答切题、信息充分度 |
| context_precision | **0.407** | 检索条目中相关条目占比（**主要短板**） |
| context_recall | **0.675** | 标准答案信息点被上下文覆盖的比例 |

## 2. 按 route 分组

| route | faith | relev | prec | recall | 解读 |
|---|---|---|---|---|---|
| rules | 1.000 | 1.000 | 0.375 | 1.000 | 规则库精准、召回满分，但上下文仍夹带噪声 |
| technique | 0.963 | 0.917 | 0.467 | 0.600 | 忠实度高，召回受知识表覆盖限制 |
| equipment | 0.944 | 0.917 | 0.574 | 0.611 | 结构化查询 precision 最高（证据：定向检索有效） |
| multi | 0.733 | 0.842 | **0.356** | 0.683 | 拆解合并上下文最嘈杂（**重点改进对象**） |

## 3. 主要发现（bad case 反哺素材）

1. **multi 路由 context_precision 仅 0.356**：拆解后多子问题检索结果合并（最多 15 条）
   再经 retry 全表扩大（BM25 候选池 ~18 条、无截断）→ 大量无关条目挤占上下文，
   既压低 precision 也稀释生成质量。改进方向：multi 上下文按子问题分组截断 + retry 后
   重新精排/截断（如 top-8），而不是全量喂给生成。
2. **a05/a06/a16 三题本采样走兜底（fallback=True）**：Phase 3 的 90% 评估里 a05/a16 通过、
   a06 本就失败；本轮 fallback 属 DeepSeek 单次采样方差（retry=1 仍未挽回）。
   需 `--repeat 3` 多数制复测确认（见第 5 节）。a06 是已知数据缺口（PU/毛巾材质对比不足）。
3. **context_recall 0.675**：`agent_golden.json` 的 `reference_answer` 是我方标准答案，
   部分信息点（如 a02 步法细节、a17 高远球错误原因）未被现有知识表覆盖 →
   一是补语料（数据缺口），二是校准 reference_answer 与知识库口径（评测口径问题）。

## 4. 对比实验（/ask vs /chat）

- 设计：同一 golden 分别跑 `scripts/ragas_eval.py`（/ask：全表检索候选池）与
  `scripts/eval_agent_quality.py`（/chat：路由定向上下文），对比 context_precision。
- 状态：**待跑**（两套 golden 题目不同：`golden.json` 12 题 vs `agent_golden.json` 20 题，
  需抽公共子集或统一 golden 后执行）。预期 /chat 的 precision 更高（路由+定向检索）。

## 5. 复测指引

```bash
# 单次采样（成本低）
.venv/Scripts/python.exe -m scripts.eval_agent_quality --online --repeat 1 --json-out data/eval/quality_result.json
# 多次取均值（缓解 LLM 非确定性，建议改进后使用）
.venv/Scripts/python.exe -m scripts.eval_agent_quality --online --repeat 3 --json-out data/eval/quality_result_r3.json
# 通过率回归（Phase 3 验收口径）
.venv/Scripts/python.exe -m scripts.eval_agent --online --repeat 3
# bad case 重生成
.venv/Scripts/python.exe -m scripts.collect_bad_cases
```

> 改进闭环：改检索/路由/数据 → 重跑 `--repeat 3` → 观察四指标与通过率 → 更新 `bad_cases.md` 状态列。
