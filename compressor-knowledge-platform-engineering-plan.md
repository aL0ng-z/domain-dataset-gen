# 领域知识抽取与数据资产平台 — 工程开发计划

> Version: 2.0
> Date: 2026-04-04
> Status: Draft
> 基于: PRD.md V1.0

---

## 1. 总览

### 1.1 项目概述

本平台围绕 **PDF 文献 → 知识资产 → 数据集/Benchmark** 构建完整的生产闭环。首个落地场景为压气机设计领域。

### 1.2 核心链路

```
Upload PDF → Parse (MinerU) → Clean/Verify (Section) → Chunk → LLM Generate → Human Review → CuratedItem → Dataset/Benchmark Export
```

### 1.3 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Next.js 15 (App Router) + TypeScript + Tailwind CSS + shadcn/ui |
| 后端 | FastAPI + Pydantic v2 + SQLAlchemy 2.x + Alembic |
| 数据库 | PostgreSQL 16 |
| 缓存/任务 | Redis 7 + FastAPI BackgroundTasks (R1) → Celery (R2+) |
| 文件存储 | MinIO (S3-compatible) |
| 文档解析 | MinerU (主) → PaddleOCR (R3 兜底) |
| LLM | OpenAI-compatible gateway |
| 认证 | JWT (PyJWT) |
| 包管理 | uv workspace |
| 部署 | Docker Compose (单机) |

### 1.4 仓库结构

```
apps/web/           # Next.js 前端
apps/api/           # FastAPI 后端
workers/            # 异步任务执行器
libs/domain/        # DTO、Schema、领域对象
libs/parsing/       # MinerU / PaddleOCR 封装
libs/cleaning/      # Section 切分、Markdown 渲染、LLM 清洗
libs/splitters/     # Chunking 策略
libs/llm/           # LLM Provider 适配层
libs/storage/       # MinIO / S3 封装
infra/docker/       # Docker Compose 部署
infra/migrations/   # Alembic 迁移
```

### 1.5 Release 规划总览

| Release | 名称 | 周期 | 目标 |
|---------|------|------|------|
| R1 | Lab Pilot | ~13 周 (9 Sprint) | 主链路闭环 MVP，实验室内部可真实试用 |
| R2 | Lab Team | ~9 周 (4 Sprint) | 多人协作、知识分类、评测中心 |
| R3 | Quality Automation | ~9 周 (4 Sprint) | 质量自动化，不扩大边界 |
| R4 | Optional Extensions | 待定 | 多轮对话、GA 多样性、Arena 盲测 |

---

## 2. Release 1: Lab Pilot（~13 周）

### 2.1 R1 交付范围

| 模块 | 交付内容 |
|------|---------|
| 认证与项目 | JWT 登录、项目 CRUD、角色权限 |
| 项目控制中心 | ModelConfig / ParserProfile / ChunkProfile / ExportProfile / TaskPolicy CRUD + 设置页 |
| 文档接入 | PDF 上传、SHA256 去重、MinIO 持久化 |
| 文档解析 | MinerU 集成、异步解析、状态追踪 |
| 清洗工作台 | 四栏布局、Section 自动划分、编辑/提交/审核、租约、评论 |
| Chunking | hybrid_heading_recursive 默认策略 + ChunkProfile 绑定 |
| Prompt Template 中心 | knowledge_extraction + qa_generation + benchmark_case 模板，版本化 + 试跑 |
| Candidate 生成 | single_chunk 模式生成 + 证据审核 |
| CuratedItem | promote + 编辑 + evidence link |
| Dataset/Benchmark | 基础编组 + 导出 |
| 导出增强 | ExportProfile + 多格式 + SnapshotManifest |
| 任务中心 | 统一 Task 层 + 任务面板 + WebSocket 推送 |
| 资源监控 | LlmUsageLog 采集 + 概览页 |
| Docker Compose | 一键部署 |

**R1 不含**（后移到 R2）：Taxonomy 分类树、Playground、评测中心、AI 质量评分、section_context 模式

---

### 2.2 Sprint 0：基础设施与项目骨架（第 1~1.5 周）

**目标**：搭建可运行的开发环境，所有后续 Sprint 依赖此基座。

#### 交付物

