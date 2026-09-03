# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

badminton-rag 是羽毛球装备/知识问答服务，采用 Agentic RAG 架构。Phase 0~2 已完成（CSV 清洗入库 + `POST /ask` 问答 + 审计/限流/管理后台），Phase 3 完成：`POST /chat` Agentic 对话（LangGraph 路由 Agent + 工具调用 + 多轮记忆 + 回答校验，20 题多跳评估 90%）。Phase 4 完成：评测与可观测（agent 版四指标评测、Langfuse/Null tracer、FAQ 缓存、token 成本统计、用户反馈 bad case 闭环，221 测试全绿）。Phase 5 完成：部署与展示（Docker Compose 一键起：init 入库 + api + web 前端；Vite+React+TS 聊天 UI；README 重写）。Phase 6 完成：文档/图片入库（PDF 用 PyMuPDF 解析、图片用 RapidOCR 文本索引 + SiliconFlow Qwen3-VL-Embedding 多模态图片索引、document 路由、管理端上传/文档管理）。行级序列化模板见 `phase_0.md`；球拍模板以 `phase_0_修改.md` 为准（型号后追加 `({别名})`）。设计文档：`badminton-rag-pdf-image-ingest-plan.md`（已确认 6 项决策）。

代码、注释、文档、数据均为中文；问答输出也须为中文。实现新功能前先给设计再写代码（沿用 phase_0.md 约定）。

## 常用命令

Windows + Git Bash，虚拟环境位于 `.venv/`：

```bash
# 安装依赖
.venv/Scripts/python.exe -m pip install -r requirements.txt

# 启动服务（http://127.0.0.1:8000）
.venv/Scripts/python.exe -m uvicorn main:app --reload

# 运行全部测试
.venv/Scripts/python.exe -m pytest -q

# 运行单个测试
.venv/Scripts/python.exe -m pytest tests/test_health.py -q
.venv/Scripts/python.exe -m pytest tests/test_health.py::test_health -q

# 重新入库全部 17 张表（真实百炼 embedding → data/chroma，幂等）
.venv/Scripts/python.exe -m app.ingest.pipeline

# 增量同步（推荐）：行主键 id + 内容 digest，只重嵌变化的行、删除陈旧行；完成后接 build_wiki 联动 wiki 层
.venv/Scripts/python.exe -m app.ingest.pipeline --sync                 # 全部 17 张
.venv/Scripts/python.exe -m app.ingest.pipeline --sync --tables 球拍    # 只同步指定表（逗号分隔中文表名）

# 文档/图片批量入库（data/raw_docs 下 pdf/图片/txt/md/csv，按文件 hash 幂等）
.venv/Scripts/python.exe -m app.ingest.pipeline --dir data/raw_docs   # CSV 照常 + 追加文档
.venv/Scripts/python.exe -m app.ingest.pipeline --only-docs           # 只入文档目录

# 编译 LLM Wiki 派生层（data/processed 829 行 → data/wiki 条目页，纯模板零 LLM，幂等）
.venv/Scripts/python.exe -m scripts.build_wiki --skip-llm             # 只重写内容变了的页
.venv/Scripts/python.exe -m scripts.build_wiki --check                # 只校验 wiki 是否落后于 CSV
.venv/Scripts/python.exe -m scripts.build_wiki --force --dry-run      # 全量重编译预演

# golden set 评估（默认离线：FakeEmbedder + 内存库 + stub LLM，不触网）
.venv/Scripts/python.exe -m scripts.eval_ask
.venv/Scripts/python.exe -m scripts.eval_ask --bm25     # 开 BM25 混合检索
.venv/Scripts/python.exe -m scripts.eval_ask --online   # 走真实服务
.venv/Scripts/python.exe -m scripts.eval_agent          # Agent 多跳评估（离线）
.venv/Scripts/python.exe -m scripts.eval_agent --online --repeat 3   # 在线，多次取多数
.venv/Scripts/python.exe -m scripts.eval_agent_quality --online     # agent 版四指标（LLM-as-judge）
.venv/Scripts/python.exe -m scripts.eval_agent_quality --online --mode both --repeat 2   # classic vs wiki 并排 A/B（需先 build_wiki --index）
.venv/Scripts/python.exe -m scripts.collect_bad_cases  # bad case 报告（读 quality_result + 点踩）

# Docker Compose 一键部署（mysql + init 入库 + api + web）
docker compose up -d --build   # 首次 5 分钟内可提问，浏览器 http://localhost:8080
docker compose down            # 停止（卷保留：app-data=chroma、mysql-data=业务库）
docker compose --profile selfhost up -d   # 可选：自托管 Langfuse

# 前端（web/，Vite + React + TS）
cd web && npm install && npm run dev   # dev 服务 5173，proxy 转发到 8000
cd web && npm run build                # 产物 web/dist（web 镜像构建时自动执行）
```

未配置 linter / formatter（requirements.txt 中无相关依赖）；测试框架为 pytest，通过 `TestClient` + httpx 测接口。测试不得触网：向量用 `FakeEmbedder`、库用内存版 `VectorStore()`、LLM 用 stub 覆盖 `complete()`。

## 架构

应用工厂模式：`main.py` 的 `create_app()` 初始化日志与配置、挂载路由并返回 FastAPI 实例；模块级 `app = create_app()` 供 uvicorn 使用。测试各自调用 `TestClient(create_app())`，因此工厂需可重复调用。

- `app/api/routes/` — FastAPI 路由，`__init__.py` 聚合各模块 router 为 `api_router`。`health.py` 存活探针；`auth.py`（Phase 7）注册/登录/当前用户（双角色）；`ask.py` 提供 `POST /ask`；`chat.py` 提供 `POST /chat`（Phase 3）+ `GET /chat/stats`（成本报表，需管理员）；`feedback.py` 提供 `POST /feedback`（点赞/点踩，登录用户自动关联 user_id）；`kb.py` 提供 `GET /kb/overview`（Phase 5：知识库统计，公开只读）。
- `app/api/deps.py` — 鉴权依赖（Phase 7）：`require_admin_key`（旧 X-Admin-Key，保留兼容）/ `get_current_user_optional`（Bearer 令牌可选，用户端接口用）/ `get_current_user`（强制登录）/ `require_admin`（严格管理员，用户与权限管理用）/ `require_admin_access`（管理员 JWT 或旧 key 任一，文档/审计/统计端点用）+ 限流依赖工厂。
- `app/core/security.py` — 安全原语（纯 stdlib，无新依赖）：`hash_password`/`verify_password`（PBKDF2-HMAC-SHA256 加盐）；`create_token`/`decode_token`（HMAC-SHA256 签名 Bearer token，payload 含 sub/role/exp，JWT 兼容用法）。
- `app/core/config.py` — pydantic-settings 配置，`get_settings()` 为 lru_cache 单例；`BASE_DIR` 向上三级解析。环境变量优先于根目录 `.env`，`extra="ignore"`。RAG 相关配置：`llm_api_key`（大小写不敏感自动读 `.env` 的 `LLM_API_KEY`，生成 LLM 与文本 embedding 共用同一个 key 与 base_url）/`llm_base_url`/`llm_model`（默认百炼 DashScope compatible-mode + `qwen3.8-flash`）/`embedding_model`（默认 `qwen3.7-text-embedding`）、`chroma_dir`、`ask_vector_top_k`/`ask_filter_top_k`、`ingest_batch_size`；精排相关：`rerank_api_key`（自动读 `.env` 的 `RERANK_API_KEY`）/`rerank_base_url`/`rerank_model`（默认 `BAAI/bge-reranker-v2-m3`）/`ask_use_rerank`（默认 False，关闭时不接入精排）；账户相关：`auth_token_secret`/`auth_token_ttl`/`bootstrap_admin_username`/`bootstrap_admin_password`。
- `app/core/logging.py` — `setup_logging()` 幂等（模块级 `_configured` 标志），控制台 + 滚动文件双 handler。
- `app/models/` — `spec.py` 定义 `SpecTable`（表名/CSV 文件名/序列化函数/可过滤 metadata 字段）；`schema.py` 定义 `AskRequest`/`AskResponse`/`AskSource`。
- `app/ingest/` — 入库流水线：
  - `serializer.py` — 5 张表的行级序列化函数 + `SPEC_TABLES` 注册表。规则：只拼非空字段；列名以 CSV 实际为准（球拍为 `参考价`/`来源`，其余表为 `来源文件`；`重量克重` 列不存在，整段跳过）。球拍按 `{品牌} {型号}({别名})`。
  - `embedder.py` — `Embedder` Protocol；`DashScopeEmbedder` 用 httpx POST 百炼 OpenAI 兼容 `/embeddings`（入参超单批上限自动分批，按响应 `index` 还原顺序）；`build_embedder(settings)` 是入库与查询的唯一构造入口（保证两边同向量空间）；`FakeEmbedder`（确定性字符 hash 向量）仅测试用。
  - `store.py` — `VectorStore` 封装 Chroma。未传 `persist_dir` 用内存版（构造时清空进程内共享内存系统以彼此隔离，仅测试用），否则 `PersistentClient` 持久化到 `data/chroma`；一律传入预计算 embeddings，不依赖 Chroma 内置 embedding 函数。
  - `pipeline.py` — `run_ingest()`/`ingest_table()` 编排（读 CSV → 序列化 → 分批 embedding → upsert），`main()` 为真实入库入口。行 id 由 `row_ids()` 生成：`{collection}:{sha1(主键串)[:12]}`（主键在 `SpecTable.primary_key` 注册，与行位置无关；重复主键按出现序加 `-2` 后缀消歧，主键全空回退整行内容哈希），metadata 带内容 `digest`，重复执行幂等。`sync_table()/sync_all()`（CLI `--sync`）为增量模式：只重嵌 digest 变化的行、自动删除陈旧行（含旧 `{coll}:{idx}` 行号 id——首次 sync 即完成迁移）；`--tables` 限定表名。设计文档 `badminton-rag-incremental-sync-plan.md`。
