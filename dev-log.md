# Development Log

> 本文件记录项目开发进度，每个阶段完成后更新。

---

## 项目总览

| Release | 名称 | 状态 | 备注 |
|---------|------|------|------|
| R1 | Lab Pilot | 代码完成，待测试 | 主链路 MVP |
| R2 | Lab Team | 未开始 | 多人协作、评测中心 |
| R3 | Quality Automation | 未开始 | 质量自动化 |
| R4 | Optional Extensions | 未开始 | 多轮对话、Arena 等 |

---

## R1: Lab Pilot

### 开发阶段：已完成 (2026-04-05)

**27 次提交 | 191 个文件 | 28 张数据库表 | 130 条 API 路由 | 21 个前端页面**

#### 后端 (apps/api/)

| 模块 | 内容 | 状态 |
|------|------|------|
| 数据库 | 14 个模型文件 / 28 张表 / Alembic 迁移 | 已完成 |
| 认证 | JWT 登录 + 4 角色权限 (admin/reviewer/editor/viewer) | 已完成 |
| 项目管理 | 项目 CRUD + 成员管理 + 配置克隆 | 已完成 |
| 配置中心 | 5 类 Profile (ModelConfig/Parser/Chunk/Export/TaskPolicy) | 已完成 |
| 文档接入 | PDF 上传 + SHA256 去重 + MinIO 存储 | 已完成 |
| 文档解析 | Mock 解析器 (pymupdf4llm) + MinerU 接口预留 | 已完成 |
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
| parsing | BaseParser + MockParser (pymupdf4llm) + MinerU stub | 已完成 |
| cleaning | split_into_sections (H1→H2→全文 fallback) | 已完成 |
| splitters | HybridHeadingRecursiveChunker + tiktoken | 已完成 |
| llm | OpenAI 兼容客户端 + 3x 重试 + 用量回调 | 已完成 |

#### 前端 (apps/web/)

| 模块 | 内容 | 状态 |
|------|------|------|
| 框架 | Next.js 15 + TypeScript + Tailwind + shadcn/ui (15 组件) | 已完成 |
| 核心 | API Client (JWT 自动刷新) + WebSocket (自动重连) + Auth Context | 已完成 |
| 布局 | 侧边栏 + 项目 Tab 导航 + 任务浮窗 + 状态徽章 | 已完成 |
| 页面 | 21 个页面 (含四栏清洗工作台 + CodeMirror + PDF 预览) | 已完成 |
| 构建 | npm run build 零错误通过 | 已完成 |

#### 基础设施 (infra/)

| 模块 | 状态 |
|------|------|
| Docker Compose (PG 16 + Redis 7 + MinIO) | 已完成，服务运行中 |
| Alembic 异步迁移 | 已完成，28 表已创建 |
| 种子脚本 (admin + 默认项目 + 3 模板) | 已完成，已执行 |

#### 已验证

- API 启动正常，130 条路由加载
- `POST /api/auth/login` 登录成功，返回 JWT
- `GET /api/auth/me` 认证端点正常
- `GET /api/projects/` 返回种子项目"压气机知识抽取"
- 前端 `npm run build` 零错误

#### 已知问题

- `bcrypt` 需要 <4.1 版本以兼容 passlib（已在环境中降级，但 pyproject.toml 未固定版本）
- Docker 端口映射使用非标准端口 (PG:5433, Redis:6380, MinIO:9002/9003)，因本机已有占用
- MinerU 解析器为 stub，实际 PDF 解析使用 pymupdf4llm mock

---

### 测试阶段：待进行

下一步需要进行 R1 端到端集成测试，验证主链路闭环：

1. **认证全流程** — 注册用户、登录、角色权限校验
2. **项目配置** — 创建项目、配置 ModelConfig / ParserProfile / ChunkProfile
3. **文档上传 → 解析** — 上传真实 PDF → 触发解析 → 确认产出 markdown
4. **清洗工作台** — Section 自动划分 → 编辑 → 提交审核 → 通过
5. **切分** — accepted Section → 执行切分 → 确认 Chunk 产出
6. **LLM 生成** — 配置 LLM 端点 → 选模板 → 单 Chunk 生成 → 确认 Candidate 产出
7. **审核 → 提升** — Candidate 审核 → promote 为 CuratedItem
8. **导出** — CuratedItem 编组为 Dataset → 选 ExportProfile → 导出 → 下载验证
9. **任务中心** — 确认 Task 状态推送、WebSocket 实时更新
10. **前端联调** — 浏览器访问前端，验证各页面与 API 交互

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