**仓库初始化**
- `uv` workspace 初始化：根 `pyproject.toml` + 各 `libs/` 和 `apps/api` 子包
- Next.js 15 App Router 初始化：`apps/web/`，配置 TypeScript + Tailwind CSS + shadcn/ui
- ESLint / Ruff / pre-commit 配置
- `.gitignore`、`README.md`

**Docker Compose 基础版** (`infra/docker/`)
- PostgreSQL 16 + Redis 7 + MinIO 容器
- FastAPI dev 容器（hot reload）+ Next.js dev 容器
- MinIO 初始 bucket 创建脚本
- 环境变量模板 `.env.example`

**后端骨架** (`apps/api/`)
- FastAPI 应用入口 + CORS + 生命周期管理
- SQLAlchemy 2.x async engine + session factory
- Alembic 初始化 (`infra/migrations/`)
- Pydantic Settings（环境变量管理）
- 健康检查端点 `GET /api/health`

**公共库骨架**
- `libs/storage/`：MinIO client wrapper（upload / download / presigned URL）
- `libs/domain/`：基础 DTO 约定（BaseSchema, PaginatedResponse 等）
- `libs/llm/`：OpenAI-compatible client skeleton + LlmUsageLog 采集钩子

**前端骨架** (`apps/web/`)
- App Router layout + 基础导航壳
- API client 封装（fetch wrapper + token 注入）
- shadcn/ui 主题配置

#### 并行策略
后端骨架、前端骨架、Docker Compose 三线并行。

---

### 2.3 Sprint 1：认证 + 项目 + 数据库基础表（第 2~3 周）

**目标**：用户能登录、创建项目、看到项目列表。核心数据表就绪。

#### 交付物

**数据库迁移（第一批）**
- `users`（id, username, email, password_hash, role, created_at）
- `projects`（id, name, description, created_by, created_at）
- `project_members`（project_id, user_id, role）
- 配置型表：`model_configs`, `parser_profiles`, `chunk_profiles`, `export_profiles`, `task_policies`
- `tasks` 表（统一任务层）
- `llm_usage_logs` 表

**Auth 模块**
- `POST /api/auth/register`（admin 创建用户）
- `POST /api/auth/login` → JWT access + refresh token
- JWT 中间件（decode + inject current_user）
- 角色权限装饰器（admin / reviewer / editor / viewer）
- 密码哈希（bcrypt）

**Project CRUD**
- `POST/GET /api/projects` + `GET/PATCH /api/projects/{pid}`
- `POST/GET /api/projects/{pid}/members`
- 项目级权限校验中间件

**前端**
- `/login` 登录页
- `/projects` 项目列表页
- JWT 存储 + 自动刷新
- 项目创建对话框

#### 依赖
依赖 Sprint 0 全部基础设施。

---

### 2.4 Sprint 2：项目控制中心 + 文档接入 + Task 层（第 3~4.5 周）

**目标**：用户能配置项目参数，能上传 PDF 并去重存储。统一 Task 层就绪。

#### 交付物

**项目控制中心 API**（5 个 Profile CRUD）
- ModelConfig CRUD + `POST /api/model-configs/{id}/test`（连通性测试）
- ParserProfile / ChunkProfile / ExportProfile / TaskPolicy CRUD
- `POST /api/projects/{pid}/clone-config-from/{source_pid}`
- 所有配置表支持 `version` 自增、`is_default` 管理

**文档接入**
- `POST /api/projects/{pid}/documents/upload`：magic bytes 校验 + 大小限制 + SHA256 去重 + MinIO 上传
- `GET /api/projects/{pid}/documents` + `GET /api/documents/{did}`
- `libs/storage/` MinIO wrapper 完善

**统一 Task 层**
- Task CRUD service + 状态机（queued → processing → completed / failed / cancelled）
- `POST /api/tasks/{tid}/cancel` + `POST /api/tasks/{tid}/retry`

**前端**
- `/projects/[id]/settings`：Tab 式布局（模型 / 解析器 / 切分 / 导出 / 任务策略）
- `/projects/[id]/documents`：文档列表 + 拖拽上传 + 进度条

#### 并行策略
- 5 个 Profile API 结构高度相似，可多人并行
- 文档接入与控制中心独立
- 前端设置页与文档页可并行