- `app/rag/` — 检索与问答：
  - `retriever.py` — `Record` 数据类（含 `to_dict()`）；`Retriever.retrieve()` 对全部 17 个 collection 各查 `per_table_k`（默认 8）条后按 cosine 距离合并，同一 collection 最多保留 `max_per_table`（默认 4）条（多样性约束），再升序取前 `top_k`。`use_bm25=True`（构造参数默认 False，生产 `ask.py` 开启）时返回候选池 = 向量主结果 + BM25 词法补充（主结果未覆盖的 BM25 命中，独立受 `max_per_table` 约束，供属性过滤窄化）。`use_expansion=True`（默认开）时先经 `query_expander.expand()` 做同义词扩展，对每个扩展查询分别检索，结果按 id 合并去重、distance 取各查询中最优（BM25 排名取最优）；未命中同义词时 expand 返回 `[原查询]`，行为不变。
  - `query_expander.py` — 查询改写：同义词表 `SYNONYMS`（杀球/扣杀/劈杀、搓球/放网、4U/四U、平衡点/重心/头重、磅数/拉力、耐打/耐用/结实）；`expand(query)` 命中时返回 `[原查询, 变体...]`（追加不替换、去重保序），未命中返回 `[原查询]`。
  - `bm25.py` — `Bm25Index` 按表建 `BM25Okapi` 索引；分词：文档侧 jieba 词、查询侧 jieba 词 + CJK 单字（桥接「红色」↔ 枚举里的「红」）+ 停用词过滤；`Retriever` 内懒构建并按 collection 计数哈希失效重建。
  - `filters.py` — `apply_filters()` 属性过滤：普通字段用 `_matches_string()` 双向包含 + 尾部「色」归一化 + 拆词（metadata 按 `、/,/空格` 拆词后互相包含，如「红色」可命中「黑、红、白、黄」）；键以 `>=/<=/>/<` 结尾做数值比较；未知字段忽略。`FILTERABLE_FIELDS` 为全表 metadata 字段并集。
  - `llm.py` — `LLMClient`（百炼 DashScope，OpenAI 兼容）：`extract_filters()` 抽过滤条件 JSON（`response_format=json_object`）；`generate_answer()` 生成中文回答（禁止编造、末尾附「来源：品牌 型号」、不足以回答时输出 `知识库中暂无相关信息`）。`parse_filter_json()` 为纯函数，兼容 ```json 围栏。构造参数 `usage_hook`（默认 None）：每次 `complete()` 响应含 usage 时回调 `{"prompt_tokens","completion_tokens","total_tokens"}`（旁路，异常不影响主链路）。`complete()` 固定带 `extra_body={"enable_thinking": False}` 关闭千问 3.x 思考链——实测 A/B（4 道多跳题×3 次）：开思考 0/4 通过、均 593 completion tokens；关思考 2/4 通过、均 46 tokens（省约 12 倍且不退化）。
  - `reranker.py` — `Reranker` Protocol（`rerank(query, records, top_n)`）；`SiliconFlowReranker` httpx POST 硅基流动 `/v1/rerank`（`BAAI/bge-reranker-v2-m3`，payload 含 `model/query/documents`，解析 `results` 按 `relevance_score` 降序）；`FakeReranker` 原样返回（测试用）；`build_reranker(settings)` 按 `ask_use_rerank` 开关构建，未开或缺 key 返回 None。
  - `service.py` — `AskService.ask()`：检索 top-10 → 抽过滤条件 → 属性过滤 → （接了 reranker 时先精排再取）top-5 拼 prompt → 生成回答 → 来源（`(表, 品牌, 型号)` 去重）。过滤为空时回退到未过滤 top-5；精排失败兜底原序不破坏链路；回答为空或含兜底文案时，统一返回「知识库中暂无相关信息」且 sources 为空。
- `app/wiki/` — LLM Wiki 派生层（离线编译，见 `badminton-rag-llm-wiki-plan.md`；W1 只编译，尚未接入在线问答链路，`wiki_mode_enabled` 默认 False）：
  - `schema.py` — `Entry`/`Section`/`SourceAnchor` 数据类与条目 markdown 往返（YAML frontmatter 承载结构化事实、`##` 标题承载章节正文）。条目 id 规则：`{prefix}_{ascii slug}_{sha1前6位}`，必须匹配 `^[A-Za-z0-9][A-Za-z0-9._-]{2,400}$` 且**不含冒号**（`Retriever._fetch_record` 用 `rpartition(":")` 拆 record id）；中文标题无 ASCII 可用时 slug 退化为 `x`，靠 hash 保唯一。
  - `compile.py` — 模板编译器（零 LLM）：5 张规格表逐行 → `product` 条目（概况=复用行级序列化 / 规格参数=全字段表 / 适用人群与打法），12 张知识表按 `CONCEPT_POLICIES` 选粒度（`row` 一行一页 / `field` 按列分组一行一节 / `table` 整表一页一行一节）→ `concept` 条目；`links` 由「值索引」模板推导（`4U` → 概念《拍身重量U数》§4U，产品→概念的反向链接上限 `MAX_IN_LINKS=10`）。`validate_entries()` 是忠实性闸门：每条记录被且只被一个条目锚定、每个非空单元格必须出现在所属条目文本中、facets 必须原样等于 CSV 单元格值，违反即 `WikiCompileError`。
  - `manifest.py` — `manifest.json`（条目摘要 + 章节级 record 锚点 + `record_to_entries` 反查 + 源指纹）与 `toc.json`（分类 → 表 → 条目行，在线 orient 的输入）。幂等：全局 `source_fingerprint`（含 `COMPILED_VERSION`，模板升级即失效）未变 → 零写入；变了则逐条目比 `digest()` 只重写变化的页，并删除消失条目的陈旧页。派生内容只落文件（`data/wiki/`，已 gitignore），DB 不参与。
- `app/observability/`（Phase 4）— 可观测与成本：
  - `tracer.py` — `LocalTracer` 基类（内存记账：span 顺序 + token 归因到当前 span，contextvar 栈）；`NullTracer`（默认，不加载 SDK 不触网）/ `RecordingTracer`（测试）/ `LangfuseTracer`（构造时才 lazy import langfuse，上报 trace 与 span）；`build_tracer(settings)` 工厂按 `langfuse_enabled` + key 构建，缺 key 降级 NullTracer。
  - `usage.py` — `TokenCounter` 按 route 聚合 token 与调用次数，`report()` 输出成本报表。
  - `faq_cache.py` — `FaqCache`（LRU + TTL，`now` 可注入）：key = 无历史问题，命中秒回。
- `app/api/routes/ask.py` 的 `get_ask_service()` 是模块级单例依赖（Chroma PersistentClient 每个进程只应持有一个实例）；测试通过 `app.dependency_overrides[get_ask_service]` 替换。`chat.py` 的 `get_agent()` 同理。

## 数据

- `data/raw/` — 原始中文 CSV（球拍、羽毛球、球线、手胶、球鞋）。
- `data/processed/` — 5 张清洗后的规格表（`球拍.csv`、`羽毛球.csv`、`球线.csv`、`手胶.csv`、`球鞋.csv`：中文文件名、中文表头），各表含来源列（作过滤 metadata）；另有 `knowledge/` 下 12 张文本知识表（均已接入检索，含 Phase 3 新增的 `规格常识`）。**表名体系**：`SPEC_TABLES`/`KNOWLEDGE_TABLES`（serializer.py）用**中文表名**（面向用户），Chroma collection 名用**英文**（Chroma 仅允许 `[A-Za-z0-9._-]` 3-512 位）——映射注册表在 `app/ingest/store.py::COLLECTION_NAMES`（`collection_name()` 中文→英文、`display_name()` 英文→中文，已合法名直通）。新增表须同时注册映射与 `primary_key`（行 id = `{collection}:{sha1(主键串)[:12]}`，见 `badminton-rag-incremental-sync-plan.md`）。
- `data/chroma/` — Chroma 持久化向量库（入库产物，可由 `python -m app.ingest.pipeline` 重新生成）。
- `data/wiki/` — LLM Wiki 派生层（`entries/<entry_id>.md` 785 页 + `manifest.json` + `toc.json`，约 4.4MB），由 `python -m scripts.build_wiki` 从 `data/processed/` 单向编译生成，同样被 .gitignore 忽略（要 review 生成页可临时去掉忽略规则）。**`data/processed/` 仍是唯一事实源，wiki 只是派生视图**：改了 CSV 必须重编译，`--check` 可判定 wiki 是否落后。
- `data/eval/` — golden set 评估：`golden.json`（12 题，/ask）、`agent_golden.json`（20 题，/chat，含 reference_answer 供 context_recall）、`result.json`/`agent_result.json`（通过率评估产物）、`baseline_pure_vector.md`（纯向量基线）、`quality_result.json`/`bad_cases.md`（Phase 4 四指标与 bad case 报告）、`wiki_comparison.json`（classic vs wiki 并排 A/B，`runs` 存两模式逐题分数、`comparison` 存并排平均）。
- `data/raw/`、`data/processed/` 文件被 .gitignore 忽略，仅保留目录结构；`data/eval/` 纳入版本控制。业务库为 **MySQL `badminton` 库**（`data/app.db` 这个 SQLite 旧副本已退役移出；sqlite 仅作测试临时库，与 MySQL 共用同一份 `_SCHEMA`）：表 `documents`/`audit_logs`/`feedback`/`users`（Phase 7 双角色账户，`feedback.user_id` 关联登录用户，0=匿名）+ Phase 8 用户端表 `conversations`/`messages`/`favorite_folders`/`favorites`/`posts`/`corrections`/`notifications`（旧库启动自动 ALTER 补列，见 `database.py::_migrate_users`）。**连接模型：每线程独立连接**（thread-local + 代际号，`reset_db` 后代际 +1 自动重建；sqlite WAL + busy_timeout 10s）——单连接被多线程并发使用会抛 `sqlite3.InterfaceError`（FastAPI 同步依赖跑在线程池，页面多请求并发触库），勿改回单连接。

