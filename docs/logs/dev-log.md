# Development Log

> 本文件记录项目开发进度，每个阶段完成后更新。

---

## 项目总览

| Release | 名称 | 状态 | 备注 |
|---------|------|------|------|
| R1 | Lab Pilot | 测试进行中 | 主链路 MVP，已修复 34 个 issue |
| R1+ | Slice 1: Clean 协作升级 | 后端冒烟通过，前端 UI 待目检 | 数据模型扩展 + Clean 分派/合并/终审工作流 |
| R2 | Lab Team | 未开始 | 多人协作、评测中心 |
| R3 | Quality Automation | 未开始 | 质量自动化 |
| R4 | Optional Extensions | 未开始 | 多轮对话、Arena 等 |

---

## R1: Lab Pilot

### 初版开发完成，进入测试修复 (2026-04-05 ~ 至今)

**27 次提交 | 191 个文件 | 28 张数据库表 | 133 条 FastAPI 路由 | 21 个前端页面**

#### 后端 (apps/api/)

| 模块 | 内容 | 状态 |
|------|------|------|
| 数据库 | 14 个模型文件 / 28 张表 / Alembic 迁移 | 已完成 |
| 认证 | JWT 登录 + 4 角色权限 (admin/reviewer/editor/viewer) | 已完成 |
| 项目管理 | 项目 CRUD + 成员管理 + 配置克隆 | 已完成 |
| 配置中心 | 5 类 Profile (ModelConfig/Parser/Chunk/Export/TaskPolicy) | 已完成 |
| 文档接入 | PDF 上传 + SHA256 记录 + 允许重复上传 + MinIO 存储 | 已完成 |
| 文档解析 | `pymupdf4llm` 默认解析 + MinerU / PaddleOCR API 解析器 | 已完成 |
| 清洗 | Section 自动划分 + 编辑/提交/审核 + 租约 + 评论 | 已完成 |
| 切分 | hybrid_heading_recursive 策略 + tiktoken 计数 | 已完成 |
| Prompt 模板 | CRUD + 版本历史 + 复制 + 试跑 | 已完成 |
| Candidate 生成 | single_chunk 模式 + 批量生成 + LLM 调用 | 已完成 |
| 证据审核 | 审核判定 (supported/partially/unsupported/out_of_scope) | 已完成 |
| CuratedItem | promote + 编辑 + 版本记录 + 证据链接 | 已完成 |
| Dataset/Benchmark | 编组 + 6 种导出格式 + SnapshotManifest | 已完成 |
| 任务中心 | 统一 Task 层 + WebSocket 实时推送 | 已完成 |
| 资源监控 | LLM 用量统计 (按模型/模板/任务类型/日趋势) | 已完成 |
| Pydantic Schema | 15 个 Schema 文件 | 已完成 |

#### 公共库 (libs/)

| 库 | 功能 | 状态 |
|----|------|------|
| domain | 15 枚举 + BaseSchema + PaginatedResponse | 已完成 |
| storage | MinIO S3 封装 | 已完成 |
| parsing | BaseParser + `pymupdf4llm` 默认解析器 + MinerU / PaddleOCR API 解析器 | 已完成 |
| cleaning | split_into_sections (H1→H2→全文 fallback) | 已完成 |
| splitters | HybridHeadingRecursiveChunker + tiktoken | 已完成 |
| llm | OpenAI 兼容客户端 + 3x 重试 + 用量回调 | 已完成 |

#### 前端 (apps/web/)

| 模块 | 内容 | 状态 |
|------|------|------|
| 框架 | Next.js 16 + TypeScript + Tailwind + shadcn/ui (15 组件) | 已完成 |
| 核心 | API Client (JWT 自动刷新) + WebSocket (自动重连) + Auth Context | 已完成 |
| 布局 | 侧边栏 + 项目 Tab 导航 + 任务浮窗 + 状态徽章 | 已完成 |
| 页面 | 21 个页面 (含三栏清洗工作台 + CodeMirror + PDF 预览) | 已完成 |
| 构建 | npm run build 零错误通过 | 已完成 |

#### 基础设施 (infra/)

| 模块 | 状态 |
|------|------|
| Docker Compose (PG 16 + Redis 7 + MinIO) | 已完成，服务运行中 |
| Alembic 异步迁移 | 已完成，28 表已创建 |
| 种子脚本 (admin + 默认项目 + 3 模板) | 已完成，已执行 |

#### 已验证