---

### 2.5 Sprint 3：文档解析 + 任务中心 + WebSocket（第 4.5~6 周）

**目标**：PDF 能被 MinerU 异步解析，用户能在任务中心看到实时进度。

#### 交付物

**MinerU 集成** (`libs/parsing/`)
- MinerU Python wrapper：PDF → raw markdown + JSON 结构 + page mapping
- MinerU Docker 容器配置（GPU 可选）
- 错误处理 + 超时控制

**异步解析流程** (`workers/`)
- `POST /api/documents/{did}/parse`：创建 ParseJob + Task → BackgroundTask
- Worker：从 MinIO 下载 PDF → 调用 MinerU → 上传结果 → 更新状态
- Document 状态机：uploaded → parsing → parsed / parse_failed
- `GET /api/documents/{did}/parse-jobs` + `GET /api/parse-jobs/{jid}`

**数据库迁移**
- `parse_jobs` 表

**WebSocket 基础**
- FastAPI WebSocket endpoint：`/ws/projects/{pid}/tasks`
- Task 状态变更广播：`task.created`, `task.progress`, `task.completed`, `task.failed`
- Redis pub/sub 作为消息通道

**前端**
- 文档详情页"发起解析"按钮 + ParserProfile 选择
- `/projects/[id]/tasks`：任务历史列表
- 右下角浮窗徽标（进行中任务数）+ 展开面板
- WebSocket 连接 + 实时状态更新

#### 风险
MinerU 环境搭建可能遇到依赖问题（尤其 GPU 驱动）。**建议在 Sprint 0 就开始调研 MinerU Docker 镜像**。

---

### 2.6 Sprint 4：清洗工作台（第 6~7.5 周）

**目标**：解析完成后自动生成 Section，用户能在四栏工作台中编辑、提交、审核。

> 这是 R1 中 UI 最复杂的页面，也是平台最核心的差异化功能。

#### 交付物

**数据库迁移**
- `cleaning_jobs`, `sections`, `section_leases`, `section_comments`, `section_revisions`

**Section 自动划分** (`libs/cleaning/`)
- 从 ParseJob 产出的 raw markdown 按一级 heading 切分为 Section
- 生成 `heading_path`, `source_pages`, `ordinal`
- `POST /api/documents/{did}/cleaning/start`：创建 CleaningJob + 批量生成 Section

**Section API**
- `GET /api/documents/{did}/sections` + `GET/PATCH /api/sections/{sid}`
- `POST /api/sections/{sid}/submit`（提交审核）+ `POST /api/sections/{sid}/review`（accept/reject）
- Section 状态机：draft → in_cleaning → review_pending → accepted / rejected
- SectionRevision 自动记录

**Section 租约**
- `POST /api/sections/{sid}/lease/acquire` / `heartbeat` / `release`
- 租约过期自动释放（Redis TTL）
- reviewer 强制接管

**Section 评论**
- `POST/GET /api/sections/{sid}/comments`
- 评论类型：parse_issue / ocr_issue / layout_issue / general

**前端：四栏清洗工作台**
- `/projects/[id]/documents/[did]/clean`
- 左栏：PDF.js 渲染原始 PDF（页码跳转，section 对应页高亮）
- 中左栏：原始 Markdown 只读展示
- 中右栏：cleaned_markdown 可编辑（CodeMirror / Monaco）
- 右栏：Markdown 渲染预览 + 评论列表 + 审核操作
- Section 列表侧边栏 + 状态筛选
- 租约 UI：编辑锁定提示、心跳保活

#### 风险
四栏布局的响应式设计和 PDF.js 集成复杂度较高。**建议前端在 Sprint 3 就开始 PDF.js 技术预研**。

---

### 2.7 Sprint 5：Chunking + Prompt Template 中心 + LLM Client（第 7.5~9 周）

**目标**：accepted Section 能被切分为 Chunk，Prompt 模板管理就绪，为 LLM 生成做好准备。

#### 交付物

**数据库迁移**
- `chunks` 表
- `prompt_templates` 表（含扩展字段：task_type, version, input_schema, output_schema 等）