## POST /ask 数据流

1. 问题向量化（百炼 `qwen3.7-text-embedding`）→ 跨全部 17 表（5 规格 + 12 知识）检索 top-10（每表取 8、同表最多 4 条）。
2. LLM 抽可选过滤条件 JSON（如 `{"拍身重量(U)": ["4U"], "最高磅数>=": 28}`）→ `apply_filters` 属性过滤。
3. 过滤后（开启精排时先经 BGE-Reranker 重排）取 top-5 拼进 prompt → LLM 生成中文回答，末尾附「来源：品牌 型号」。
4. 检索不到或答案不确定 → 输出「知识库中暂无相关信息」，禁止编造。

## POST /chat 数据流（Phase 3 Agentic RAG）

1. 路由 Agent（`app/agent/router.py`）分类 → equipment / rules / technique / chitchat / multi（LLM 优先 + 关键词兜底 + 跨类混合检测，带历史指代消解）。
2. 工具调用（`app/agent/tools.py`）：equipment 结构化查询（检索 + 抽过滤条件 + 属性过滤）/ rag_search 定向 collection 子集 / decompose 拆子问题 / chitchat 短路。
3. 多轮记忆（`app/agent/memory.py`）：MemoryStore 按 session_id 存历史，>8 条压缩为摘要；路由与检索注入历史（增强查询消解「这款拍」类指代）。
4. 生成：规格表记录拼 metadata 结构化行（品牌/型号/重量/磅数/平衡点/打法/适合水平/参考价）支撑精确对比。
5. 校验（`app/agent/verifier.py`）：LLM 判断回答是否由上下文支撑；兜底或不支撑 → 重检索一次（全表扩大候选）→ 仍不行降级；`retry_count` 上限防死循环。
6. 图编排（`app/agent/graph.py`）：LangGraph StateGraph，每个节点经 `_traced` 包装把 `{"node","input","output"}` 追加到 `state["trace"]`；`POST /chat` 响应含 answer / sources / clarification / trace。

## app/agent/ 模块（Phase 3）

- `state.py` — `AgentState` TypedDict + `trace_entry()`（浅拷贝摘要，保证可 JSON 序列化）。
- `router.py` — `classify(question, llm, history)` 五类路由；`is_multi_signal()` 多跳信号。
- `tools.py` — `equipment_query`（可限定规格表 collections）/ `rag_search`（按路由定向 collection 子集）/ `decompose`（LLM 拆子问题，失败回退原问题）/ `chitchat`（带历史闲聊）。
- `memory.py` — `MemoryStore`（内存版，session 隔离）+ `compress_history`（超限摘要化）。
- `verifier.py` — `verify()` LLM 判断支撑性，异常保守放行。
- `graph.py` — `BadmintonAgent`：LangGraph 编排（route → 工具 → generate → verify → retry→generate / 结束），`use_verifier` 开关，可选 `tracer` 参数（节点 span 记录）。

## 可观测与评测（Phase 4）

- **tracer 接入点**：`BadmintonAgent(tracer=...)`；`_traced` 包装在节点执行期间开 span、结束后 `span.end(输出摘要)`；`attach_llm` 把 `usage_hook` 挂到 LLMClient，LLM token 归因到当前 span（contextvar 栈）。`/chat` 每请求：`tracer.start_trace("/chat {trace_id}")` → `agent.invoke` → `tracer.end_trace(route/answer/retry/兜底)` → `token_summary()` 聚合后 `TokenCounter.add(route, usage)`。
- **FAQ 缓存**：`/chat` 仅当 `history 为空` 时查/写（key=问题原文）；写入条件 = `verified` 且无澄清且非 chitchat；命中响应 `cached=true` 且跳过 agent。
- **响应字段**：`POST /chat` 返回 `{answer, sources, images, clarification, trace, trace_id, cached}`（`images=[{url,title}]` 为图片文档的展示链接，与 sources 同源、used 优先；生成 prompt 要求以 markdown `![说明](url)` 输出图片，前端内联渲染 + 未内联图集兜底）；`GET /chat/stats`（管理员 JWT 或旧 X-Admin-Key）返回按 route 聚合的 token 报表。
- **四指标口径**：`scripts/eval_agent_quality.py` 复用 `scripts/ragas_eval.py` 的 judge 函数（faithfulness / answer_relevancy / context_precision / context_recall），上下文取 **agent 真实 `state["contexts"]` 的 document 文本**（不是检索候选池）；golden 的 `reference_answer` 是 context_recall 分母。
- **bad case 流程**：`eval_agent_quality --online` 产出 `quality_result.json` → `collect_bad_cases.py` 按分类（router/data/faithfulness/relevancy/retrieval/feedback）生成 `bad_cases.md`；`POST /feedback` 点踩记录自动并入。
- **Langfuse 开关与配置**（`.env`）：
  ```ini
  LANGFUSE_ENABLED=true
  LANGFUSE_PUBLIC_KEY=pk-lf-...
  LANGFUSE_SECRET_KEY=sk-lf-...
  LANGFUSE_BASE_URL=https://cloud.langfuse.com   # 兼容 Langfuse 官方变量名（config 里 AliasChoices 映射到 langfuse_host）
  ```
  缺 key 或未开启自动降级 NullTracer（不触网）。**依赖版本**：`langfuse>=4,<5`（v4，OpenTelemetry 基座）。
- **trace 结构（v4）**：v4 无独立 trace 对象，trace 由 trace_id 隐式标识（= `/chat` 响应的 `trace_id`，32 位 hex，可查/可开 URL）；**根 observation（`start_as_current_observation` + `TraceContext(trace_id)`）承载整体 input/output**；`propagate_attributes(session_id, tags, trace_name)` 在根创建前进入，把属性传播给所有 span；每个 agent 节点一个 SPAN（metadata 含 `duration_ms` 与 `tokens`），顺序创建自动挂到根下（兄弟节点）。注意：trace tags 只能带创建时已知的值（`["chat"]`），route 在 trace output 里。
- **flush**：`LangfuseTracer` 构造时注册 `atexit.flush`；`main.py` 关闭时经 `chat.flush_tracer()` 显式刷出。**读回是最终一致**：`api.trace.get` 可能先返回空 observations，稍后补齐（`scripts/check_langfuse.py` 已按此重试）。
- **验证工具**：`python -m scripts.check_langfuse --trace-id <id>`（重试至 observations 就绪）或 `--latest 5` 列出最近 trace 与 span 明细（含 parent/duration/tokens）。

## 部署与前端（Phase 5）

- **Docker Compose**：`docker compose up -d --build` 一键起（mysql → init 一次性入库 → api → web）。embedding 与生成 LLM 都走**百炼 API**，容器不再依赖 Ollama，只需 `.env` 配好 `LLM_API_KEY`（init 已 `env_file: .env`，缺失时 init 直接失败而不是在入库中途报 401）。数据卷 `mysql-data`（业务库）+ `app-data`（chroma 等入库产物）持久化，`down` 后 `up` 幂等。
- **前端 `web/`**（Vite + React + TS）：`LoginPage`（登录/注册，Phase 7）/ `ChatPage`（多轮会话 localStorage 持久化、引用弹层、赞/踩、缓存 badge、trace 外链）/ `Sidebar`（当前用户、知识库统计、管理员入口）/ `AdminPage`（成本报表 + 知识库管理 + 用户与权限 + bad case 静态渲染）。`api/client.ts` 请求路径**无 `/api` 前缀**（后端路由直接挂根）；dev 用 vite proxy 转发到 8000。bad_cases.md 在 `web/public/eval/`（构建时随 dist 进镜像），改动后需重新复制。
- **后端增量**（零回归）：`GET /kb/overview`（`app/api/routes/kb.py`，VectorStore 计数 + processed 文件扫描，降级安全）；`ChatResponse.langfuse_url`（NullTracer 时为 null）；CORS 由 `CORS_ORIGINS` 逗号分隔配置（config 的 `cors_origins_str` + property，避免 pydantic list 解析坑）。
- **requirements.txt**：Phase 3 起依赖 `langgraph>=1.0` 已在 Phase 5 补录（此前缺失会导致容器内 ModuleNotFoundError）。

## 文档/图片入库（Phase 6）

- **两条文档入口**（复用现有 `doc_{id}` collection 体系）：
  - **CLI 批量**：`python -m app.ingest.pipeline --dir data/raw_docs`（CSV 照常 + 追加目录内 pdf/图片/txt/md/csv）或 `--only-docs`。每文件一个 collection：`pdf_{file_hash前8位}` / `img_{...}` / `doc_{...}`，**按文件 sha256 hash 幂等**（同 hash collection 存在即跳过）。文件放在 `data/raw_docs/`（gitignored，仅保留 .gitkeep）。
  - **管理端上传**：`POST /admin/documents`（管理员 JWT，兼容旧 X-Admin-Key）。`ALLOWED_EXTS = {txt,md,csv,pdf} ∪ {png,jpg,jpeg,webp,bmp}`；上限 `upload_max_size`（默认 20MB，原硬编码 5MB）；`GET /admin/documents`（列表）/ `DELETE` / `POST .../reindex`（版本 +1）已有，Phase 6 前端补了管理 UI（AdminPage「知识库管理」区块：上传 + 列表 + 删除/重索引）。