- API 启动正常，133 条路由加载
- `POST /api/auth/login` 登录成功，返回 JWT
- `GET /api/auth/me` 认证端点正常
- `GET /api/projects/` 返回种子项目"压气机知识抽取"
- 前端 `npm run build` 零错误

#### 已知问题

- `bcrypt` 需要 <4.1 版本以兼容 passlib（已在环境中降级，但 pyproject.toml 未固定版本）
- Docker 端口映射使用非标准端口 (PG:5433, Redis:6380, MinIO:9002/9003)，因本机已有占用
- 外部解析器（MinerU / PaddleOCR）虽已接入 ParserProfile，但真实服务联调仍依赖具体部署环境

---

### 测试修复阶段：进行中 (2026-04-07 ~ 至今)

**34 个 Issue 已记录并回灌到 R1 基线**

详见 `docs/r1-testing-issues.md`。

#### 当前判定

- R1 不再以“初版开发完成”作为完成定义，而以“主链路端到端验收完成”作为完成定义。
- 已通过测试并完成大量修复的部分集中在认证、配置、文档上传/解析、清洗工作台、切分。
- 尚未完成最终验收的部分为 LLM 生成、审核→提升、导出下载验证。

#### 测试进度

| # | 测试项 | 状态 | 修复的 Issue |
|---|--------|------|-------------|
| 1 | 认证全流程 | ✅ 已通过 | #1 (登录跳转) |
| 2 | 项目配置 | ✅ 已通过 | #2~#9 (项目列表、模型配置、Provider 预设) |
| 3 | 文档上传 → 解析 | ✅ 已通过 | #10~#21 (上传性能、解析器、进度显示、删除) |
| 4 | 清洗工作台 | ✅ 已通过 | #22~#29, #31~#32 (PDF加载、三栏布局、字段对齐、滚动、编辑器主题) |
| 5 | 切分 | ✅ 已通过 | #33 (分块列表字段不匹配) |
| 6 | LLM 生成 | ⏳ 待测试 | — |
| 7 | 审核 → 提升 | ⏳ 待测试 | — |
| 8 | 导出 | ⏳ 待测试 | — |
| 9 | 任务中心 | ✅ 部分通过 | WebSocket 实时推送已验证 |
| 10 | 前端联调 | 🔄 持续进行 | 每阶段均验证前后端交互 |

#### 主要改进（测试期间）

- **PDF 文件端点**：新增 `GET /documents/{did}/file`，支持 iframe 嵌入 (token query param 认证 + 浏览器缓存)
- **解析实时进度**：ParseJob 立即创建 + WebSocket 推送 + 前端进度条
- **清洗工作台重构**：四栏 → 三栏 (PDF | Markdown预览 | 编辑器+评论)，CodeMirror 亮/暗主题切换
- **级联删除修复**：删除解析记录时清理 CleaningJob + MinIO 文件；删除文档时清理全部产出
- **API 响应优化**：Redis 连接复用、Storage 单例、PDF 缓存头

#### 已知问题

- `bcrypt` 需要 <4.1 版本以兼容 passlib（已在环境中降级，但 pyproject.toml 未固定版本）
- Docker 端口映射使用非标准端口 (PG:5433, Redis:6380, MinIO:9002/9003)
- Next.js 16 Turbopack dev server 长时间运行偶发内存泄漏崩溃（重启即恢复，不影响生产）
- 清洗工作台 sections 分页上限 100，超长文档需后续优化

#### 下一步

继续测试主链路后半段：
1. **LLM 生成** — 配置 LLM 端点 → 选模板 → 单 Chunk 生成 → 确认 Candidate 产出
2. **审核 → 提升** — Candidate 审核 → promote 为 CuratedItem
3. **导出** — CuratedItem 编组为 Dataset → 选 ExportProfile → 导出 → 下载验证

---

## R1+: 面向新 R1 方案的改进 —— Slice 1 已完成 (2026-04-18)

依据 `domain-dataset-gen_R1_改进清单.md` 对主链路做协作治理升级。本切片覆盖 P1（数据模型基础）+ P2（Clean 工作流）。后续切片 Chunk / Generate / Export 留待下一轮。

**10 次提交 | 24 个文件改动 | +3170 / -32 行 | 1 个 Alembic 迁移**

#### 数据模型（P1）

新增 4 张表：

| 表 | 用途 | 本切片使用 |
|----|------|-----------|
| `cleaned_document_versions` | 整篇合并后的 cleaned markdown 版本 + 管理员终审 | ✅ 已接入 |
| `chunk_sets` | 分块结果集的版本化（为 P3 预留） | 仅建表 |
| `generation_batches` | 批次生成（为 P4 预留） | 仅建表 |
| `review_records` | 结构化审核记录（polymorphic entity_type） | 接入 cleaned_document_version |