**Chunking 实现** (`libs/splitters/`)
- `hybrid_heading_recursive` 策略：基于 heading path 先划大块 → 块内按段落递归
- 每个 chunk 携带 `section_id`, `heading_path`, `source_pages`, `token_count`
- Token 计数器（tiktoken 或等效）

**Chunking API**
- `POST /api/documents/{did}/chunk`（含 chunk_profile_id，异步执行）
- `GET /api/documents/{did}/chunks`（分页 + 按 section / token range / status 筛选）
- `GET/PATCH /api/chunks/{cid}`
- Document 状态推进：cleaned → chunking → chunked

**Prompt Template 中心 API**
- `GET/POST /api/projects/{pid}/prompt-templates`（支持 `?task_type=` 筛选）
- `PATCH /api/prompt-templates/{id}`（自动 version++）
- `POST /api/prompt-templates/{id}/duplicate`
- `POST /api/prompt-templates/{id}/test-run`：选 chunk → 拼装 prompt → 调用 LLM → 返回结果
- `GET /api/prompt-templates/{id}/versions`
- 预置种子模板：`knowledge_extraction`, `qa_generation`, `benchmark_case`

**LLM Client 完善** (`libs/llm/`)
- OpenAI-compatible 调用封装（chat completion）
- LlmUsageLog 自动采集（input/output tokens, latency, status）
- 错误重试 + 超时处理
- 从 ModelConfig 构建 client

**前端**
- 文档详情页"执行切分"按钮 + ChunkProfile 选择
- `/projects/[id]/documents/[did]/chunks`：Chunk 列表（分页、筛选）
- `/projects/[id]/templates`：按 task_type 分组展示
- 模板编辑页：左侧 prompt 编辑 + 右侧试跑面板

#### 并行策略
- `libs/splitters/` 与 Prompt Template API 完全独立，可并行
- LLM Client 封装可与 API 层并行

---

### 2.8 Sprint 6：Candidate 生成 + 证据审核 + CuratedItem（第 9~10.5 周）

**目标**：打通从 Chunk 到 CuratedItem 的 **生成 → 审核 → 提升** 链路。这是知识生产的核心闭环。

#### 交付物

**数据库迁移**
- `generation_runs`, `candidates`
- `curated_items`, `curated_revisions`, `evidence_links`

**Candidate 生成**
- `POST /api/chunks/{cid}/generate`（template_id + model_config_id + context_mode: single_chunk）
  - 创建 GenerationRun + Task → 异步执行
  - Worker：拼装 prompt → 调用 LLM → 解析结构化输出 → 创建 Candidate
- `POST /api/documents/{did}/generate-batch`：批量生成（父 Task + 子 Task）
- Chunk 状态推进：ready → generating → generated

**Candidate 审核**
- `GET/PATCH /api/candidates/{cid}`
- `POST /api/candidates/{cid}/review`：证据判定（supported / partially_supported / unsupported / out_of_scope）+ 证据 span + 驳回原因
- `POST /api/candidates/{cid}/comments`
- Candidate 状态机：ai_generated → human_edited → review_pending → approved / rejected

**CuratedItem 管理**
- `POST /api/candidates/{cid}/promote-to-curated`：从 approved Candidate 创建 CuratedItem + EvidenceLink
- `GET /api/projects/{pid}/curated-items`（筛选：type, status, source_type）
- `GET/PATCH /api/curated-items/{id}` + `GET /api/curated-items/{id}/revisions`
- CuratedRevision 自动记录
- CuratedItem 状态机：draft → approved → exported → deprecated

**前端**
- `/projects/[id]/documents/[did]/chunks/[cid]`：Chunk 详情 + 生成面板（选模板、选模型、生成）
- Candidate 列表 + 审核面板（证据判定、span 输入、评论）
- `/projects/[id]/curated`：CuratedItem 列表 + 详情/编辑页
- 证据回溯展示：document → chunk → pages → heading_path → quote_text

---

### 2.9 Sprint 7：Dataset/Benchmark + 导出 + 资源监控（第 10.5~12 周）

**目标**：完成产出端闭环。CuratedItem 编组为 Dataset/Benchmark，多格式导出，SnapshotManifest 保证可复现。

#### 交付物

**数据库迁移**
- `datasets`, `dataset_items`, `benchmarks`, `benchmark_cases`
- `snapshot_manifests`, `exports`

