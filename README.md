# badminton-rag · 羽毛球知识库问答系统

> 基于 **Agentic RAG** 架构的羽毛球领域知识问答系统：LangGraph 路由/工具/校验编排 + 混合检索（向量 + BM25）+ 重排序 + 四指标评测闭环 + Langfuse 全链路可观测 + Docker Compose 一键部署。

[![测试](https://img.shields.io/badge/tests-437%20passed-green)](tests/) · [![阶段](https://img.shields.io/badge/phase-0~10%20完成-brightgreen)](badminton-rag-roadmap.md)

---

## 架构图

```mermaid
flowchart LR
    U[用户 / 浏览器] --> W["web（nginx：前端 SPA + 反代）"]
    W --> A["api（FastAPI）"]
    A --> AG["Agent 编排（LangGraph）"]
    AG --> RT["route → tools → generate → verify → retry"]
    RT --> R["检索链路：向量 embedding + BM25 混合 + Reranker 精排"]
    R --> C[(Chroma 向量库)]
    A --> O["百炼 · qwen3.7-text-embedding"]
    A --> D["百炼 · qwen3.8-flash LLM"]
    A --> LF[Langfuse 可观测 trace]
    A --> DB[(MySQL：审计 / 反馈 / 文档)]
    INIT["init 容器：幂等入库 17 张表"] --> C
```

**一键部署**：`docker compose up -d --build` → 新环境 **5 分钟内可提问**（浏览器打开 `http://localhost:8080`）。

---

## 功能亮点（Phase 0~10）

| 阶段 | 能力 | 验收数据 |
|---|---|---|
| P0~1 | 文档接入 + 混合检索（向量+BM25）+ 重排序 + 引用溯源 | 检索候选池覆盖 17 张表 |
| P2 | 企业级底座：审计日志 / 限流 / 管理后台 / 统一错误码 | API 层全量测试 |
| P3 | **Agentic RAG**：五类路由 + 工具调用 + 多轮记忆 + 回答校验 | 20 题多跳通过率 **90%**（在线 repeat=3） |
| P4 | **评测闭环 + 可观测**：LLM-as-judge 四指标 / Langfuse trace / FAQ 缓存 / 成本统计 / bad case 反哺 | 221 测试全绿 |
| P5 | **部署与展示**：Docker Compose 一键起 + 精致聊天 UI + README 简历化 | 新环境 5 分钟可提问 |
| P6 | **文档/图片入库**：PDF（PyMuPDF）+ 图片（RapidOCR / SiliconFlow 多模态索引）+ document 路由 + 管理端上传/文档管理 | 254 测试全绿 |
| P7 | **双角色账户与 RBAC**：用户/管理员登录、模块级权限、种子管理员 | 严格管理员端点测试 |
| P8 | **用户端功能**：范围限定/语音/历史/收藏/目录/动态/纠错/通知 | 用户端 API 10 例 |
| P9 | **双数据库后端**：自动建库 + DDL 方言转换（当前默认后端为 MySQL，详见「数据库」小节） | MySQL 真实链路验证 |
| P10 | **管理端后台**：总览 Dashboard/健康探活、知识库打标、RAG 调优（沙箱/参数/Prompt/词典）、审核工单、模块权限导航、系统只读 | **326 测试全绿** |

---

## 快速开始

### 方式一：Docker Compose 一键起（推荐演示）

```bash
# 1. 准备 .env（复制模板并填密钥：百炼 LLM_API_KEY 必填）
cp .env.example .env

# 2. 一键启动（首次自动：校验 key → 重建向量库 → 起 API + Web）
docker compose up -d --build

# 3. 打开浏览器
#    http://localhost:8080   聊天 UI
#    http://localhost:8000/docs  OpenAPI 文档
```

- 首次启动 5 分钟内可提问（含入库，829 条知识：5 张规格表 714 行 + 12 张知识表 115 行）；
- 停止 `docker compose down`（数据卷保留）；可选自托管 Langfuse：`docker compose --profile selfhost up -d`。

### 方式二：本地开发

```bash
# 后端（入库与问答都调百炼 API，需 .env 配置 LLM_API_KEY）
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m app.ingest.pipeline      # 重建向量库
.venv/Scripts/python.exe -m uvicorn main:app --reload

# 前端（vite dev，proxy 转发到 8000）
cd web && npm install && npm run dev                 # http://localhost:5173
```

---

## 评测报告（Phase 4）

### 1. Agent 通过率（20 题多跳，在线 repeat=3）

**18 / 20 = 90%**（2026-08-29 重跑，百炼 `qwen3.8-flash` + `qwen3.7-text-embedding`，在线 repeat=3）。

失败集中在 a14/a15 两道 **multi 拆解题**：同一题 3 次里有 2 次 generate 节点在合并后的上下文上判「依据不足」整答兜底，剩下 1 次答案完整正确（a14 还叠加路由抖动：走 `equipment` 那次通过、走 `multi` 两次兜底）。已列入 bad case 改进项；此前记录的「唯一稳定失败 a06」本轮 2/3 通过，不再成立。

### 2. 四指标在线基线（LLM-as-judge，上下文 = agent 真实喂给生成节点的 `state["contexts"]`）

| 指标 | 平均 | 说明 |
|---|---|---|
| faithfulness | **0.812** | 回答主张能被上下文支撑的比例 |
| answer_relevancy | **0.873** | 回答切题、信息充分度 |
| context_precision | **0.407** | 检索条目中相关条目占比（**主要短板**） |
| context_recall | **0.675** | 标准答案信息点被上下文覆盖的比例 |

**按 route 分组**：

| route | faith | relev | prec | recall |
|---|---|---|---|---|
| rules | 1.000 | 1.000 | 0.375 | 1.000 |
| technique | 0.963 | 0.917 | 0.467 | 0.600 |
| equipment | 0.944 | 0.917 | **0.574** | 0.611 |
| multi | 0.733 | 0.842 | **0.356** | 0.683 |

> 关键结论：**equipment 路由的 context_precision 最高（0.574）**，证明「路由 + 定向检索」相比全表检索更精准；multi 路由拆解后上下文最嘈杂（0.356），是下一步优化重点（子问题分组截断 + retry 后重排）。

### 3. 可观测（Langfuse v4）

每次 `/chat` 请求一条完整 trace：`route → 工具 → generate → verify`，每个节点记录**耗时 + token**，根 observation 承载整体输入/输出（`POST /chat` 响应带 `trace_id` 与 `langfuse_url`，前端一键跳转）。

### 4. Bad Case 复盘

`data/eval/bad_cases.md`：20+ 条失败案例，按 **router / retrieval / faithfulness / relevancy / data / feedback** 六类标注根因与改进方向；`POST /feedback` 点踩记录自动并入闭环。

---

## 目录结构

```
badminton-rag/
├── app/
│   ├── api/routes/       # FastAPI 路由（/chat /ask /feedback /kb/overview /admin* /audit）
│   ├── agent/            # LangGraph 编排（路由/工具/记忆/校验）
│   ├── rag/              # 混合检索 + 重排 + 查询改写 + 调试回放
│   ├── ingest/           # 入库流水线（CSV + PDF/图片/文本文档）
│   ├── observability/    # Tracer（Langfuse v4 / Null）/ FAQ 缓存 / token 统计
│   └── core/             # 配置 / 日志 / 限流
├── web/                  # 前端（Vite + React + TS：登录 / 用户端 4 Tab / 管理端 6 模块）
├── scripts/              # 评测 / bad case / Langfuse 验证脚本
├── data/                 # raw / processed / raw_docs / chroma / eval / wiki / uploads
├── tests/                # 437 个测试（全部离线，不触网）
├── Dockerfile            # API 镜像（python:3.12-slim）
├── docker-compose.yml    # mysql + init + api + web（自托管 Langfuse 仍在 profile 下）
├── deploy/nginx.conf     # web 反代配置（含 client_max_body_size）
└── .env.example          # 环境变量模板
```

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/register` | 注册（默认角色 user），返回 token + 用户信息 |
| POST | `/auth/login` | 登录（用户/管理员统一入口），Bearer token |
| GET/PATCH | `/auth/me` `/auth/profile` | 当前用户 / 更新资料与偏好（头像/昵称/性别/水平/球拍/语气/引用） |
| POST | `/chat` | Agentic 对话（路由/工具/记忆/校验），支持 `scope` 范围限定；登录自动存会话 |
| POST | `/ask` | 朴素 RAG 问答（登录可选） |
| POST | `/feedback` | 点赞/点踩（进 bad case 闭环；登录用户自动关联 user_id） |
| GET | `/kb/overview` | 知识库统计（公开只读）；`/kb/catalog` 已废弃（页面不再展示） |
| GET/POST/PATCH/DELETE | `/user/conversations` | 历史对话记录（搜索/标签/收藏/重命名/删除/详情回放） |
| GET/POST/PATCH/DELETE | `/user/favorites` `/user/folders` | 收藏夹与文件夹（移动分类） |
| GET/POST | `/user/posts` | 球友动态（文本+图片，带已赞状态/回复数）与发布 |
| POST | `/user/posts/{id}/like` | 点赞动态（再点取消；每用户每条限 1 次） |
| GET/POST | `/user/posts/{id}/replies` | 动态回复列表（楼中楼）/ 回复动态（可回复他人的回复） |
| POST | `/user/replies/{id}/like` | 点赞回复（再点取消；每用户每条限 1 次） |
| GET | `/user/hot` | 热门问答排行（赞 + 收藏聚合） |
| POST/GET | `/user/corrections` | 内容纠错提交 / 我的纠错 |
| GET/POST | `/user/notifications` `/user/notifications/read` | 消息通知列表 / 全部或部分已读 |
| POST | `/user/uploads` | 动态配图上传（≤2MB，返回 `/uploads/xxx`） |
| GET | `/chat/stats` | 成本报表（管理员 JWT，兼容旧 X-Admin-Key） |
| GET/POST/DELETE | `/admin/documents` | 文档上传（txt/md/csv/pdf/图片）/列表/删除/重索引（管理员 JWT，兼容旧 X-Admin-Key） |
| GET/PATCH | `/admin/users` | 用户与权限管理：列表 / 改角色 / 禁用 / 模块权限（**严格管理员 JWT**，旧 key 不可用） |
| GET | `/admin/dashboard` | 知识库总览：文档/向量/消息/用户统计、待办提醒、成本报表（`require_admin_module("dashboard")`） |
| GET | `/admin/health` | 组件健康探活：DB / Chroma / 百炼（LLM+embedding） / SiliconFlow |
| GET/PUT | `/admin/rag/settings` | RAG 运行时参数：`vector_top_k` / `filter_top_k` / `rerank_enabled` / `blacklist_enabled` |
| GET/POST/PUT/DELETE | `/admin/rag/prompts` | Prompt 模板 CRUD + `POST .../{id}/activate` 激活（变更即重建 Agent） |
| GET/POST/DELETE | `/admin/rag/synonyms` `/admin/rag/blacklist` | 同义词组 / 敏感词词典（变更即重建 Agent） |
| POST | `/admin/rag/debug` | **RAG 沙箱**：链路回放（路由→扩展查询→候选块→过滤→上下文→回答），`with_answer=false` 免 LLM |
| GET/PATCH | `/admin/corrections` | 纠错工单（状态筛选）；采纳即通知提交者（rejected/discussion 仅落状态） |
| GET | `/admin/qc/bad` | 低质量问答聚合（点踩按问题统计） |
| GET | `/admin/system` | 系统配置只读（模型/限流/上传/库，密钥掩码） |
| PATCH | `/admin/documents/{id}/tags` | 文档打标（DB + Chroma 元数据同步，不重嵌） |
| GET | `/audit` | 审计日志（管理员 JWT，兼容旧 X-Admin-Key） |
| GET | `/health` | 存活探针 |

## 配置（.env）

| 变量 | 说明 |
|---|---|
| `DB_BACKEND` | 数据库后端：`mysql`（默认）或 `sqlite`（仅离线测试，由 `tests/conftest.py` 强制，无需手工配） |
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DB` | MySQL 连接配置（默认后端所需；库不存在时启动自动创建） |
| `LLM_API_KEY` | 百炼 DashScope key：生成 LLM 与文本 embedding 共用（必填） |
| `LLM_BASE_URL` / `LLM_MODEL` | 生成端点与模型（默认百炼 compatible-mode / `qwen3.8-flash`） |
| `EMBEDDING_MODEL` | 文本 embedding 模型（默认 `qwen3.7-text-embedding`）；改这一项必须全量重索引 |
| `RERANK_API_KEY` / `ASK_USE_RERANK` | 可选精排（SiliconFlow bge-reranker） |
| `LANGFUSE_ENABLED` / `LANGFUSE_*` | 可观测开关（默认 false，不依赖） |
| `ADMIN_API_KEY` | 旧版管理共享密钥（向后兼容，可留空） |
| `AUTH_TOKEN_SECRET` | 会话令牌签名密钥（生产必改） |
| `AUTH_TOKEN_TTL` | 令牌有效期秒数（默认 604800 = 7 天） |
| `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` | 启动时自动创建的管理员种子账号（可留空） |

### 数据库：MySQL（默认）与 SQLite（仅测试）

- **MySQL（默认）**：`.env` 配 `MYSQL_*` 即可；业务表自动**建库**（库不存在时 `CREATE DATABASE IF NOT EXISTS`，账号需有 CREATE 权限）+ 建表/迁移（utf8mb4，DDL 方言差异由 `app/db/database.py` 透明转换）。本机 MySQL 直接跑：
  ```bash
  # .env：DB_BACKEND=mysql + MYSQL_HOST/USER/PASSWORD/DB
  .venv/Scripts/python.exe -m uvicorn main:app --port 8000
  ```
- **SQLite（仅离线测试）**：`tests/conftest.py` 顶层强制 `DB_BACKEND=sqlite` 并把 `db_path` 指向每个测试自己的临时文件，因此 `pytest` 既不触网也绝不写入真实业务库——本机 `.env` 开着 MySQL 也不影响。日常开发不需要手工配 sqlite。
- **Docker Compose**：`mysql:8.0` 已是默认服务，随 `docker compose up -d --build` 一起起（数据卷 `mysql-data`），不再需要 `--profile mysql`。
  ```bash
  docker compose up -d --build     # mysql + init 建表入库 + api + web
  ```
  注意：MySQL 与 SQLite 两套数据不互通，切换后端后用户/会话等业务数据从空库开始；向量库（Chroma）与业务库相互独立，不受切换影响。仓库里原先的 `data/app.db`（SQLite 旧副本）已退役移出项目，业务数据以 MySQL 的 `badminton` 库为准。

## 测试

```bash
.venv/Scripts/python.exe -m pytest -q    # 437 passed，全部离线（不触网）
```

## 文档/图片入库（Phase 6）

```bash
# CLI 批量入库（data/raw_docs 下放 PDF/图片/txt/md/csv，按文件 hash 幂等）
.venv/Scripts/python.exe -m app.ingest.pipeline --dir data/raw_docs
# 或只入文档（跳过 17 张 CSV）
.venv/Scripts/python.exe -m app.ingest.pipeline --only-docs
```

- **PDF**：PyMuPDF 解析文字层 + 表格（`find_tables` 转「表头:值」），chunk 带页码；电子版 PDF 入库后 `/chat` 问文档内容会路由到 `document` 并引用文件名/页码。
- **图片**：RapidOCR 提取文字（CPU 离线）入库，可被文字检索命中；**无文字插画走多模态图片索引**（SiliconFlow `Qwen/Qwen3-VL-Embedding-8B`，图片+文本同空间 4096 维，按图检索），需在 `.env` 开 `VISION_EMBED_ENABLED=true`（key 缺省复用 `RERANK_API_KEY`）。
- **管理端**：`#/admin` 用管理员账户登录（种子账号见 `BOOTSTRAP_ADMIN_*`，或后台分配），左侧导航 6 模块：📊 知识库总览（指标卡/健康探活/成本报表）、📚 知识库管理（上传/列表/打标）、🧪 检索调优（RAG 沙箱/运行时参数/Prompt 模板/同义词与敏感词）、⚖️ 内容审核（纠错工单/低质量聚合 + Bad Case 复盘）、👥 用户与权限（角色/启停/模块权限/审计日志）、⚙️ 系统设置（只读）。模块权限：`users.permissions` 为 NULL=全部、空数组=无、列表=仅所列模块（用户与权限对所有管理员可见）。旧 `ADMIN_API_KEY` 仍兼容文档/审计端点。

## 演示视频

📹 30~60s 演示视频：*占位（录屏：compose up → 提问 → 引用溯源 → 缓存秒回 → Langfuse trace → 管理页报表）*

---

## License

MIT（仅学习展示；语料不随仓库分发）