新增列（加性，全部 nullable 或含 server_default）：

- `documents`: `clean_status`（新枚举）+ `active_clean_version_id` + `active_chunk_set_id`
- `sections`: `assignment_status`（新枚举）+ `assigned_to/by/at` + `completed_at` + `return_reason`
- `chunks`: `chunk_set_id`
- `candidates`: `author_id` + `source_generation_batch_id` + `review_status` + `thinking_text`
- `snapshot_manifests`: `chunk_set_id` + `cleaned_version_id` + `generation_batch_id`

迁移文件：`apps/api/migrations/versions/58918ea257fd_r1plus_slice1_schema_additions.py`
- 6 个新枚举全部先建
- ADD COLUMN 用 server_default 隐式回填 + 显式 UPDATE 语句二次兜底
- downgrade 完整逆序

#### 后端接口（P2）

新增服务：`apps/api/app/services/clean_version_service.py`
- `create_merged_version()` — 按 ordinal 合并所有 section 的 cleaned_markdown（fallback 到 raw_markdown），上传 MinIO `outputs/cleaned/{doc_id}/v{n}.md`，新建 CleanedDocumentVersion 行
- `final_review()` — 写 review_records + 更新 version status + 联动 document.clean_status

Section 服务扩展：`bulk_assign / assign_section / complete_section / return_section`。
`update_section` 现在会把 `assigned` 或 `returned` 状态自动推进到 `in_progress`。

新增 8 条路由：

```
POST  /api/projects/{pid}/documents/{did}/cleaning/assign         reviewer+
POST  /api/projects/{pid}/documents/{did}/cleaning/merge          reviewer+
POST  /api/projects/{pid}/documents/{did}/cleaning/final-review   reviewer+
GET   /api/projects/{pid}/documents/{did}/cleaning/versions       viewer+
GET   /api/cleaned-versions/{vid}                                  authenticated
POST  /api/sections/{sid}/assign                                   reviewer+
POST  /api/sections/{sid}/complete                                 editor（受分派约束）
POST  /api/sections/{sid}/return                                   reviewer+
```

`app.main.py` 注册新路由后总路由数 133 → 141。

#### 前端（P2）

`projects/[id]/documents/[did]/clean/page.tsx` 从 443 行扩到 708 行（+265）：

- 新分派筛选 chip：全部 / 分派给我 / 我未完成 / 未分派（editor 默认 "分派给我"）
- 管理员专属批量分派面板：多选 + assignee 下拉 + 一键分派
- section 列表行显示 `assignment_status` 彩色徽章和 assignee
- 顶部完成进度条：`X / Y sections completed` + `生成合并版本` 按钮
- 最新合并版本徽章 + 管理员 `通过 / 驳回 / 查看全文` 按钮
- 工具栏增加 `完成 (分派)` 和 `退回` 按钮，按角色和状态显示

`npm run build` 零错误通过。所有新 UI 通过 `isAdmin` 门控。

#### 代码评审

code-reviewer subagent 检查后裁定 **APPROVE WITH NITS**，两条重要建议已一起修复：
1. 迁移补上显式 UPDATE 回填语句（对齐 spec §2.3）
2. `update_section` 扩展条件 `assigned` → `assigned | returned`（对齐 spec §2.4 `completed → returned → in_progress`）

详细记录在 `docs/r1-testing-issues.md` Issue #35。

#### 冒烟测试（2026-04-18 本地环境）

测试 PDF：`Active Control of Compressor Surge Using a Real Time Observer.pdf`（744KB / 11 页）

**后端 curl 冒烟（8/8 通过）**

| 端点 | HTTP | 验证要点 |
|------|------|----------|
| `POST /cleaning/assign` | 200 | `{assigned:1}`，section.assignment_status → `assigned`，document.clean_status → `section_planned` |
| `POST /sections/{sid}/complete` | 200 | `assignment_status → completed`，`completed_at` 自动填充 |
| `POST /sections/{sid}/return` | 200 | `assignment_status → returned`，`return_reason` 保存，`completed_at` 清空 |
| `POST /sections/{sid}/assign` | 200 | 重新分派生效 |
| `POST /cleaning/merge` | 200 | 创建 v1，status=`review_pending`，MinIO `outputs/cleaned/{did}/v1.md` 写入 (32B) |
| `GET /cleaning/versions` | 200 | 返回版本列表 |
| `GET /cleaned-versions/{vid}` | 200 | 返回含 `merged_markdown` 的详情 |
| `POST /cleaning/final-review` (accept) | 200 | status → `accepted`，document.clean_status → `completed`，document.active_clean_version_id 绑定，review_records 写入 action=`approve` |