**Dataset / Benchmark API**
- `POST /api/projects/{pid}/datasets` + `POST /api/projects/{pid}/benchmarks`
- `POST /api/curated-items/{id}/add-to-dataset` + `POST /api/curated-items/{id}/add-to-benchmark`
- `GET /api/datasets/{did}/items` + `GET /api/benchmarks/{bid}/cases`
- 硬性规则校验：未审核 CuratedItem 不可进入、Benchmark 仅允许 evidence_grounded

**导出引擎**
- `POST /api/datasets/{did}/export`（按 ExportProfile 格式化）
  - 支持格式：sft_jsonl / qa_json / messages / alpaca / sharegpt
  - 生成 SnapshotManifest（冻结全链路版本）
  - 输出文件上传 MinIO
- `POST /api/benchmarks/{bid}/export`：benchmark_json 格式
- `GET /api/projects/{pid}/exports` + `GET /api/exports/{eid}/manifest` + `GET /api/exports/{eid}/download`
- 导出预览：条数、类型分布、前 5 条样例

**资源监控**
- `GET /api/projects/{pid}/monitoring/summary`
- `GET /api/projects/{pid}/monitoring/by-task-type` / `by-model` / `daily-trend` / `by-template`
- 基于 `llm_usage_logs` 的聚合查询

**前端**
- `/projects/[id]/datasets`：Dataset 列表 + 创建 + CuratedItem 选择
- `/projects/[id]/datasets/[did]/export`：ExportProfile 选择 + 预览 + 导出 + 历史
- `/projects/[id]/benchmarks`：Benchmark 列表 + 管理
- `/projects/[id]/monitoring`：概览卡片 + 趋势图 + 饼图 + 对比表
- SnapshotManifest 查看页

---

### 2.10 Sprint 8：集成测试 + 部署 + 收尾（第 12~13 周）

**目标**：端到端验证、修复、Docker Compose 生产部署就绪。

#### 交付物

**端到端集成测试**
- 完整链路：上传真实 PDF → Parse → Clean → Chunk → Generate → Review → Curate → Export
- 验证 SnapshotManifest 完整性
- 验证 Task 状态推送正确性
- 验证权限控制（4 种角色各自操作边界）
- 验证去重逻辑（相同 PDF 重复上传）

**Docker Compose 生产配置**
- 多阶段构建 Dockerfile（FastAPI + Next.js + Worker）
- MinerU 服务容器
- Nginx/Caddy 反向代理
- 健康检查 + 启动顺序
- 数据卷挂载（PostgreSQL + MinIO 持久化）
- `docker compose up` 一键启动

**初始化脚本**
- 创建管理员账户
- 预置默认配置（各 Profile 默认值）
- 预置 Prompt Template 种子
- MinIO bucket 初始化

**文档**
- 部署文档
- 用户快速上手指南

---

### 2.11 R1 关键路径

```
Sprint 0 → Sprint 1 → Sprint 2 → Sprint 3 → Sprint 4 → Sprint 5 → Sprint 6 → Sprint 7 → Sprint 8
  基座       Auth      配置+上传   解析+Task   清洗工作台  Chunk+Prompt  生成+审核   导出+监控   集成部署
```

**关键路径**是主链路的串行依赖：**解析 (S3) → 清洗 (S4) → 切分 (S5) → 生成 (S6) → 导出 (S7)**。每个环节的输出是下一环节的输入。

**缓解方案**：在等待上游数据时，用 mock/fixture 数据先行开发下游 API 和 UI。

### 2.12 R1 集成测试里程碑

| 时间点 | 验证内容 |
|--------|---------|
| Sprint 2 结束 | Auth 全流程 + 项目配置 CRUD + PDF 上传到 MinIO |
| Sprint 3 结束 | PDF 上传 → MinerU 解析 → 产出 markdown（**首次端到端贯通**） |
| Sprint 4 结束 | 解析 → Section 自动划分 → 编辑提交审核（清洗链路贯通） |
| Sprint 5 结束 | Section → Chunk 切分 + Prompt 试跑调通 LLM 调用 |
| Sprint 6 结束 | Chunk → Generate → Review → CuratedItem（**知识生产闭环贯通**） |
| Sprint 7 结束 | CuratedItem → Dataset → Export + SnapshotManifest（**完整链路贯通**） |
| Sprint 8 结束 | Docker Compose 一键部署 + 真实 PDF 端到端回归 |

