# Development Log

> 本文件记录项目开发进度，每个阶段完成后更新。

---

## 项目总览

| Release | 名称 | 状态 | 备注 |
|---------|------|------|------|
| R1 | Lab Pilot | 测试进行中 | 主链路 MVP，已修复 34 个 issue |
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