- **解析分派**（`app/ingest/doc_ingest.py`）：`ingest_document()` 按**真实扩展名**分派（不信任 Content-Type）：csv → `_parse_csv`（「表头:值」）；txt/md → `_chunk_text`（chunk_size 默认 500，超长段滑动窗口重叠）；**pdf → `_parse_pdf`**（PyMuPDF `fitz` 逐页 `get_text` + `find_tables()` 表格转「表头:值」，每块带 `page_no`；**只支持有文字层的电子版 PDF**，扫描版整页无文字层 → 返回 failed「PDF 无文字层」）；**图片 → `_parse_image`**（OCR 文本 ≥ `ocr_min_chars`（默认 20）→ 文本块入 `doc_{id}`；低于阈值或无 OCR 引擎 → failed）。
- **OCR 抽象**（`app/ingest/ocr.py`）：`OcrEngine` Protocol（`ocr(image_bytes) -> str`）；`RapidOcrEngine`（RapidOCR + onnxruntime，CPU 离线，懒加载）；`FakeOcrEngine`（测试）；`build_ocr_engine(settings)` 按 `ocr_engine`（默认 rapidocr）构建，`none` 返回 None。admin 上传经 `get_ocr_engine` 依赖注入（测试 override）。
- **metadata**：pdf/图片块新增 `source_type`（pdf/image）、`page_no`（int，pdf）、`file_hash`（sha256 前 12 位）、`原始路径`（CLI 时）；沿用 `文件名`/`来源文件`/`doc_id`。**图片展示链路**：图片文档入库时会复制一份到 `data/uploads/docs/img_{hash}.{ext}`（配置 `doc_images_dir`，静态挂载 `/uploads/docs`，`main.py` 须先于 `/uploads` 注册），metadata 写 `图片URL`（`/uploads/docs/...`）——聊天回答据此内联展示；CLI 批量入库对缺 `图片URL` 的旧 `img_*` collection 自动删除重建（迁移升级），管理端上传/重索引均传入 `image_dir`（重索引已补 vision_embed 依赖）。原文件（`data/uploads/doc_*.ext`、`data/raw_docs/`）不对外暴露。
- **查询侧**：
  - 路由（`app/agent/router.py`）新增第 6 类 **`document`**（文档/资料/手册/上传/PDF/图片/图示/图里 关键词，优先级 multi > document > rules > …）；`graph.py` 加 `document` 节点（`rag_search(route="document")`）。
  - `rag_search`（`app/agent/tools.py`）document 路由**动态展开全部文档 collection**：`doc_*`/`pdf_*` 文本用文本 embedding 查询向量；`img_*` 多模态用 `vision_embed.embed_text` 向量（与文本向量**不同空间**，分开查询）。
  - BM25（`app/rag/retriever.py` `_ensure_bm25_index`）：索引与指纹纳入文档类 collection，修复文档无词法召回、重入库后缓存不失效的盲区；`Bm25Index.build` 现接受表名字符串。
  - **多模态图片索引（SiliconFlow API）**：无文字图片（插画/实拍图，OCR 文本 < `ocr_min_chars`）→ `vision_embed.embed_images` 经 **SiliconFlow `Qwen/Qwen3-VL-Embedding-8B`**（`POST /v1/embeddings`，`input={"image": data-URI}`，默认 4096 维）→ 存 `img_{hash8}` collection。图片与文本**同空间**（同一模型编码），查询时 document 路由用 `vision_embed.embed_text(question)`（4096 维）检索 img_*，与文本 embedding 分开。key 缺省回退 `rerank_api_key`（同为 SiliconFlow）；图片发第三方（需接受隐私约束）。**无本地 torch/大模型权重/HF 依赖**。实现：`app/ingest/vision_embed.py`（`SiliconFlowVisionEmbedder` + `FakeVisionEmbedder` + `build_vision_embedder`）。
- **工程配套**：requirements 加 `pymupdf`/`pymupdf4llm`/`rapidocr-onnxruntime`；config 加 `raw_docs_dir`/`doc_chunk_size`/`doc_chunk_overlap`/`ocr_engine`/`ocr_min_chars`/`vision_embed_enabled`/`vision_embed_model`/`vision_embed_dim`/`vision_api_key`/`vision_base_url`/`upload_max_size`；nginx 加 `client_max_body_size 20m`（原默认 1MB 会 413）；Dockerfile 加 `libgomp1`（onnxruntime）+ `COPY data/raw_docs`；compose init 追加 `python -m app.ingest.pipeline --only-docs`。
- **测试**：`tests/test_doc_ingest_pdf.py`（fitz 生成最小 PDF：中文用 `fontname="china-s"`；单行 `insert_text` 会按页宽截断，长文本用逐行插入）、`test_pipeline_cli.py`（目录收集/幂等）、`test_ocr.py`、`test_doc_ingest_image.py`（FakeOcrEngine）、`test_admin_upload_pdf.py`（override `get_ocr_engine`）、`test_retriever_doc_route.py`、`test_agent_document_route.py`。全部离线。

## 双角色账户与 RBAC（Phase 7）

系统分为两个角色：**用户（user）**与**管理员（admin）**。骨架已就绪（账户体系 + 登录认证 + 角色控制），用户端/管理端功能模块按 `用户和管理员的设计.md` 后续填充。

- **用户表**：`users`（`username` 唯一 / `password_hash` / `role` `user|admin` / `nickname` / `permissions` JSON 字符串，NULL=全部 / `is_active` / `created_at` / `last_active_at`）。`app/db/repos.py` 新增 `UserRepo` + `user_to_public()`（剥离密码哈希）。
- **认证端点**（`app/api/routes/auth.py`，前缀 `/auth`）：`POST /auth/register`（仅创建 role=user，注册即返回 token）、`POST /auth/login`（返回 token+user，按 role 前端分流）、`GET /auth/me`（需 Bearer）。令牌为 HMAC-SHA256 签名的 `payload.签名` Bearer token（`app/core/security.py`，纯 stdlib，无新依赖；生产用 `AUTH_TOKEN_SECRET` 覆盖）。
- **种子管理员**：配置 `BOOTSTRAP_ADMIN_USERNAME`/`BOOTSTRAP_ADMIN_PASSWORD`，`main.py` 启动时（lifespan）幂等创建。`.env` 已内置 `admin/admin123456`（仅开发）。
- **鉴权依赖**（`app/api/deps.py`）与策略：
  - 用户端接口（/ask、/chat、/feedback、/kb/overview）**登录可选**：带 Bearer 即关联用户（feedback 落 `user_id`），匿名仍可用（兼容旧测试与匿名访问）。
  - 管理/审计端点（/admin/documents*、/audit*、/chat/stats）：`require_admin_access` = 管理员 JWT **或** 旧 X-Admin-Key 任一通过（向后兼容，旧 `ADMIN_API_KEY` 仍有效）。
  - 用户与权限管理（`/admin/users` GET/PATCH，独立 `users_router`）：**严格 `require_admin`**，仅登录的管理员账户可访问，旧 X-Admin-Key 也不行（401）。PATCH 支持 `role` / `is_active` / `permissions`（模块级权限，对应设计文档「角色权限分配」）。
- **前端**（`web/src/`）：`api/auth.ts`（localStorage 持久化 token+user）/ `LoginPage`（登录/注册）/ `App.tsx` 按 `session.user.role` 路由（`#/login` → `#/` 用户端 / `#/admin` 管理端，管理员越权访问管理端显示无权限页）；`Sidebar` 显示当前用户与管理员入口；`AdminPage` 用管理员 JWT（新增「用户与权限管理」区块）；`ChatPage`/`ChatMessage` 发 chat/feedback 时带 Bearer。vite proxy 含 `/auth`。
- **测试**：`tests/test_auth.py`（注册/登录/me/种子管理员/用户管理严格 RBAC/旧 key 兼容）；`tests/test_vision_embed.py` 的 fallback 用例显式清空 `vision_api_key`（避免本机 `.env` 的 `VISION_API_KEY` 掩盖回退逻辑）。

## 用户端功能（Phase 8）

按 `用户端功能实现设计.md` 完成设计文档「用户端」全部模块（P0/P1）：
P0 智能问答中枢（文字+预设卡片+范围限定+语音输入+引用溯源）+ 历史对话记录 + 反馈评价；P1 收藏夹 + 知识库目录 + 语音；二次强化互动纠错/动态/通知/资料与偏好。流式输出（SSE）未做，一次性 JSON 响应 + 前端「思考中」。