状态机联动验证：
- `section.assignment_status`：`unassigned → assigned → in_progress → completed → returned → in_progress → completed` 全路径走通（含 reviewer 修复的 `returned → in_progress` 编辑自动转换）
- `document.clean_status`：`not_started → section_planned → review_pending → completed` 端到端
- `review_records` polymorphic entity_type=`cleaned_document_version` 写入正确
- MinIO artifact_key 指向正确且内容匹配 section.cleaned_markdown

**测试 PDF 注记**：由于该 PDF 无 H1 标题，`split_into_sections` fallback 成单 section，本轮只测试了 1 个 section 的分派/完成/合并。多 section 批量分派、并行完成路径需后续用带标题的 PDF 补测。

#### 开发环境一键启动脚本

`scripts/dev-start.{sh,ps1}` + `scripts/dev-stop.{sh,ps1}`：
- PowerShell 版原生，Git Bash 版等价
- 一键起 Docker / 迁移 / API / Web 到两个独立窗口
- stop 脚本按 PID 文件杀整个进程树（Windows Terminal 标签合并问题的兜底）

#### 下一步测试清单

**前端 UI 目检（未自动化）**：
- 3 列布局：PDF iframe / Markdown 预览 / CodeMirror 编辑器 渲染正常
- Section 列表行显示 `assignment_status` 彩色徽章 + assignee 名字
- 分派筛选 chip：`全部 / 分派给我 / 我未完成 / 未分派`（editor 默认 "分派给我"）
- 管理员专属：多选 checkbox + assignee 下拉 + "批量分派" 按钮
- 完成进度条：`X / Y sections completed` + "生成合并版本" 按钮禁用/可点
- 版本徽章：`v1 · review_pending` 显示 + 通过/驳回按钮
- "查看全文" 新窗口显示合并 markdown
- 工具栏 "完成 (分派)" 按钮条件显示（assigned_to=self 且 status!=completed）
- 工具栏 "退回" 按钮条件显示（admin 且 status=completed）

**角色权限目检**：
- 用非 admin editor 账号登录：应只看到 "分派给我" 且无分派面板
- editor 调用 `/cleaning/assign` 应返回 403（`require_project_member(UserRole.reviewer)`）
- editor 调用 `/sections/{其他人section}/complete` 应返回 403

**未覆盖路径（代码已实现，逻辑对称，可选补测）**：
- `POST /cleaning/final-review` 的 `action=reject` 分支 + 重新 merge v2 的循环
- 带 H1 标题的 PDF 产生多 section 时的批量分派
- 多轮 section 并行完成 → 合并后某一 section 被退回 → 重新合并 v2

**Slice 2 — P3 Chunk 工作流**（下一迭代）：
- ChunkSet 生成 worker 接入
- Chunk 统计（token 总数/平均/分布）接口
- 管理员确认 ChunkSet 流程
- 新 `chunks/workbench` 页面

**Slice 3 — P4 Generate 工作流**：
- GenerationBatch 创建 + 勾选 chunk 批量生成
- Candidate `author_id` + thinking_text 全链路
- 非作者审核约束（服务层 + DB 约束）

**Slice 4 — P5 Export Bundle**：
- manifest 版本链扩展
- 新 bundle 结构（raw / cleaned / chunks / generation / summary）

---

## R2: Lab Team — 待开始

计划交付内容：
- Taxonomy 知识分类树 + LLM 建议 + 覆盖度分析
- Section/Chunk 租约增强 + WebSocket 全事件覆盖
- 评论与 Revision 完善
- 评测中心 (EvalRun / LLM Judge / 多 run 对比)
- 模型 Playground (多模型并排对比)
- AI 质量评分 (quality_evaluation 模板)
- section_context 生成模式
- 导出增强 (按分类平衡、train/test split、下游框架配置)

前置条件：R1 测试通过

---

## R3: Quality Automation — 待开始

计划交付内容：
- PaddleOCR/PP-StructureV3 兜底解析器
- Parser Compare 多解析器对比视图
- LLM 清洗建议 (clean_suggestion 模板)
- Claim Check 逐声明事实验证
- Comment-Driven Revision
- Semantic Dedup 嵌入相似度检测
- 多切分策略 (fixed_length / recursive_separator / 手工边界)
- Taxonomy 增强

前置条件：R2 完成