### 2.13 R1 并行开发建议（2~3 人团队）

| Sprint | 开发者 A（后端为主） | 开发者 B（前端为主） | 开发者 C（基础库 + DevOps） |
|--------|---------------------|---------------------|---------------------------|
| S0 | FastAPI 骨架 + Alembic | Next.js 骨架 + shadcn | Docker Compose + libs/ |
| S1 | Auth API + Project API | 登录页 + 项目列表 | DB 迁移 + domain DTOs |
| S2 | 控制中心 5 个 Profile API | 设置页 Tab UI + 文档上传 | Storage wrapper + 文档 API |
| S3 | 解析 Worker + ParseJob API | 任务面板 + WebSocket 前端 | libs/parsing/ MinerU wrapper |
| S4 | Section API + 租约 + 评论 | 四栏清洗工作台 UI | libs/cleaning/ heading 切分 |
| S5 | Chunking API + Template API | Chunk 列表 + Template 编辑页 | libs/splitters/ + libs/llm/ |
| S6 | 生成 Worker + Candidate API | 生成面板 + 审核 UI | CuratedItem API + Evidence |
| S7 | 导出引擎 + Snapshot | 导出页 + 监控页 | Dataset/Benchmark API |
| S8 | 集成测试 + 部署配置 | UI 打磨 + 交互修复 | 初始化脚本 + 文档 |

---

## 3. Release 2: Lab Team（~9 周）

### 3.1 R2 交付范围

| 模块 | 交付内容 |
|------|---------|
| Taxonomy 知识分类 | TaxonomyNode CRUD + LLM 建议生成 + CuratedItem 关联 + 覆盖度分析 |
| Section/Chunk 租约增强 | 前端锁定 UI + WebSocket 通知 |
| 评论与 Revision | Section 评论 + CuratedItem 评论 + revision 历史 |
| WebSocket 完善 | section.lease.changed / candidate.updated 等全事件覆盖 |
| 评测中心 | EvalRun / EvalResult + LLM Judge + 评测页面 |
| 模型 Playground | 多模型对比 API + Playground 页面 |
| AI 质量评分 | quality_evaluation 模板 + 批量评分 |
| section_context 模式 | 相邻 chunk + heading path 拼接 |
| 导出增强 | 平衡策略 + train/test split + 下游框架配置 + 导出预览 |

**新增数据库表**：`taxonomy_nodes`, `curated_item_taxonomy`, `eval_runs`, `eval_results`

### 3.2 R2 模块依赖分析

```
                   ┌─ Taxonomy ──────────────────────────┐
                   │                                      ↓
独立模块 ──────────┤                              导出增强（按分类平衡）
                   │
                   ├─ 评测中心（独立）
                   ├─ Playground + AI 质量评分（独立）
                   ├─ section_context 模式（独立）
                   │
紧耦合三件套 ──────┤─ 租约增强 + WebSocket 完善 + 评论/Revision
```

### 3.3 Sprint R2-1：数据库迁移 + Celery + 协作后端 + 独立模块后端（第 1~2 周）

**目标**：完成 R2 全部新表迁移，交付协作核心后端，启动独立模块后端。

| 任务 | 说明 |
|------|------|
| Alembic 迁移 | 4 张新表 + `candidates` 新增 `quality_score_json` / `quality_evaluated_at` |
| BackgroundTasks → Celery 迁移 | R2 批量任务依赖可靠异步层，**必须前置** |
| Section/Chunk 租约 API | acquire / heartbeat / release 业务逻辑实现 |
| WebSocket 事件扩展 | `section.lease.changed`, `section.updated`, `candidate.updated` 等 |
| section_context 模式后端 | 修改 generation 逻辑：查询相邻 chunk + heading_path 拼接 |
| Taxonomy CRUD API | TaxonomyNode `POST/GET/PATCH/DEL` |

**并行度**：4 条线完全并行（迁移先行，其余紧跟）。

### 3.4 Sprint R2-2：协作前端 + 评测/Playground/质量评分后端（第 3~4 周）