- **数据模型**（`app/db/database.py`）：`users` 扩展 `gender`/`level`/`racket_model`/`avatar`/`pref_style`/`pref_show_sources`（旧库 `_migrate_users` 自动 ALTER）；新表 `conversations`（(user_id, session_id) 唯一）、`messages`、`favorite_folders`、`favorites`、`posts`、`post_likes`（动态点赞，UNIQUE(post_id,user_id)）、`post_replies`（楼中楼：parent_id=NULL 一级，否则一级 id；reply_to_user_id 记录实际被回复者）、`reply_likes`（UNIQUE(reply_id,user_id)）、`corrections`、`notifications`。
- **动态点赞/回复语义**：点赞均为 toggle（赞/取消），**每用户每条限 1 次的唯一约束由 post_likes/reply_likes 保证**，posts.likes 为冗余计数（并发双击用唯一约束冲突兜底，绝不双加，见 `PostRepo.toggle_like`）；回复仅一层楼中楼（对二级回复的回复自动上挂一级，reply_to 指向实际被回复者）。
- **`POST /chat` 扩展**：可选 `scope`（equipment/rules/technique/document）→ 路由节点强制该路线（`graph._route_node`，state 新增 `scope`）；登录用户自动落会话+消息（`ConversationRepo.upsert` + `MessageRepo.add`，旁路失败不影响回答）。
- **`/user/*` 路由**（`app/api/routes/user.py`，全部需登录）：会话 CRUD/搜索（q/tag/favorite）、收藏夹与文件夹、动态（文本+图片+点赞）、热门排行（feedback 赞+收藏聚合 SQL）、纠错提交/我的纠错、通知列表/已读、图片上传（`POST /user/uploads`，png/jpg/jpeg/webp ≤2MB → `/uploads/{name}`）。
- **`GET /kb/catalog`**（公开，`kb.py`）：按文件名关键词分组成 装备规格/规则库/技术库/伤病康复库/球星专辑/文档资料/其他知识，每项含 chunk 数。
- **静态服务**：`main.py` 挂载 `/uploads` → `data/uploads/posts`（`user_uploads_dir`；只暴露动态配图，文档原文件不在该目录）。nginx/vite proxy 均已加 `/auth`、`/user`、`/uploads`、`/kb/catalog`。
- **前端**（移动优先 4 Tab）：`UserShell`（底部导航 + 悬浮「＋」）+ `ChatPage`（预设卡片/范围下拉/Web Speech 语音/复制/收藏/点踩弹窗）+ `DiscoverPage`（目录/热门/动态发布与点赞）+ `WorkbenchPage`（历史记录/收藏夹）+ `ProfilePage`（资料/偏好/通知/纠错）；`api/user.ts` 封装全部 /user 接口。页面以 `key={chatKey}` 重挂载切换会话。
- **测试**：`tests/test_user_api.py`（10 例：会话落库/CRUD/scope/收藏/动态上传/热门/纠错/通知/资料/未登录 401/catalog/上传校验）；`tests/conftest.py` 新增限流器隔离（进程级令牌桶跨测试会用空触发 429）。
- **注意事项**：`/chat`、`/ask`、`/kb/overview` 仍匿名可用（登录才落库）；目录分组走文件名关键词（新增 knowledge 表建议按 规则/技术/伤病/球星 命名以便归类）。

## 数据库后端：MySQL 默认 / SQLite 仅测试（Phase 9）

业务库支持双后端，`DB_BACKEND`（mysql | sqlite）切换，**运行时默认 mysql**；sqlite 只是离线测试的临时文件库（每个测试一个，跑完即弃），不作为开发/生产的数据存储。

- **本机事实（2026-08-29 核实）**：MySQL 8.0.41 以 Windows 服务 `MySQL80` 跑在 `127.0.0.1:3306`，`badminton` 库是业务数据的唯一权威；项目里的旧副本 `data/app.db`（SQLite）已退役移出仓库目录，不要再按「默认 sqlite」的旧假设排查数据问题。

- **连接层**（`app/db/database.py`）：每线程独立连接 + 代际号（`reset_db` 后代际 +1 自动重建）；
  - sqlite：WAL + busy_timeout 10s（沿用既有策略）；
  - mysql：PyMySQL（懒加载，`_MySQLConn` 代理把 repos 的 `?` 占位符翻译成 `%s`、行返回 dict，`executescript` 逐条执行 DDL）。**首次连接自动建库**：目标库不存在（1049）→ 免库连接 `CREATE DATABASE IF NOT EXISTS`（库名禁止反引号）→ 重连；账号需有 CREATE 权限。
- **DDL 转换**（`to_mysql_ddl()`，纯函数可单测）：自增主键、`DEFAULT CURRENT_TIMESTAMP`、`CREATE INDEX IF NOT EXISTS` 移除（已存在时忽略）、带默认值/被索引的 `TEXT` → `VARCHAR`、时间戳列 → `DATETIME`、建表 `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`。**新增表请先在 `_SCHEMA` 加 SQLite DDL，MySQL 由转换函数自动生成**（注意：TEXT 列不能带 DEFAULT、不能直接建索引，必要时给转换函数补规则）。
- **repos 层**：SQL 统一用 `?` 占位符；时间戳一律用 `database.ts_expr()`（sqlite → `datetime('now','localtime')`，mysql → `NOW()`），不要再写死字面量。
- **配置**：`db_backend`/`mysql_host`/`mysql_port`/`mysql_user`/`mysql_password`/`mysql_db`/`mysql_charset`（默认 utf8mb4）；`.env.example` 有注释模板。
- **Docker Compose**：`mysql:8.0` 是**默认服务**（不再挂在 profile 下），`docker compose up -d --build` 即一起拉起；api/init 的 `DB_BACKEND` 默认 `mysql`、`MYSQL_*` 由 `.env` 注入。注意 compose 的 mysql 容器用 `MYSQL_USER` 建业务账号，`.env` 里 `MYSQL_USER=root` 时容器会拒绝初始化——用本机 MySQL 就直接 `MYSQL_HOST=127.0.0.1` 跑本地 api，别起这个容器。
- **测试**：`tests/test_mysql_dialect.py`（DDL 转换 + 占位符翻译 + ts_expr + 自动建库，离线）；已在本机 MySQL 上做过真实全链路验证（建表/中文读写/多线程并发/FastAPI 各接口）。**测试永远走 sqlite**：`tests/conftest.py` 顶层强制 `os.environ["DB_BACKEND"]="sqlite"`（环境变量优先于 `.env`），本机 `.env` 开 MySQL 不影响 pytest 离线。**注意**：MySQL 与 SQLite 数据不互通，切后端后业务数据从空库开始；Chroma 向量库不受影响。

## 管理端后台（Phase 10）

按 `管理端功能实现设计.md` 完成设计文档后台六大模块（P0 总览/知识库/调优 + P1 审核/用户权限 + P2 系统只读；「待入库语料审核」因用户端上传未上线而暂缓）。后端模块权限用 **`require_admin_module`**，前端 `AdminPage` 重构为 6 模块左侧导航。

- **模块级权限**（`app/api/deps.py`）：`ADMIN_MODULES = ("dashboard", "kb", "rag", "review", "system")`；`require_admin_module(module)` 仅认**管理员 JWT**（旧 X-Admin-Key 直接 401）。`users.permissions` 语义：NULL/`""`/`"null"` = 全部模块；`"[]"` = 无；JSON 数组 = 仅列出的模块。**users 与 audit 端点维持 `require_admin_access` 不动**（前端导航中「用户与权限」对所有管理员可见，其余 5 个模块按 permissions 过滤，见 `web/src/pages/AdminPage.tsx::parsePerms`）。
- **新表**（`app/db/database.py`，MySQL 自动转换）：`prompt_templates(id, name, description, system_prompt, is_active, created_at, updated_at)`（`init_db()` 幂等播种 3 条：默认知识助手/裁判员语气/教练员语气，均 inactive）；`rag_settings(setting_key TEXT PK, value, updated_at)`（**列名用 `setting_key`——`key` 是 MySQL 保留字**；键：`vector_top_k`/`filter_top_k`/`rerank_enabled`/`blacklist_enabled`，缺省回退 config 默认值）；`rag_dictionary(id, type, word, values_json, created_at, updated_at, UNIQUE(type, word))`（`type=synonym` 词条：word 为锚点词、values 为其余同义词 JSON 数组；`type=blacklist`：敏感词，values 空闲）。`documents` 加列 `tags TEXT`（`_migrate_documents` 自动 ALTER）。
- **repos 新增**：`DocRepo.set_tags/count_by_type/count_by_status`；`CorrectionRepo.list_all(status)/get_any/count_by_status/update`；`FeedbackRepo.count_dislikes/bad_questions`（点踩按问题聚合）；`ConversationRepo.count_all`；`MessageRepo.count_all/count_since`；`PromptTemplateRepo`（list_all/get_active/create/update/delete/set_active）；`RagSettingsRepo`（`KEYS` 常量 + get_all/set_many，`set_many` 用 UPDATE-then-INSERT 的 upsert，无方言 ON CONFLICT）；`RagDictRepo`（list_by_type/add/delete/synonyms_groups/blacklist_words；`add` 失败先 `rollback()` 再抛，防残留未提交事务）。
- **端点**（`app/api/routes/`，`__init__.py` 聚合，全部 `require_admin_module` 门禁）：
  - `admin_dashboard.py`：`GET /admin/dashboard`（文档按类型/向量总览/消息与今日问答数/用户数/待审纠错/点踩汇总/route 成本报表）；`GET /admin/health`（DB/Chroma/百炼/SiliconFlow 探活，探针函数可注入供测试 stub；百炼探针一次 `/models` 同时覆盖 LLM 与 embedding）。
  - `admin_rag.py`：`GET/PUT /admin/rag/settings`；`GET/POST/PUT/DELETE /admin/rag/prompts` + `POST /admin/rag/prompts/{id}/activate`；`GET/POST/DELETE /admin/rag/synonyms`、`/admin/rag/blacklist`；`POST /admin/rag/debug`（RAG 沙箱：路由→扩展查询→候选块（文本/得分/来源）→过滤条件→上下文→回答，`with_answer=false` 时不调 LLM，组件从 agent 实例复用，管理员限流）。所有参数/模板/词典变更后调用 `chat.reload_agent()`。
  - `admin_review.py`：`GET /admin/corrections?status=`（JOIN users）+ `PATCH /admin/corrections/{id}`（accept → 经 NotificationRepo 通知提交者；rejected/discussion 仅落状态）；`GET /admin/qc/bad`。
  - `admin_system.py`：`GET /admin/system`（模型/限流/上传/库配置只读，密钥掩码）。
  - `admin.py` 扩展：`PATCH /admin/documents/{id}/tags`（`DocRepo.set_tags` + `VectorStore.update_metadata` 同步 Chroma 元数据，不重嵌）。
- **RAG 参数化接线**：`app/api/routes/chat.py::_build_agent()` 每次构建从 `RagSettingsRepo`（vector_top_k/filter_top_k）、`RagDictRepo.synonyms_groups()`（注入 `Retriever.extra_synonyms`）、`PromptTemplateRepo.get_active()`（注入 `BadmintonAgent.generate_system`）读取，缺表/异常回退默认不阻断；`reload_agent()` 置 `_agent=None + _guard_cache=None` 强制下次重建。黑名单门禁在 agent 调用前：`app/rag/guard.py::contains_blacklist/blacklist_reply`（`_get_guard_config` 读 settings 与词典）。`rerank_enabled` 目前仅存储/展示，`/chat` 实际路径尚未接精排。沙箱链路回放见 `app/rag/debug.py::debug_pipeline`。**回答清洗**：`_generate_node`/`_chitchat_node` 输出前经 `graph._clean_answer()`——还原 `\n`/`\t`/`\"` 双转义为真实字符、剥离末尾「来源：…」（行或内联）标注；默认生成 prompt 已改为**禁止**在 answer 正文输出引用标注（来源由结构化 `sources` 展示）。
- **前端**：`web/src/pages/AdminPage.tsx` 仅剩导航容器（6 模块 + 权限过滤 + bad_cases.md 静态区），各模块拆到 `web/src/components/admin/{Dashboard,Kb,Rag,Review,Users,System}Section.tsx`；`api/client.ts` 增 `adminDashboard/adminHealth/getRagSettings/putRagSettings/ragDebug/listPrompts/createPrompt/updatePrompt/deletePrompt/activatePrompt/listDict/addDict/deleteDict/listCorrections/patchCorrection/badQuestions/systemConfig/patchDocTags` 及 `DocRecord.tags`；样式在 `web/src/styles/index.css`（`.admin-*`/`.rag-*`/`.health-pill`/`.dict-chip`/`.perm-chip`）。
- **测试**（全部离线，用 `_isolate` fixture：`reset_db()+init_db()` + 幂等 `_admin_headers()` 复用已建管理员用户名避免 UNIQUE 冲突）：`test_admin_dashboard.py`（统计/探活 stub）、`test_admin_rag.py`（settings 默认值与保存/prompts CRUD 与激活/词典与黑名单/debug 用 StubLLM+StubAgent+FakeEmbedder+内存 VectorStore）、`test_admin_review.py`（工单/采纳通知/低质量聚合）、`test_admin_module_rbac.py`（模块权限 401/403：无模块权限 403、旧 key 401、NULL=全部）、`test_doc_tags.py`（打标 DB+Chroma 同步）、`test_db.py`（tags 迁移 + 新表与种子）、`test_mysql_dialect.py`（`setting_key VARCHAR(100) PRIMARY KEY` 等新规则 + ENGINE 计数 17）。

## 小程序版（Phase 11，进行中）

按 `小程序版可行性方案.md`（技术/合规/周期）+ `小程序版设计方案.md`（视觉/页面/接口）+ `mp-prototype/`（6 屏高保真原型 375×812）推进，产品名**羽问**。当前进度 **W1~W4 完成**（微信登录/手机号绑定 + 10 页完整功能 + 内容安全/订阅消息骨架 + tabBar 图标 + 提审清单文档；真机 golden 走查以 `agent_result_w4.json` 为准）。**个人主体合规**：微信不给个人主体开放社交/UGC 动态类目，发现页「球友动态」模块与 `post-create`/`post-detail` 页已从 mp 端移除（后端 `/user/posts*` 与 H5 端保留不动）。

- **后端微信登录**（`app/api/routes/auth.py`）：`POST /auth/wechat {code, nickname?}` → `wx_code2session()`（httpx 调微信 code2session；**模块级独立函数，测试 monkeypatch 注入，不触网**）→ 按 openid 查 `users.openid`（无则 `UserRepo.create_wx` 自动建号：用户名 `wx_{openid前29位}` + 随机密码哈希，禁止密码登录）→ 签发既有 token，响应附 `is_new`。未配置 `WX_APPID/WX_SECRET` 返回 500（显式提示），key 无效 401，网络异常 500。
- **数据层**：`users` 加列 `openid`（`_USER_COLUMNS` + `_migrate_users` ALTER，MySQL→VARCHAR(100)）；**openid 唯一索引在 `_migrate_users` 中创建而非 `_SCHEMA`**（旧库执行 DDL 时列还不存在会炸；MySQL 无 IF NOT EXISTS，try/except 忽略 1061）。
- **配置**：`wx_appid`/`wx_secret`（环境变量 `WX_APPID`/`WX_SECRET`）；`wx_subscribe_template_id`（订阅消息模板 id，留空不发送）。
- **微信开放能力**（`app/security/wx_sec.py`，全部配置门控 + 可注入 + 离线测试）：`check_text(text, openid)`（msgSecCheck v2：True 通过/False 违规/None 未配置或异常放行，access_token 进程内缓存）；`send_subscribe_notice(openid, template_id, page, data)`（None=未配置/True/False 不抛出）。接入点：`user.py` UGC 三处守卫（动态 `POST /user/posts`/回复/纠错，`_text_guard` 违规 422）；`admin_review.py` 纠错**采纳**时经 `_wx_notify_correction` 推送订阅消息（旁路）。
- **mp/（Taro 4.1.8 + React18 + TS + Sass）**：官方 default 模板手工搭建（`npx @tarojs/cli init` 交互式无法自动化；模板文件在 npx 缓存 `@tarojs/cli/templates/default`；对应源码仓库 taro-project-templates 无 4.1.8 分支，不要重试 CLI init）。**设计稿 750，样式全用 rpx（= 原型 px×2）**，tokens 见 `mp/src/styles/theme.scss`。**10 个页面**：login（微信一键登录全屏 Hero）/ chat（**W2 完整**：问候态+预设卡+范围 Picker+气泡全状态——打字机 `components/typewriter.tsx`（长文本提速、缓存直出全文）、缓存徽标、失败重试、引用来源面板（`pref_show_sources=0` 时整体隐藏）、图片内联+`previewImage` 预览、赞踩反馈、⭐收藏（createFavorite）、↗分享（qa-detail）、发现页预填 `mp_prefill` 消费、工作台会话续聊 `mp_active_conv` 消费）/ discover（热门问答；**球友动态流与发布入口已因个人主体合规移除**）/ workbench（**会话管理**：改名/标签/收藏/删除；**收藏夹**：新建/删除/移动/删除收藏；点击进 conv-detail）/ profile（偏好即改即存 `/auth/profile`：seg 语气 + switch 引用；菜单已接通知/纠错/发现）/ 新页 conv-detail（历史回放+继续提问）、notifications（未读角标+全部已读）、corrections（提交表单+记录状态徽标+提交后 `requestSubscribeMessage`（模板 id 见 `src/config.ts::SUBSCRIBE_TEMPLATE_ID`，留空跳过））、qa-detail（转发落地 `useShareAppMessage` + canvas 海报保存相册）。`mp/src/api/request.ts` 封装 wx.request（Bearer 注入、`{code,message,data}` 解包、401 清登录态 reLaunch 登录页、`API_BASE_URL` 暂指 `http://127.0.0.1:8000`，上线换备案域名并在微信后台配 request 合法域名）。tabBar 为文本模式（未配图标 png），appid `touristappid` + `urlCheck:false` 仅开发。**W2 验收口径**：多路由真实 /chat 抽检 5/5 CLEAN（无「来源：」标注、无字面 `\n`；「如何预防网球肘」返回兜底文案属预期）。
- **注意**：`/user/hot` 实际需登录（返回键名 `hot` 非 `items`）；查询串手拼不用 URLSearchParams（基础库兼容）；Taro 4.1.8 对 `showModal.editable`/`requestSubscribeMessage` 类型定义漂移 → 用 any 桥接（见 workbench `promptModal`/corrections `askSubscribe`）。**Taro 页面样式必须显式 `import './index.scss'`**（官方模板如此；漏掉则页面无任何样式且编译不报错——app.scss 只覆盖全局，页面 wxss 不会自动生成，表现=工具里全是裸文字）。
- **W4 交付**：账号绑定（`users.phone` 列 + `_migrate_users` 唯一索引，同 openid 模式；`POST /auth/wechat/phone`（getuserphonenumber，`wx_get_phone_number` 可注入，已被他人绑定→409）、`POST /auth/unbind {type}`；`user_to_public` 增 `wx_bound`/`phone_bound`；`UserRepo.bind_openid/bind_phone` 简化赋值（旧写法 `WHERE ... OR openid=?` 在解绑 None 时恒假——**更新列不要带 NULL 等值条件**）；测试 `tests/test_auth_bind.py`（注意：fixture 只能改真实 settings 字段，整体替换 get_settings 会造成 token 签发/解密 secret 不一致））；设置页 `pages/settings`（绑定状态/解绑、`Button open-type=getPhoneNumber` 绑定、清缓存、隐私入口、版本/备案占位）；tabBar 图标 `mp/src/assets/tabbar/*.png`（System.Drawing 生成 8 张 81×81，正常灰 `#7c8b82`/选中绿 `#047857`，folder 图标需标准圆角矩形 GraphicsPath 拼接）；提审清单 `小程序提审清单.md`（类目/备案/隐私/内容安全/审核驳回点/真机 golden 步骤）。
- **W4 质量修复（golden 20 题抖动归因）**：① 模型：`deepseek_model`（该字段现为 `llm_model`）默认 `deepseek-chat`（flash 版本随机兜底 55%，chat 单签 80%）；② **部分作答重试**（graph.py `_generate_node`）：LLM 整答兜底但有上下文时，用 `_RULE3_PARTIAL`（规则3 替换为「必须作答、禁止兜底文案」）强制二次生成——解决「检索有数据却幻觉式兜底」（a6/a10 类）；③ **强多跳信号**（router.py `_strong_multi_signal`：还是/VS/区别 + 影响/后果/适合/怎么选 → 强制 multi，先于 LLM 判定）——解决「对比+追问」被 LLM 误判单类（a15 类）。golden 评测口径=repeat 3 多数制（与 Phase 3 基线一致），过程记录与已知缺口见 `小程序提审清单.md` 第五节。
- **W4 之后（上线冲刺）**：备案与提审执行（依赖真实主体/域名）、帮助与反馈入口页、`API_BASE_URL` 换 HTTPS 域名、订阅消息模板字段联调、手机号合并（已有账号同手机号 → 目前直接 409 不自动合并）。
- **测试**：`tests/test_auth_wechat.py`（自动建号/同 openid 复用 is_new/坏 code 401/未配置 500/服务异常 500/微信账号不可密码登录/缺 code 422）；`test_db.py` 新增 openid 旧库迁移 + 唯一约束拦截用例。