| 任务 | 说明 |
|------|------|
| 租约前端锁定 UI | 清洗工作台显示锁定状态，编辑器根据租约启用/禁用 |
| 评论组件（Section + CuratedItem） | 通用 Comment 组件，绑定不同实体 |
| Revision 历史展示 | CuratedItem 详情页 revision diff 视图 |
| WebSocket 前端集成 | 租约变更、candidate 更新实时刷新 |
| 评测中心后端 | EvalRun CRUD + LLM Judge 批量执行 + EvalResult 写入 |
| Playground 后端 | `/api/playground/compare`：并行调用 2-3 个 ModelConfig |
| AI 质量评分后端 | 单条 + 批量评分 API |
| Taxonomy LLM 建议生成 | `tag_suggestion` 模板 + CuratedItem 关联 API |

### 3.5 Sprint R2-3：新增页面 + 导出增强（第 5~7 周）

| 任务 | 说明 |
|------|------|
| `/projects/[id]/taxonomy` 页面 | 左侧树形编辑器 + 右侧关联 CuratedItem 列表 + 覆盖度提示 |
| `/projects/[id]/benchmarks/[bid]/eval` 页面 | 发起评测 + 汇总卡片 + 逐题结果 + 多 run 对比 |
| `/projects/[id]/playground` 页面 | chunk 选择 + 模板选择 + 模型多选 + 并排结果 |
| 导出增强后端 | `balance_strategy`（按 taxonomy 均衡采样）、`train_test_split`、LLaMA Factory 配置生成 |
| 导出增强前端 | ExportProfile 选择器、预览面板、下游配置下载 |
| section_context 前端 | 生成面板中增加 context_mode 切换 |

### 3.6 Sprint R2-4：集成测试 + 修复 + 发布（第 8~9 周）

| 测试场景 | 说明 |
|---------|------|
| 协作流程 | 2 人同时操作 → 租约互斥 → WebSocket 通知 → 评论 → revision 记录 |
| 分类 + 导出 | 创建 taxonomy → LLM 建议 → 关联 → 按分类平衡导出 → 验证 Snapshot |
| 评测闭环 | 编组 Benchmark → 选模型 + Judge → EvalRun → 自动评分 → 人工复核 → 多 run 对比 |
| Playground + 质量评分 | 多模型对比 → 批量质量评分 → 按分数排序 |
| section_context 生成 | 切换 context_mode → 验证拼接逻辑 → 验证 context_chunk_ids 记录 |

---

## 4. Release 3: Quality Automation（~9 周）

### 4.1 R3 交付范围

| 模块 | 交付内容 |
|------|---------|
| Parser Compare | 同文档多 parser 对比视图 |
| LLM 清洗建议 | clean_suggestion 模板 + Section 内联建议 |
| Claim Check | 逐声明事实验证 |
| Comment-Driven Revision | 基于评论的 LLM 自动修订 |
| Semantic Dedup | 嵌入相似度检测 + 合并 |
| Taxonomy 增强 | 基于 CuratedItem 反向优化分类建议 |
| 多切分策略 | fixed_length / recursive_separator / 手工边界覆盖 |
| PaddleOCR/Vision 解析 | 兜底解析器，支持扫描件 |

### 4.2 R3 模块依赖分析

```
独立模块 ──── Parser Compare + PaddleOCR（解析层）
              多切分策略（独立）
              Claim Check + Semantic Dedup（质量验证）
              Taxonomy 增强（独立）

有先后 ────── LLM 清洗建议 → Comment-Driven Revision（共享 inline suggestion UI）
```

### 4.3 Sprint R3-1：解析层 + 清洗建议 + 多切分（第 1~2 周）

| 任务 | 说明 |
|------|------|
| PaddleOCR/PP-StructureV3 集成 | `libs/parsing/` 新增 wrapper，`parser_name=paddleocr` |
| PaddleOCR Docker 容器 | `infra/docker/` 新增服务 |
| Parser Compare 后端 | 同一 document 多个 ParseJob 的 section 级 diff API |
| `clean_suggestion` 模板 + API | Section → LLM 建议 → inline suggestions JSON |
| 多切分策略后端 | `fixed_length` / `recursive_separator` + 手工边界覆盖 |
| Semantic Dedup 基础 | Embedding 计算 + 向量存储（pgvector 或内存） |