## LLM Wiki 模式（W1~W5，进行中）

按 `badminton-rag-llm-wiki-plan.md` 推进：把检索从「相似度 top-k 句子投票」改造为「离线编译 Wiki + 在线 LLM 导航式检索」。四项已确认决策：**A+B 形态**、**并行新增 + 特性开关**、**模板骨架 + LLM 补概念**、**精度优先 + 沿用现有四指标**。当前进度 **W1~W3 完成**（W3 为**改造版**：只做「补展开循环」，未做完整动作空间；编译 + 索引 + orient/read/step + 沙箱回放已接通，`WIKI_MODE_ENABLED=false` 时问答行为逐字不变）。

- **W1 交付**：`app/wiki/{schema,compile,manifest}.py` + `scripts/build_wiki.py`。829 条 CSV 记录 → **785 个条目页**（product 714 / concept 71，2397 个章节，341 个条目带模板推导的出链），全部可 100% 回溯到 record id；重跑幂等（源未变 → 零写入，改一行 → 只重写该页）。
- **概念条目粒度决策（W1 落地时定）**：`规格常识` 按 `规格项` 分组（4 页，值成节）、`BWF官方规则`/`常见判罚` 按类型分组、小体量对比表（毛片等级/球头材质/速度等级/两类影响因素）整表一页、其余（战术/手法/步法/毛片类型）逐行成页 —— 兼顾「知识单元闭合」与「一行一锚点可回溯」。
- **W2 交付**：
  - `app/wiki/indexer.py` — `wiki_page`（条目概况）+ `wiki_section`（章节全文，文档 id=`entry_id#section_key`）两个 collection；按 `digest` 元数据增量重嵌（全量 3182 段中 1441 段可跳过），消失的 id 自动清理；`index_state.json` 记源指纹，`index_is_current()` 供健康检查。`VectorStore` 为此新增 `delete()` / `list_ids()`，嵌入按 `ingest_batch_size` 分批（一次丢几千段会撞 embedding 客户端的 60s 超时）。
  - `app/wiki/navigator.py` — orient 三步：**①目录一级分类**（17 个分类路径，LLM 选 ≤3）→ **②条目清单**（超过 `TOC_ENTRY_CAP=40` 行时先用 `wiki_page` 向量粗排）→ **③hybrid 反查补齐**。关键实现点：hybrid 命中的 record 经 manifest 的**章节级 records 锚点**直接定位到「命中的那一节」——只给 entry_id 时 `read()` 只能按前 N 节展开，实测「4U 代表多少克」会因展开 2U/3U 节而兜底。`read()` 的展开策略（首轮 A/B 后修正）：条目已点章节（LLM 指定 / hybrid 反查）→ 就展开那几节；未点章节 → **整页章节全部进候选**，候选超出 `max_contexts=8` 时在候选集内按问题向量精排取前 N（`VectorStore.query` 为此新增 `ids` 约束）。这一处同时修两类问题：technique 路由的章节级稀释（5 条目 × 2 章节的噪声）、以及「一个概念页需要多节才能答全」的题（如《飞行稳定性影响因素》）。`read()` 产出与 classic **同构的 context dict**（多带 `entry_title/section_title/facets/records` 元数据），故 verify、来源、图片、trace 全链路无需分叉。
  - `app/wiki/prompts.py` — orient 两级漏斗的 system 提示。**「宁可少选、禁止编造 id、id 必须照抄」是刻意反向约束**：让 LLM 承担 precision、向量承担 recall。
  - graph 接入：`AgentState` 新增 `mode` / `wiki_trace`；`route` 节点归一化 mode（请求级 `mode` > 全局 `WIKI_MODE_ENABLED` > classic，导航器不可用一律 classic）；`mode=wiki` 的非闲聊路由进新 `wiki` 节点，classic 五个检索节点与边**逐字未动**；`_format_contexts()` 按上下文自分派（wiki → `条目《X》§Y（属性：…）`）；`resolve_source` 支持 `(条目标题, 章节名)`；wiki 未取到知识单元时**自动回落该路由的 classic 检索**并在 `wiki_trace.degraded` 记原因；retry 对 wiki 链路改为「放开分类重新 orient」而非扩大 top-k。`POST /chat` 请求体 +`mode`，响应 +`mode`/`wiki_trace`。
  - 评测：`scripts.eval_agent_quality --mode classic|wiki|both`（both 需 `--online`，产出并排对照 + `data/eval/wiki_comparison.json`）。新增 `context_precision_strict`（上下文展开回原始 record 后用**同一个 judge** 判相关性）与 `token_efficiency`（上下文中相关信息占的字节比例，值域 0~1）；`ragas_eval.judge_context_precision` 抽出 `judge_context_relevance` 供复用，避免复制提示词造成口径漂移。**classic 下两条口径必然重合**（一条上下文恰好一行），这是 `_strict` 可比的依据，已有离线用例锁定。
- **W3 交付（改造版：只做「补展开循环」，不做完整 Open/Follow/Filter/Search 动作空间）**：
  - `WikiNavigator.navigate(question, route, max_steps)` = orient → read → **至多 `max_steps` 轮补展开**；`collect()` 已删，graph 的 `wiki` 节点与 retry 都走 `navigate`。
  - `_expandable(targets, contexts)` 组成**可补展开池**：已展开条目的剩余章节 + 这些条目 `links.out` **一跳**指向的条目章节（限一层，防链接图漫游）。
  - `prompts.STEP_SYSTEM` 的语义：LLM 看「已展开 / 可补展开」两份清单，输出 `{"enough": bool, "expand": [{"id","sections"}]}`。**池外的 id 一律丢弃**；丢弃后没有合法目标就本轮即停，但 `enough` **保留 LLM 原判**（轨迹如实记录「觉得不够但无合法目标」，不粉饰）。
  - 章节选择改为**按条目公平轮转**：`_rank_sections` 先给每个已打开条目留最相似的一节，再按相似度补第二轮/第三轮，直到 `max_contexts`（纯相似度截断会让单个条目占满预算、把别的条目挤掉，实测掉多跳召回）。
  - 三个预算参数：`max_entries=5`（条目数）、`max_sections=2`（单条目单次展开节数）、`max_contexts=8`（喂生成的章节总数）、`max_steps=1` / `max_expansions=4`（每问 LLM 调用 = 路由1 + orient2 + step1 + 生成1(+强制二次) + 校验1 ≈ 5~6 次，与 classic 的 multi 路由同量级）。服务端配置 `WIKI_MAX_STEPS` 经 `build_navigator(max_steps=...)` 注入。
  - `wiki_trace` 新增 `steps` 字段（每轮的 `enough` 与补展开的章节文档 id），retry 对 wiki 链路改为「放开分类重新 orient + 至少补展开一轮」。
  - **管理端沙箱**：`POST /admin/rag/debug` 请求体 +`mode`，`mode=wiki` 时走 `app/rag/debug.py::wiki_debug_pipeline`，输出的每个候选带 `origin`（orient / step）、`context_block` 用 `条目《X》§Y` 格式、附 `wiki_trace`；未装配 wiki 时回落 classic 并在响应里给 `wiki_unavailable` 说明。