### 4.4 Sprint R3-2：清洗建议前端 + Claim Check + Dedup（第 3~4 周）

| 任务 | 说明 |
|------|------|
| 清洗工作台 inline suggestion UI | 编辑器中 LLM 建议标记，accept/reject 单条 |
| Parser Compare 前端 | 多 parse 结果并排对比视图 |
| `claim_check` 模板 + 后端 | 内容拆解为 claims → 逐条验证 → verified/unverified/uncertain |
| Semantic Dedup 后端 | 余弦相似度 → 疑似重复标记 → 合并 API |
| 多切分策略前端 | ChunkProfile 编辑器扩展 + 手工边界调整交互 |
| `comment_revision` 模板 + 后端 | 读取评论 → LLM 生成修订建议 → 返回 diff |

### 4.5 Sprint R3-3：剩余前端闭合（第 5~7 周）

| 任务 | 说明 |
|------|------|
| Comment-Driven Revision 前端 | 评论面板"LLM 修订"按钮 → 预览 diff → accept/reject → 写入 revision |
| Semantic Dedup 前端 | CuratedItem 列表中重复标记 + 合并确认 |
| Claim Check 前端 | Candidate/CuratedItem 详情页 claim 列表 + 验证状态 |
| Taxonomy 增强后端 + 前端 | 基于 CuratedItem 反向生成分类建议 |
| PaddleOCR 前端集成 | ParserProfile 编辑器增加 PaddleOCR 选项 |

### 4.6 Sprint R3-4：集成测试 + 修复 + 发布（第 8~9 周）

| 测试场景 | 说明 |
|---------|------|
| 解析层 | 同一扫描件 → MinerU + PaddleOCR → Parser Compare 对比 → 选优继续 |
| 清洗建议 + 评论修订 | 触发 LLM 建议 → accept 部分 → 评论 → comment-driven revision → revision 记录完整 |
| Claim Check | 生成 Candidate → claim check → 逐条验证 → reviewer 参考验证结果 |
| Semantic Dedup | 批量 CuratedItem → dedup 检测 → 合并 → 验证关联更新 |
| 多切分策略 | 3 种策略对比 → 手工调整 → 验证 section_id 追溯 |

---

## 5. 技术风险与应对

| 风险 | 影响 Sprint | 应对措施 |
|------|------------|---------|
| MinerU 环境/GPU 依赖复杂 | R1-S3 | Sprint 0 提前调研 Docker 镜像；准备无 GPU fallback 模式 |
| PDF.js 四栏布局复杂度 | R1-S4 | Sprint 3 前端做 PDF.js 技术 spike；考虑先用简化两栏再迭代 |
| LLM 结构化输出不稳定 | R1-S6 | libs/llm/ 加入输出解析 + 重试 + fallback；Pydantic 校验 |
| WebSocket 连接管理 | R1-S3~S7 | Redis pub/sub 解耦；前端重连机制 |
| 大文档（500+ 页）性能 | R1-S3~S5 | 流式处理；分页加载；异步任务避免阻塞 |
| R2 Celery 迁移风险 | R2-S1 | R1 就预留 Celery 兼容接口；R2 第一周完成迁移 |
| R3 Embedding 基础设施选型 | R3-S1 | CuratedItem < 10000 用内存计算；否则部署 pgvector |

---

## 6. 关键决策记录

1. **R2 Celery 迁移必须前置**：R2 评测批量任务和 AI 质量评分依赖可靠异步层。
2. **R2 协作三件套紧耦合**：租约 + WebSocket + 评论前端交付必须协同，建议同一前端开发者负责。
3. **R2 导出增强依赖 Taxonomy**：按分类平衡导出的前提是分类树已建立。
4. **R3 PaddleOCR 是独立模块**：与其他 R3 模块无依赖，资源紧张可延后。
5. **R3 Comment-Driven Revision 复用清洗建议 UI**：先做清洗建议，再复用 UI 组件做评论修订。
6. **Candidate vs CuratedItem 严格分离**：LLM 输出是候选，正式知识资产必须经人工提升。
7. **SnapshotManifest 是导出的硬性前提**：没有完整 manifest 的导出不算正式导出。