- **W4 交付（第一部分：category 聚合条目，plan 判断的 precision 最大来源）**：
  - `CATEGORY_FACETS` 声明每张规格表可聚合的列（`CategoryFacet(column, split, min_members=5)`）：球拍按 拍身重量(U)/平衡点类别/打法类型/适合水平/品牌，羽毛球按 羽毛类别/球头类别/品牌，球线按 材质/品牌，手胶按 材质类别/颜色/品牌，球鞋按 品牌；`split=False` 用于品牌这类「中文+拉丁文」整体值（避免 `威克多 VICTOR` 拆成两页）。成员数低于门槛不建页，挡掉长尾噪声。**全量语料产出 68 个聚合页**（球拍 25 / 羽毛球 14 / 手胶 12 / 球线 11 / 球鞋 6）。
  - 聚合页结构：`聚合概况` 节（成员总数 + 按品牌分布 + 价格区间）+ **按品牌分节**，每节最多 `MAX_CATEGORY_MEMBERS=12` 行成员摘要（`型号｜重量｜平衡点｜打法｜水平｜参考价`），超出写明「本节共 N 款，仅列出前 M 款」—— 展开一节不会撑爆 token，需要更多成员时由 `step` 补展开下一节。
  - 链接：`link_categories` 挂 **产品页 ↔ 所属聚合页**（成员映射在编译期顺手产出，避免事后 O(n²) 反查）与 **聚合页 → 该取值的概念页章节**（复用 `_link_index` 值索引，如《4U 球拍》→《拍身重量U数》§4U）。
  - **校验器语义拆分**：主条目（product/concept）继续承担三条铁律（每条记录被**恰好一个主条目**锚定、逐单元格回溯、facets 原样等于单元格）；category 页是**派生视图**，允许重复锚定成员行，只校验「聚合取值来自某成员行该列的主干」与「每个成员都能回溯到主条目」——不让聚合页变成第二个事实源。`_category_key` 负责 `头重(进攻)` → `头重` 这类归一（同一取值不分裂成两页）。
  - 锚点渲染压缩：`Entry.frontmatter()` 里**章节级只列 record id**，完整出处（表/文件/行）在条目级保留一份 —— 否则 191 成员的聚合页会把 frontmatter 撑爆；`Section.from_dict` 用条目级锚点表还原 id 引用，往返与 digest 不变。
  - 修 `write_wiki` 零写入快路径漏判：**只比源指纹 + 失效条目，会漏掉「源未变但新增了条目」**（W4 第一次落盘时 68 个新页被整体跳过）。快路径现在额外要求条目 id 集合一致，并补了回归用例。
  - orient 提示明确「聚合视图/…」是**筛选类问题**（推荐/对比一批满足某些属性的东西）的正确落点，替代原来「检索 18 条 → LLM 抽条件 → 属性过滤」这条脆弱链路。
  - 当前规模：**853 个条目页（product 714 / concept 71 / category 68）、2786 个章节、5.5MB**，仍全部由 `data/processed/` 单向编译而来。
  - **两处实现修正（第四轮实测后）**：
    1. **锚点必须等于渲染范围**：聚合概况节正文只有统计数字，却锚定了全部成员行（`4U 球拍` 191 行）→ `_strict` 分母被从未参与作答的行灌满，「100% 可回溯」也变虚。现在概况节不锚定、品牌节只锚**实际列出的** ≤12 行。
    2. **重嵌判据 = 将被写入的文本 + 元数据**（`indexer._doc_digest`），不再用整份条目 md 的 digest：否则 frontmatter 一行变化（如条目级指纹）就会重嵌全库 3639 段；`COMPILED_VERSION` 的作用回归到「只作废 `write_wiki` 的整库零写入快路径」。同时修掉该快路径**漏判「源未变但新增了条目」**的缺陷（正是它让 68 个聚合页第一次没落盘），两处都有回归用例。
  - **测量状态**：`data/eval/wiki_comparison_w4.json` 的数字（wiki strict 0.631 / classic 0.501）**因上面缺陷 1 失真，不作为 category 的效果结论**。条目页与 wiki 索引均已按 v2 重建完成（853 页 / 2786 章节；重嵌 1922、跳过 1717 —— 上一次被误设 900s `timeout` 中途杀掉的部分由增量判据自动跳过，`index_is_current()` 为真）。教训：**别用 `cmd | grep | tail` 的退出码判断长任务成败**（管道退出码来自 `tail`，会把 timeout 杀掉的失败伪装成成功）。
  - **下次开工**：直接重跑 `python -m scripts.eval_agent_quality --online --mode both --repeat 2 --json-out data/eval/wiki_comparison_w5.json`，再判定 category 是否净收益。
- **已知缺口（留给后续期）**：`最高磅数` 数值 → `拉线磅数` 低/中/高磅概念的映射未建（需从概念文本读区间，属推断，不做）；`羽毛球.毛片等级`（值为「大方/全圆」）与概念《毛片等级》（一级~五级）词表不一致，毛片维度暂无反向链接；**W4 剩余（LLM 撰写概念段落 + 句级忠实性校验 + 上传文档的 source 条目与增量重编译）与显式 `Filter`/`Search` 动作（当前靠 TOC 与 links 间接达成）仍未做**；概念页与聚合页并存时的「冲突取规格表」权威域规则尚未写进生成提示。
- **在线 A/B 结果（2026-08-28，`data/eval/wiki_comparison.json`，20 题 golden / repeat=2 / 同 judge 同库同 seed）**：

  | 指标 | classic | wiki | Δ |
  |---|---|---|---|
  | `context_precision_strict` | 0.494 | **0.726** | **+0.232** |
  | `context_precision` | 0.489 | 0.690 | +0.200 |
  | `token_efficiency` | 0.483 | 0.715 | +0.231 |
  | `faithfulness` | 0.778 | 0.871 | +0.093 |
  | `answer_relevancy` | 0.831 | 0.838 | +0.006 |
  | `context_recall` | 0.527 | 0.513 | −0.014 |
  | 平均 context 条数 | 11.95 | 8.95 | −3.00 |

  0 题降级 classic。**`_strict` 口径下 wiki 已超 plan 给 W3 定的 ≥0.60 目标**（且仅靠「取消每表配额 + 上下文换成章节」，未用导航循环）。
  - 分路由：`equipment` prec 0.503→0.925（strict 0.581→0.654）、`multi` prec 0.474→0.770（strict 0.497→0.834）—— 收益主要来自跨表拼盘噪声被消掉；
  - **`technique` 反而 prec 0.504→0.338**（strict 0.414→0.467、recall 0.550→0.635）：技术类概念页是 row 粒度，hybrid 补齐会把相近技术页填满 5 个条目、每条目再展开 2 节，条目相关但**章节**不相关 → 章节级稀释。`rules` 只有 1 题不作结论；
  - 逐题退化 3 处：`a12`（为什么有的羽毛球飞行不稳，recall 0.75→0.17，唯一实质退化：影响因素整表一页，命中的是「毛片对称性」节但答案需要多节）、`a09`（strict 0.60→0.50）、`a03`（strict 0.34→0.30 但 recall 0.5→1.0）。
  - **当时的检查点判断（plan §7 建议的停一停看数字）**：单轮数据显示精度收益已超前预期 → 倾向下调 W3 导航循环预算。**这一判断被下一轮复测推翻**，最终结论见「第二轮复测」段。
  - 绝对值与历史四指标表（precision 0.407 / recall 0.675）不可直接横比：本次 repeat=2、`data/chroma` 现含上传文档 collection，A/B 结论只取同一次运行内的差值。

- **第二轮复测（同日，read 改成「整页章节进候选 + 候选集内向量精排 + `max_contexts=8`」之后，repeat=2）**：

  | 指标 | classic | wiki | Δ |
  |---|---|---|---|
  | `context_precision_strict` | 0.449 | **0.714** | +0.266 |
  | `context_precision` | 0.445 | 0.687 | +0.241 |
  | `token_efficiency` | 0.444 | 0.710 | +0.266 |
  | `context_recall` | 0.655 | 0.493 | −0.162 |
  | `faithfulness` | 0.859 | 0.789 | −0.070 |
  | 平均 context 条数 | 12.40 | 7.85 | −4.55 |

  - **精度结论跨两轮稳定复现**：wiki `context_precision_strict` = 0.726 / 0.714（classic = 0.494 / 0.449）→ 「取消每表配额 + 上下文换成条目章节」带来的 precision 增益是实的，且**未使用导航循环**。
  - **但 repeat=2 下其他差异不能当结论**：classic 自己跨轮 recall 0.527→0.655、faithfulness 0.778→0.859，波动幅度大于本轮 wiki 与它的差值 → 判 read 的章节精排「是否净收益」证据不足。
  - 本轮可确认的局部效果：technique 路由 precision 0.338→0.562（章节稀释确实被修掉）；代价是上下文更少（8.95→7.85 条）后，多跳题掉召回 —— `a17`「正手高远球怎么打，打不远是什么原因」recall 0.94→0.25（答案需要同一条目的 `动作要领` + `常见错误` 两节，精排只留下了最相似的一节）。
  - **据此调整的判断**：wiki 当前的约束已从「噪声太多」变成「**召回不足**」，而这正是 `step`/`Follow` 动作（对已打开的条目补展开章节）能解、截断策略解不了的 → W3 的导航循环不该砍，但形态改为「**verify 不支撑或问题含多跳信号时，对已选条目追加章节展开**，`max_steps=1`」，并把 `max_contexts` 作为首要调参位（8 → 10~12）。

- **第三轮（W3 补展开循环上线后，`data/eval/wiki_comparison_w3.json`，同协议 repeat=2）**：

  | 指标 | classic | wiki | Δ |
  |---|---|---|---|
  | `context_precision_strict` | 0.443 | **0.759** | **+0.316** |
  | `context_precision` | 0.499 | 0.646 | +0.147 |
  | `token_efficiency` | 0.432 | 0.746 | +0.314 |
  | `context_recall` | 0.644 | 0.578 | −0.067 |
  | `faithfulness` | 0.779 | 0.737 | −0.042 |
  | `answer_relevancy` | 0.816 | 0.756 | −0.060 |
  | 平均 context 条数 | 12.55 | 9.95 | −2.60 |

  - **补展开循环确实生效**：上下文字节 7.85 → 9.95 条，wiki `context_recall` 0.493 → 0.578（与 classic 的差距从 −0.162 收窄到 −0.067），同时 strict 精度不降反升（0.714 → 0.759）。
  - **三轮横向**：wiki `context_precision_strict` = 0.726 / 0.714 / **0.759**，classic = 0.494 / 0.449 / 0.443 → 精度增益可复现；`technique` 路由本轮 prec 0.400→0.503、strict 0.400→0.519（第二轮的章节稀释已修），`multi` strict 0.408→0.839（收益最大）。
  - **兜底题数** classic 3 / wiki 4（`a02/a06/a11/a13`）。**唯一跨轮可复现的负向信号是 `a11`「推荐适合女生的进攻型球拍，配什么球线」两轮都在 wiki 侧兜底** —— 它需要「按适合水平+打法+性别筛选一批拍」+「再配一条线」，正是 category 聚合条目（W4）与该拆成子问题分别 orient 的缺口。
  - **口径纪律**：repeat=2 下 `faithfulness`/`answer_relevancy` 的差异（本表 −0.04/−0.06）小于 classic 自身的跨轮波动（faith 0.778 / 0.859 / 0.779），**不足以定结论**；且本轮 faith 下降主要由 4 个兜底答案被判「部分支撑」拉低，不是引用了错误信息。若要对外发布绝对值，需 repeat=3 以上并重跑。
