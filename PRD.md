# 领域知识抽取与数据资产平台 — 产品需求文档

> Version: 1.0
> Date: 2026-04-04
> Status: Final
> Base: PRD-enhanced-platform-claude.md + PRD-enhanced-platform-codex.md 合并

---

## 1. 产品定位

### 1.1 一句话定位

把领域 PDF 文献转化为**可追溯、可审核、可复现**的知识资产，并编组为微调数据集与评测基准。

### 1.2 我们在做什么

一个面向高校实验室的 Web 平台，围绕"PDF 文献 → 知识资产 → 数据集/Benchmark"构建完整的生产闭环。首个落地场景是压气机设计领域教材、手册与专著，但产品架构应支持后续扩展到其他垂直学科。

这个平台**不是**另一个通用数据集生成器。它的核心竞争力不是"更快生成 QA"，而是：

- 让 PDF 文献先被正确解析和清洗
- 让知识条目有明确证据与审核结论
- 让数据集和 benchmark 成为知识资产的下游导出物
- 让整个过程具备配置化、批处理化、可监控、可回溯的产品能力

### 1.3 产品设计的外部参考

本方案在设计过程中参考了开源项目 [easy-dataset](https://github.com/ConardLi/easy-dataset)（13.8k stars），从中吸收了**配置化、任务化、评测化、导出多样化、运行可观测**等产品化能力。但在核心架构上坚持自身定位 — 证据驱动的知识资产生产系统，而非效率导向的 QA 工厂。

**借鉴了什么**：项目控制中心、统一任务层、Token 监控、多模型管理、Playground 对比、知识分类体系、增强导出格式、评测闭环、Candidate AI 质量评分。

**明确不照搬**：
- 从 chunk 直接跳到 dataset row 的主数据模型（跳过清洗层的流程）
- 单用户优先的协作假设
- 无快照、无版本冻结的导出方式
- 多轮对话生成、Genre-Audience 多样性策略、图片 QA、数据蒸馏 — 这些偏向"通用数据集工厂"的能力不属于本平台首发边界
- Electron 桌面端、数据集社区

---

## 2. 目标用户与场景

### 2.1 目标用户

| 角色 | 核心目标 | 主要动作 |
|------|---------|---------|
| 实验室负责人 / PI | 管理项目质量与可复现性 | 配置项目、审批模板、查看导出与评测结果 |
| 研究助理 / 学生 | 承担文档清洗和初审工作 | 上传文档、认领 section、编辑、提交 |
| 领域专家 / reviewer | 做事实与证据审核 | 审核 Candidate / CuratedItem，给出证据判断 |
| 模型实验负责人 | 组织导出和模型评测 | 构建 dataset、运行 benchmark、比较模型结果 |

### 2.2 典型场景

**场景 A：从教材中抽取"设计流程"知识**
上传《航空发动机风扇压气机设计》→ MinerU 解析 → 按章节清洗校核 → 按标题切分 → 生成"设计流程步骤""关键考虑因素"等知识条目 → 专家修订 → 导出为 QA 数据集。

**场景 B：从手册中抽取"参数选取原则"构建评测基准**
从手册中抽取某类设计参数的选取原则 → 保留页码与原文证据 → 生成规范表述的知识条目 → 整理成 benchmark case → 配置 rubric → 运行 LLM Judge 自动评测 → 对比不同模型表现。

**场景 C：多人协作整理一本书**
A 同学负责第 1 章清洗，B 同学负责第 2 章。清洗完成后各自处理本章 chunk 的知识生成与校核。PI 在监控页查看进度和资源消耗。

---

## 3. 产品目标与非目标

### 3.1 目标

- 建立从原始 PDF 到知识资产、数据集、benchmark 的完整闭环
- 让每条正式知识都能追溯到文档、页码、heading path、原文证据
- 支持实验室 3~20 人分工与审核协作
- 把 parser、chunking、prompt、model、export 全部升级为可配置产品能力
- 提供批量任务、任务监控、失败恢复、资源统计
- 通过快照机制保证每次导出与每次评测可复现

### 3.2 非目标

- 不做企业级多租户 SaaS
- 不做通用图像标注平台
- 不做面向公众的数据集社区或 marketplace
- 不以"完全自动生成、无需人工审核"为目标
- 不做 Electron 桌面客户端
- 不在当前版本优先覆盖所有模态和所有行业

---

## 4. 产品原则

1. **证据优先于速度** — 每条知识必须有可追溯的原文依据
2. **清洗优先于生成** — 解析结果必须经过人工确认再进入 chunk 化
3. **CuratedItem 是正式资产** — Dataset / Benchmark 是下游导出物，不直接引用 Candidate
4. **配置优先于写代码** — 用户不需要改代码就能调整生产策略
5. **批处理必须可观测** — 可取消、可恢复、可追踪进度
6. **面向实验室真实使用** — 低维护、可自托管、适合 3~20 人长期协作
7. **资产分层治理** — 区分"证据驱动资产"和"合成扩展资产"，避免产品定位被稀释
8. **快照优先于导出** — 无法被快照记录的能力不进入正式生产链路

---

## 5. 核心范围决策

### 5.1 基础治理前置

平台首先必须具备稳定的治理底座。以下对象属于首发先行能力：

- `ModelConfig`、`PromptTemplate`、`TaskCenter`、`ExportProfile`、`SnapshotManifest`
- 标签体系、Playground、评测闭环建立在这些基础能力之上，因此后置

### 5.2 资产分层治理

平台从一开始就区分两类资产：

- **Evidence-Grounded Assets**：基于文档证据产生，可进入正式 Dataset / Benchmark。包括 Section、Chunk、Candidate、CuratedItem、Dataset、Benchmark。
- **Synthetic-Derived Assets**：基于正式知识资产扩展生成，用于训练多样性增强。包括多轮对话数据、GA 多样性变体、topic distillation 结果。

硬性约束：

- 正式 Benchmark 只允许纳入 evidence_grounded 资产
- synthetic-derived 资产必须标记 `source_type`，记录上游 CuratedItem 和生成模板
- synthetic-derived 资产默认进入单独导出通道，不与证据资产混合统计
- 当前版本只实现 evidence-grounded 链路，synthetic 链路在 R3+ 按需扩展

### 5.3 主路线聚焦

当前阶段的主路线是"PDF 文献知识资产生产闭环"。泛化到图像、多模态、脱离证据的蒸馏，不属于当前首发重点。

---

## 6. 核心对象模型

```
Document → ParseJob → Section → Chunk → Candidate → CuratedItem → Dataset / Benchmark
                                                                         ↓
                                                                    ExportSnapshot
                                                                    EvalRun
```

### 主链路对象

| 对象 | 定位 | 不可退化 |
|------|------|---------|
| **Document** | 原始 PDF 文献 | |
| **ParseJob** | 一次解析执行，记录 parser 版本和配置 | |
| **Section** | 清洗与分工单元，通常对应一级标题 | 是 |
| **Chunk** | 供 LLM 处理与证据定位的细粒度文本块 | |
| **Candidate** | LLM 生成出的候选内容（不是正式资产） | |
| **CuratedItem** | 人工核定后的正式知识条目 | 是 |
| **Dataset / Benchmark** | 导出层资产，只能从 CuratedItem 编组 | |
| **SnapshotManifest** | 导出时冻结全链路版本的可复现性载体 | 是 |

### 配置型对象

| 对象 | 说明 |
|------|------|
| **ModelConfig** | LLM 模型 Provider / endpoint / 参数 |
| **ParserProfile** | 解析器选择与参数 |
| **ChunkProfile** | 切分策略与参数 |
| **PromptTemplate** | 全链路 Prompt 模板，版本化 |
| **ExportProfile** | 导出格式 / 字段映射 / 平衡策略 |
| **TaskPolicy** | 任务并发 / 重试 / 超时策略 |
| **ReviewPolicy** | 审核策略 |

### 组织型对象（R2）

| 对象 | 说明 |
|------|------|
| **TaxonomyNode** | 知识分类树节点 |

### 扩展型对象（R3+，预留）

| 对象 | 说明 |
|------|------|
| **ConversationDataset** | 多轮对话数据集 |
| **ConversationTurn** | 多轮对话单轮 |
| **GaPair** | GA 多样性变体 |

---

## 7. 核心用户流程

### 7.1 知识生产主流程

```
1. 创建项目 → 配置 parser / model / prompt / chunk profile
2. 上传 PDF → 系统计算 hash 去重 → 持久化到对象存储
3. 选择 ParserProfile → 异步解析 → 产出 raw markdown + 结构信息 + 页面映射
4. 自动按 heading 生成 Section → 进入清洗工作台
5. 用户认领 section → 四栏对照清洗 → 评论 → 提交 → reviewer 审核
6. 基于通过审核的 section → 选择 ChunkProfile → 执行切分
7. 选择 PromptTemplate + ModelConfig → LLM 按模板生成 Candidate → 记录证据跨度
8. reviewer 对 Candidate 做证据判定（supported / partially / unsupported / out_of_scope）
9. 审核通过 → 提升为 CuratedItem → 关联分类标签（R2）
10. 从 CuratedItem 编组 Dataset / Benchmark
11. 导出 → 自动生成不可变 SnapshotManifest
```

### 7.2 模型评测流程（R2）

```
1. 从 CuratedItem 编组 Benchmark（含题目、参考答案、rubric）
2. 选择被测模型 + Judge 模型 + 评分模板
3. 启动批量评测任务
4. 查看自动评分 → 人工复核 → 保存结果
5. 评测结果与 Benchmark Snapshot 绑定
```

### 7.3 扩展数据生成流程（R3+，预留）

```
1. 从已审核的 CuratedItem 选择资产
2. 选择多轮对话或 GA 变体策略
3. 生成 synthetic-derived 资产（标记 source_type）
4. 单独审核、单独导出、单独统计
```

---

## 8. 状态机

### Document

```
uploaded → parsing → parsed / parse_failed → cleaning → cleaned → chunking → chunked → archived
```

### Section

```
draft → in_cleaning → review_pending → accepted / rejected
```

### Chunk

```
ready → generating → generated → under_review → approved → archived
```

### Candidate

```
ai_generated → human_edited → review_pending → approved / rejected
```

### CuratedItem

```
draft → approved → exported → deprecated
```

### Task

```
queued → processing → completed / failed / cancelled
```

---

## 9. 功能需求

### 9.1 项目控制中心

**目标**：把用户需要频繁调整的配置全部产品化，不埋在代码或环境变量里。

**能力清单**：

- **模型配置（ModelConfig）**：多 Provider（OpenAI-compatible / Ollama / vLLM）、多用途（text / judge / embedding）、连通性测试、项目默认模型
- **解析器配置（ParserProfile）**：MinerU / PaddleOCR 参数配置、项目默认 parser
- **切分配置（ChunkProfile）**：多策略选择（hybrid_heading_recursive / fixed_length / recursive_separator / custom）、参数调整、表格/公式保留开关
- **导出配置（ExportProfile）**：格式选择、字段映射、平衡策略、train/test 分割、下游框架配置、审核门控
- **任务策略（TaskPolicy）**：并发数、重试次数、超时、优先级
- **配置复制**：从已有项目快速克隆全套配置

必须支持：默认值和推荐值、项目级覆盖、版本记录、配置复制。

**数据模型**：

```
ModelConfig
├── id: uuid
├── project_id: uuid | null         # null = 全局默认
├── name: varchar
├── provider: varchar                # openai_compatible / ollama / vllm
├── base_url: varchar
├── api_key_encrypted: varchar
├── model_id: varchar
├── model_purpose: varchar           # text / judge / embedding
├── max_tokens: int
├── temperature: numeric
├── top_p: numeric
├── is_default: boolean
├── version: int
├── created_at / updated_at: timestamptz

ParserProfile
├── id: uuid
├── project_id: uuid | null
├── name: varchar
├── parser_name: varchar             # mineru / paddleocr
├── config_json: jsonb
├── is_default: boolean
├── version: int
├── created_at: timestamptz

ChunkProfile
├── id: uuid
├── project_id: uuid | null
├── name: varchar
├── strategy: varchar
├── max_tokens: int
├── overlap_tokens: int
├── min_tokens: int
├── preserve_tables: boolean
├── preserve_formulas: boolean
├── custom_separators: jsonb | null
├── is_default: boolean
├── version: int
├── created_at: timestamptz

ExportProfile
├── id: uuid
├── project_id: uuid | null
├── name: varchar
├── format: varchar                  # sft_jsonl / qa_json / messages / alpaca / sharegpt / benchmark_json
├── field_mapping: jsonb | null
├── include_evidence: boolean
├── balance_strategy: jsonb | null
├── train_test_split: jsonb | null   # { test_ratio: 0.1, seed: 42 }
├── downstream_config: varchar | null  # llamafactory / null
├── review_gate: boolean             # true = 仅导出已审核 CuratedItem
├── version: int
├── created_at: timestamptz

TaskPolicy
├── id: uuid
├── project_id: uuid | null
├── task_type: varchar
├── max_concurrency: int
├── retry_count: int
├── retry_delay_seconds: int
├── timeout_seconds: int
├── priority: int                    # 0=低, 1=中, 2=高
├── created_at: timestamptz
```

**API**：

```
GET/POST   /api/projects/{pid}/model-configs
PATCH/DEL  /api/model-configs/{id}
POST       /api/model-configs/{id}/test

GET/POST   /api/projects/{pid}/parser-profiles
PATCH      /api/parser-profiles/{id}

GET/POST   /api/projects/{pid}/chunk-profiles
PATCH      /api/chunk-profiles/{id}

GET/POST   /api/projects/{pid}/export-profiles
PATCH      /api/export-profiles/{id}

GET/PATCH  /api/projects/{pid}/task-policies

POST       /api/projects/{pid}/clone-config-from/{source_pid}
```

**页面**：`/projects/[id]/settings` — Tab 式布局（模型 / 解析器 / 切分 / 导出 / 任务策略 / 模板入口）

---

### 9.2 文档接入与解析

**能力清单**：

- PDF 上传 + magic bytes 校验 + 大小限制
- 文件 SHA256 去重
- 原始文件持久化到 MinIO
- 选择 ParserProfile 发起异步解析
- 解析产出：raw markdown + JSON 结构 + 页面映射
- 解析失败重试
- Parser 对比视图（同文档多次 parse 结果并排对比，R3）

**数据模型**：

```
Document
├── id: uuid
├── project_id: uuid
├── title: varchar
├── source_filename: varchar
├── object_key: varchar              # MinIO 中的 PDF 路径
├── file_sha256: varchar
├── page_count: int
├── parse_status: varchar            # uploaded / parsing / parsed / failed
├── cleaning_status: varchar         # pending / in_progress / cleaned
├── latest_parse_job_id: uuid
├── latest_cleaning_job_id: uuid
├── created_by: uuid
├── created_at: timestamptz

ParseJob
├── id: uuid
├── document_id: uuid
├── parser_profile_id: uuid          # 引用 ParserProfile
├── parser_name: varchar
├── parser_version: varchar
├── config_json: jsonb
├── status: varchar                  # queued / running / succeeded / failed
├── task_id: uuid                    # 关联统一 Task
├── raw_markdown_key: varchar
├── raw_json_key: varchar
├── started_at / finished_at: timestamptz
├── error_message: text
```

**API**：

```
POST /api/projects/{pid}/documents/upload
GET  /api/projects/{pid}/documents
GET  /api/documents/{did}
POST /api/documents/{did}/parse           # body: { parser_profile_id }
GET  /api/documents/{did}/parse-jobs
GET  /api/parse-jobs/{jid}
```

---

### 9.3 清洗工作台

清洗工作台是本平台最核心的差异化页面。解析结果必须经过人工确认，再进入切分。

**四栏布局**：

| 栏 | 内容 | 交互 |
|----|------|------|
| 左栏 | 原 PDF（PDF.js 渲染） | 页码跳转，当前 section 页面高亮 |
| 中左 | 原始 Markdown（只读） | 解析器原始输出，用于对照 |
| 中右 | 清洗后 Markdown（可编辑） | 语法高亮，接受/拒绝 LLM 建议 |
| 右栏 | 预览 + 评论 + 审核操作 | Markdown 渲染预览，评论列表，accept/reject |

**能力清单**：

- 自动按一级 heading 切分为 Section
- Section 列表 + 状态展示 + 批量状态视图
- Section 认领 / 租约（同一时刻只有一人可编辑）
- Section 评论（parse_issue / ocr_issue / layout_issue / general）
- Section revision 记录（human_edit / llm_suggestion / merge）
- reviewer accept / reject
- 原文页码联动（点击 section → PDF 跳到对应页）
- LLM 清洗建议入口（R3）
- 异常 section 标记

**数据模型**：

```
CleaningJob
├── id: uuid
├── document_id: uuid
├── source_parse_job_id: uuid
├── status: varchar
├── config_json: jsonb
├── started_at / finished_at: timestamptz

Section
├── id: uuid
├── document_id: uuid
├── cleaning_job_id: uuid
├── ordinal: int
├── heading_level: int
├── heading_text: varchar
├── heading_path: jsonb
├── source_pages: int[]
├── raw_markdown: text
├── cleaned_markdown: text
├── status: varchar                  # draft / in_cleaning / review_pending / accepted / rejected
├── assigned_to: uuid
├── created_at: timestamptz

SectionLease
├── id: uuid
├── section_id: uuid
├── leased_by: uuid
├── lease_token: varchar
├── leased_at / expires_at: timestamptz
├── is_active: boolean

SectionComment
├── id: uuid
├── section_id: uuid
├── author_id: uuid
├── body: text
├── comment_type: varchar
├── created_at: timestamptz

SectionRevision
├── id: uuid
├── section_id: uuid
├── revision_type: varchar           # human_edit / llm_suggestion / merge
├── before_markdown / after_markdown: text
├── created_by: uuid
├── created_at: timestamptz
```

**API**：

```
POST  /api/documents/{did}/cleaning/start
GET   /api/documents/{did}/sections
GET   /api/sections/{sid}
PATCH /api/sections/{sid}                    # 编辑 cleaned_markdown
POST  /api/sections/{sid}/submit             # 提交审核
POST  /api/sections/{sid}/review             # reviewer accept/reject

POST  /api/sections/{sid}/lease/acquire
POST  /api/sections/{sid}/lease/heartbeat
POST  /api/sections/{sid}/lease/release

POST  /api/sections/{sid}/comments
GET   /api/sections/{sid}/comments
```

---

### 9.4 Chunking 中心

Chunking 不是单次黑盒动作，而是正式产品模块。

**支持的策略**：

- `hybrid_heading_recursive`（默认推荐）— 基于 heading path 先划大块，块内按段落递归
- `fixed_length` — 固定 token 长度
- `recursive_separator` — 按自定义分隔符递归
- `custom` — 用户自定义逻辑

**关键约束**：

- 切分输入必须是经过 Section 清洗确认的 `cleaned_markdown`
- 每个 Chunk 必须携带 `section_id`、`heading_path`、`source_pages`
- 每次 chunking 必须绑定 `ChunkProfile`
- 支持同一文档重复 chunking 并保留版本
- 支持手工边界覆盖（用户可手动调整自动切分结果）

**数据模型**：

```
Chunk
├── id: uuid
├── document_id: uuid
├── section_id: uuid                 # 来源 section（证据链关键）
├── cleaning_job_id: uuid
├── chunk_profile_id: uuid           # 绑定 ChunkProfile
├── ordinal: int
├── content: text
├── token_count: int
├── heading_path: jsonb
├── source_pages: int[]
├── status: varchar                  # ready / generating / generated / under_review / approved
├── created_at: timestamptz
```

**API**：

```
POST /api/documents/{did}/chunk            # body: { chunk_profile_id }
GET  /api/documents/{did}/chunks           # 分页 + 筛选（by section / token range / status）
GET  /api/chunks/{cid}
PATCH /api/chunks/{cid}                    # 手工编辑边界
```

---

### 9.5 Prompt Template 中心

Prompt 是业务逻辑的一部分，不是附属配置。模板中心覆盖全链路任务类型。

**任务类型**：

| task_type | 说明 | Release |
|-----------|------|---------|
| `knowledge_extraction` | 知识条目抽取 | R1 |
| `qa_generation` | QA 对生成 | R1 |
| `benchmark_case` | Benchmark case 生成 | R1 |
| `tag_suggestion` | 分类标签建议 | R2 |
| `quality_evaluation` | Candidate AI 质量评分 | R2 |
| `judge_scoring` | Benchmark LLM Judge 评分 | R2 |
| `clean_suggestion` | Section LLM 清洗建议 | R3 |
| `claim_check` | 声明事实验证 | R3 |
| `comment_revision` | 基于评论的 LLM 修订 | R3 |

**数据模型**：

```
PromptTemplate
├── id: uuid
├── project_id: uuid
├── name: varchar
├── task_type: varchar
├── system_prompt: text
├── user_prompt: text
├── input_schema: jsonb              # 模板输入变量定义
├── output_schema: jsonb             # 期望输出结构
├── recommended_model_id: uuid | null
├── is_default: boolean
├── version: int                     # 编辑自动递增
├── parent_template_id: uuid | null  # fork 来源
├── few_shot_examples: jsonb | null
├── language: varchar                # zh / en
├── created_by: uuid
├── created_at / updated_at: timestamptz
```

**能力清单**：

- 按 task_type 分组管理
- 项目级覆盖 + 默认模板回退
- 模板复制（duplicate / fork）
- **模板试跑**：选一个 chunk → 预览完整拼装后的 prompt → 调用 LLM → 看结果
- 版本历史追踪
- 使用统计（调用次数、成功率、平均质量分）
- few-shot 示例管理（可从已审核 CuratedItem 选取）

**API**：

```
GET/POST   /api/projects/{pid}/prompt-templates       # 列表（?task_type=xxx）/ 新增
PATCH      /api/prompt-templates/{id}                  # 编辑（自动 version++）
POST       /api/prompt-templates/{id}/duplicate
POST       /api/prompt-templates/{id}/test-run         # 试跑
GET        /api/prompt-templates/{id}/versions
GET        /api/prompt-templates/{id}/stats
```

**页面**：`/projects/[id]/templates` — 分组展示，编辑页左侧 prompt 编辑 + 右侧试跑面板

---

### 9.6 Candidate 生成与证据审核

**生成能力**：

- 上下文模式：`single_chunk`（仅当前 chunk）/ `section_context`（+ 同 section 相邻 chunk + heading path）
- 批量生成：对文档所有 chunk 批量执行，创建批量 Task
- 结构化输出：content_json + evidence_spans_json
- 每次生成记录完整上下文：template_id、model_config_id、context_mode、context_chunk_ids

**审核要求**：

- reviewer 必须给出 evidence judgment：`supported` / `partially_supported` / `unsupported` / `out_of_scope`
- 必须记录：证据 span、缺失前提条件、驳回原因
- 支持评论
- claim check 辅助验证（R3）
- comment-driven LLM 修订（R3）

**AI 质量评分**（R2，辅助审核，不替代人工）：

- 评分维度：准确性 / 完整性 / 可用性 / 综合分（0~1.0）
- 帮助 reviewer 优先处理低分 Candidate
- 支持批量评分
- 分数仅供参考，不改变正式审核状态，不得自动替代 reviewer approve / reject

**数据模型**：

```
GenerationRun
├── id: uuid
├── chunk_id: uuid
├── template_id: uuid
├── model_config_id: uuid
├── context_mode: varchar
├── context_chunk_ids: uuid[]
├── task_id: uuid                    # 关联统一 Task
├── status: varchar
├── created_at: timestamptz

Candidate
├── id: uuid
├── generation_run_id: uuid
├── chunk_id: uuid
├── candidate_type: varchar          # knowledge_item / qa_pair / rule / benchmark_seed
├── title: varchar
├── content_json: jsonb
├── evidence_spans_json: jsonb
├── confidence_score: numeric
├── source_type: varchar             # evidence_grounded（默认）
├── status: varchar                  # ai_generated / human_edited / review_pending / approved / rejected
├── quality_score_json: jsonb | null # AI 质量评分（R2）
├── quality_evaluated_at: timestamptz | null
├── created_at: timestamptz
```

**API**：

```
POST  /api/chunks/{cid}/generate           # body: { template_id, model_config_id, context_mode }
POST  /api/documents/{did}/generate-batch  # 批量生成
GET   /api/chunks/{cid}/generation-runs
GET   /api/candidates/{cid}
PATCH /api/candidates/{cid}                # 编辑
POST  /api/candidates/{cid}/review         # 证据判定
POST  /api/candidates/{cid}/comments

POST  /api/candidates/{cid}/evaluate-quality           # AI 质量评分（R2）
POST  /api/projects/{pid}/candidates/batch-evaluate    # 批量评分（R2）
GET   /api/projects/{pid}/candidates?min_score=0.7&sort=quality
```

---

### 9.7 CuratedItem 资产管理

CuratedItem 是系统的正式知识资产层。Dataset / Benchmark 只能从 CuratedItem 编组。

**CuratedItem 类型**：

- 流程型知识（process）
- 规则型知识（rule）
- 参数选取原则（parameter_principle）
- 约束条件（constraint）
- 术语 / 概念（terminology）
- 公式 / 定量关系（formula）
- 失败模式 / 经验教训（lesson_learned）
- benchmark seed

**能力清单**：

- 从 approved Candidate 提升（promote）
- 类型管理 + 标准标题 + 标准内容
- revision 历史（human_edit / llm_revise / merge）
- evidence link 回溯（→ document / chunk / pages / heading_path / quote_text）
- 评论与审核
- 关联分类标签（TaxonomyNode，R2）
- `source_type` 字段（evidence_grounded / synthetic_derived），为资产分层预留

**数据模型**：

```
CuratedItem
├── id: uuid
├── project_id: uuid
├── item_type: varchar
├── canonical_title: varchar
├── canonical_content_json: jsonb
├── applicable_scope: text
├── evidence_summary: text
├── created_from_candidate_id: uuid
├── source_type: varchar             # evidence_grounded（默认）/ synthetic_derived
├── status: varchar                  # draft / approved / exported / deprecated
├── version: int
├── created_by: uuid
├── created_at: timestamptz

CuratedRevision
├── id: uuid
├── curated_item_id: uuid
├── revision_type: varchar
├── before_json / after_json: jsonb
├── created_by: uuid
├── created_at: timestamptz

EvidenceLink
├── id: uuid
├── curated_item_id: uuid
├── document_id: uuid
├── chunk_id: uuid
├── source_pages: int[]
├── heading_path: jsonb
├── quote_text: text
├── span_json: jsonb
```

**API**：

```
POST  /api/candidates/{cid}/promote-to-curated
GET   /api/projects/{pid}/curated-items     # 筛选：?type=xxx&taxonomy=xxx&status=xxx&source_type=xxx
GET   /api/curated-items/{id}
PATCH /api/curated-items/{id}
GET   /api/curated-items/{id}/revisions
POST  /api/curated-items/{id}/comments
POST  /api/curated-items/{id}/review
```

---

### 9.8 Taxonomy 知识分类体系（R2）

分类树是项目知识组织骨架，用于资产检索、导出平衡、benchmark 覆盖度分析。

依赖稳定的 CuratedItem 沉淀，因此安排在 R2。

**设计要点**：

- 两级分类（一级 5~10 个，二级 1~5 个/一级）
- 可由 LLM 基于文档目录/heading path 生成建议，用户确认后写入
- 也可由已审核 CuratedItem 反向生成建议（R3）
- 用户可手工维护、合并、重命名节点
- CuratedItem 可关联多个分类节点
- 按标签筛选、统计、平衡导出

**数据模型**：

```
TaxonomyNode
├── id: uuid
├── project_id: uuid
├── parent_id: uuid | null
├── label: varchar(50)
├── description: text | null
├── ordinal: int
├── item_count: int                  # 缓存字段
├── created_at / updated_at: timestamptz

CuratedItemTaxonomy（关联表）
├── curated_item_id: uuid
├── taxonomy_node_id: uuid
├── auto_assigned: boolean
├── created_at: timestamptz
```

**API**：

```
POST  /api/projects/{pid}/taxonomy/generate-suggestion
GET   /api/projects/{pid}/taxonomy
POST  /api/projects/{pid}/taxonomy
PATCH /api/taxonomy/{nid}
DEL   /api/taxonomy/{nid}
POST  /api/taxonomy/{nid}/merge-into/{target_nid}
GET   /api/projects/{pid}/taxonomy/coverage

POST  /api/curated-items/{id}/taxonomy
DEL   /api/curated-items/{id}/taxonomy/{nid}
```

**页面**：`/projects/[id]/taxonomy` — 左侧树形结构，右侧关联 CuratedItem 列表 + 覆盖度提示

---

### 9.9 Dataset / Benchmark 管理

**能力清单**：

- 从 CuratedItem 编组 Dataset 或 Benchmark
- 过滤、排序、搜索、批量选择
- 按类型、来源文档、分类树（R2）、证据完整度、审核状态筛选
- 评分 / 备注 / 自定义标签
- **硬性规则：未完成规定审核条件的 CuratedItem 不得进入正式导出**
- **硬性规则：正式 Benchmark 只允许纳入 source_type=evidence_grounded 的资产**

**Benchmark Case 结构**：

```
BenchmarkCase
├── case_type: varchar               # fact / rule / process / comparison / multi_constraint
├── problem: text
├── reference_answer: text
├── acceptable_points: jsonb
├── rubric: text
├── evidence: text
├── difficulty: varchar              # easy / medium / hard
├── tags: jsonb
├── split: varchar                   # train / dev / test
├── judge_mode: varchar              # exact / rubric / llm_judge / human_only
```

**API**：

```
POST /api/projects/{pid}/datasets
POST /api/projects/{pid}/benchmarks
POST /api/curated-items/{id}/add-to-dataset
POST /api/curated-items/{id}/add-to-benchmark
GET  /api/datasets/{did}/items
GET  /api/benchmarks/{bid}/cases
```

---

### 9.10 导出与可复现性

**导出格式**：

| 格式 | 结构 |
|------|------|
| `sft_jsonl` | `{"instruction", "input", "output"}` |
| `qa_json` | `{"question", "answer", "evidence"}` |
| `messages` | `{"messages": [{"role", "content"}]}` |
| `alpaca` | `{"instruction", "input", "output"}` |
| `sharegpt` | `{"conversations": [{"from", "value"}]}` |
| `benchmark_json` | 含 rubric / difficulty / case_type |

**SnapshotManifest** — 每次导出自动生成，冻结全链路版本：

```json
{
    "export_id": "uuid",
    "exported_at": "timestamp",
    "exported_by": "user_id",
    "export_profile": { "format": "alpaca", "version": 2 },
    "asset_class": "evidence_grounded",
    "sources": {
        "documents": [{ "id", "file_sha256", "parse_job_id", "parser_version" }],
        "parser_profile_version": "int",
        "section_revisions": [{ "section_id", "revision_hash" }],
        "chunk_profile": { "id", "version" },
        "prompt_templates": [{ "id", "version" }],
        "model_configs": [{ "id", "model_id", "version" }],
        "review_policy_version": "int",
        "tag_tree_version": "int | null"
    },
    "items": {
        "total": "int",
        "by_type": {},
        "by_taxonomy": {},
        "dataset_item_ids": ["uuid"] ,
        "benchmark_case_ids": ["uuid"]
    },
    "integrity": { "item_ids": ["uuid"], "content_hash": "sha256" }
}
```

评测导出时额外包含：

```json
{
    "eval_run_id": "uuid",
    "judge_model_config_version": "int",
    "benchmark_snapshot_id": "uuid",
    "scoring_rubric_version": "int"
}
```

**硬性规则**：
- 没有完整 manifest 的导出不算正式导出
- 没有完整 manifest 的评测结果不算正式评测结果

**下游集成**：导出时可选生成 LLaMA Factory `dataset_info.json`。

**导出页面增强**：
- ExportProfile 选择
- 导出预览（条数、类型/分类分布、前 5 条样例）
- "未通过审核的 CuratedItem 将被排除"提示
- 导出历史 + SnapshotManifest 查看

**API**：

```
POST /api/datasets/{did}/export
     body: { export_profile_id, preview_only?, generate_downstream_config? }
POST /api/benchmarks/{bid}/export

GET  /api/projects/{pid}/exports
GET  /api/exports/{eid}/manifest
GET  /api/exports/{eid}/download
```

---

### 9.11 评测中心（R2）

Benchmark 不只是导出文件，而是可执行评测资产。

**能力清单**：

- 选择 Benchmark + 被测模型 + Judge 模型 + 评分模板 → 启动批量评测
- 客观题（判断/选择）：精确匹配
- 简答题：LLM Judge 评分（0~1.0 + 理由）
- 开放题：LLM Judge 评分（关键点覆盖 + 逻辑连贯 + 具体性）
- 人工复核入口（覆盖自动分数）
- 多次评测对比
- 评测结果与 Benchmark Snapshot 绑定

Arena 盲测放入 R4 或更后。

**数据模型**：

```
EvalRun
├── id: uuid
├── project_id: uuid
├── benchmark_id: uuid
├── model_config_id: uuid            # 被测模型
├── judge_model_config_id: uuid      # Judge 模型
├── judge_template_id: uuid
├── status: varchar
├── task_id: uuid                    # 关联统一 Task
├── total_cases / completed_cases: int
├── summary_json: jsonb | null
├── snapshot_manifest_id: uuid | null
├── created_by: uuid
├── created_at: timestamptz

EvalResult
├── id: uuid
├── eval_run_id: uuid
├── benchmark_case_id: uuid
├── model_response: text
├── response_tokens: int
├── response_latency_ms: int
├── judge_score: numeric             # 0.0 ~ 1.0
├── judge_reason: text
├── is_correct: boolean | null
├── human_override_score: numeric | null
├── human_override_reason: text | null
├── created_at: timestamptz
```

**API**：

```
POST  /api/benchmarks/{bid}/eval-runs
GET   /api/benchmarks/{bid}/eval-runs
GET   /api/eval-runs/{rid}
GET   /api/eval-runs/{rid}/results
PATCH /api/eval-results/{id}           # 人工复核
GET   /api/benchmarks/{bid}/eval-runs/compare?run_ids=a,b
```

**页面**：`/projects/[id]/benchmarks/[bid]/eval` — 发起评测 / 汇总卡片 / 逐题结果 / 多次对比

---

### 9.12 任务中心

一等页面。所有异步操作必须经过统一 Task 层。

**覆盖任务类型**：parse / clean_check / chunk / generate / claim_check / export / eval / tag_suggest / revision

**能力清单**：

- 队列状态 + 进度条（百分比 + 已完成/总数）
- 取消 / 重试 / 删除
- 错误信息 + 日志查看
- 批量任务：父 Task 下辖子 Task
- WebSocket 实时推送状态变更

每个任务必须记录：状态、进度、创建人、输入参数、错误原因、开始与完成时间、父子任务关系。

**数据模型**：

```
Task
├── id: uuid
├── project_id: uuid
├── parent_task_id: uuid | null
├── task_type: varchar
├── status: varchar                  # queued / processing / completed / failed / cancelled
├── progress: int                    # 0-100
├── total_items / completed_items: int | null
├── input_params: jsonb
├── result_summary: jsonb | null
├── error_message: text | null
├── log_key: varchar | null          # MinIO 中的日志文件
├── created_by: uuid
├── started_at / completed_at / created_at: timestamptz
```

**与现有异步任务的关系**：

- 所有异步操作先创建 Task 记录，再提交到执行层（BackgroundTasks / Celery）
- 执行层更新 Task 状态和进度
- 原有 `parse_jobs`、`generation_runs` 保留，Task 通过 `input_params` 中的 job_id 关联

**WebSocket 事件**：

```
task.created    { task_id, task_type, status }
task.progress   { task_id, progress, completed_items, total_items }
task.completed  { task_id, result_summary }
task.failed     { task_id, error_message }
```

**API**：

```
GET  /api/projects/{pid}/tasks              # ?status=failed&type=generate
GET  /api/projects/{pid}/tasks/summary      # 各状态数量
GET  /api/tasks/{tid}
GET  /api/tasks/{tid}/log
POST /api/tasks/{tid}/cancel
POST /api/tasks/{tid}/retry
DEL  /api/tasks/{tid}
```

**页面**：
- 右下角浮窗徽标（进行中任务数）→ 展开面板
- 独立页 `/projects/[id]/tasks` — 完整任务历史

---

### 9.13 资源与监控中心

帮实验室回答：哪个模型更贵、哪个模板更稳定、哪条流水线最耗资源。

**数据模型**：

```
LlmUsageLog
├── id: uuid
├── project_id: uuid
├── task_id: uuid | null
├── user_id: uuid
├── task_type: varchar
├── model_config_id: uuid
├── prompt_template_id: uuid | null
├── input_tokens / output_tokens / total_tokens: int
├── latency_ms: int
├── status: varchar                  # success / error
├── error_message: text | null
├── created_at: timestamptz
```

**实现方式**：在 `libs/llm` client wrapper 中注入非阻塞日志采集。

**API**：

```
GET /api/projects/{pid}/monitoring/summary
GET /api/projects/{pid}/monitoring/by-task-type
GET /api/projects/{pid}/monitoring/by-model
GET /api/projects/{pid}/monitoring/daily-trend
GET /api/projects/{pid}/monitoring/by-template
```

**页面**：`/projects/[id]/monitoring` — 概览卡片 + 趋势图 + 任务类型饼图 + 模型/模板对比表

费用估算：用户可在 ModelConfig 中配置单价（非必填）。

---

### 9.14 模型对比 Playground（R2）

针对当前片段快速比较 2-3 个模型或模板的输出。是 prompt 调优和模型选型的入口。

**API**：

```
POST /api/playground/compare
body: {
    chunk_id?: string,
    custom_text?: string,
    template_id: string,
    model_config_ids: string[],      # 2-3 个
    context_mode?: "single_chunk" | "section_context"
}
→ results: [{ model_config_id, model_name, output, raw_response, input_tokens, output_tokens, latency_ms }]
```

**页面**：`/projects/[id]/playground` — 选 chunk/文本 + 选模板 + 选模型 → 并排展示 + 对比表

可从 Chunk 详情页"在 Playground 中打开"一键跳转。

---

### 9.15 协作与权限

面向实验室 3~20 人协作。

**角色**：

| 角色 | 权限 |
|------|------|
| admin | 全部权限 + 用户管理 + 项目配置 |
| reviewer | 审核 Section/Candidate/CuratedItem + 强制接管租约 |
| editor | 清洗 / 生成 / 编辑 / 评论 |
| viewer | 只读 |

**协作机制**：

- Section 租约：同一时刻一人编辑，心跳续期，过期自动释放
- Chunk 租约：同理
- reviewer 强制接管 + 退回
- 评论与 revision 可审计
- WebSocket 实时状态刷新

**认证**：简单 JWT（用户名/密码），admin 创建账户。

---

## 10. 页面信息架构

```
/login
/projects                                  # 项目列表
/projects/[id]                             # 项目首页（文档统计、进度、待审核、最近任务、token 用量摘要）
/projects/[id]/settings                    # 项目控制中心（Tab: 模型/解析器/切分/导出/任务策略）
/projects/[id]/documents                   # 文档列表
/projects/[id]/documents/[did]             # 文档详情（解析状态、section 列表、chunk 列表）
/projects/[id]/documents/[did]/clean       # 清洗工作台（四栏）
/projects/[id]/documents/[did]/chunks      # Chunk 列表
/projects/[id]/documents/[did]/chunks/[cid]# Chunk 详情 + 生成面板
/projects/[id]/templates                   # Prompt Template 中心
/projects/[id]/curated                     # CuratedItem 列表
/projects/[id]/datasets                    # Dataset 管理
/projects/[id]/datasets/[did]/export       # 导出页
/projects/[id]/benchmarks                  # Benchmark 管理
/projects/[id]/tasks                       # 任务中心（也有全局浮窗入口）
/projects/[id]/monitoring                  # 资源监控
```

### R2 新增页面

```
/projects/[id]/taxonomy                    # 分类管理
/projects/[id]/benchmarks/[bid]/eval       # 评测中心
/projects/[id]/playground                  # 模型对比
```

### R3+ 新增页面（预留）

```
/projects/[id]/conversations               # 多轮对话管理（synthetic-derived）
/projects/[id]/ga-config                   # GA 配置（synthetic-derived）
```

---

## 11. 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Next.js 15 (App Router) + TypeScript + Tailwind CSS + shadcn/ui |
| 后端 | FastAPI + Pydantic v2 + SQLAlchemy 2.x + Alembic |
| 数据库 | PostgreSQL 16 |
| 缓存/任务 | Redis 7 + FastAPI BackgroundTasks（R1）→ Celery（R2+） |
| 文件存储 | MinIO（S3-compatible） |
| 文档解析 | MinerU（主）→ PaddleOCR（R3 兜底） |
| LLM | OpenAI-compatible gateway |
| 认证 | JWT（PyJWT） |
| Python 包管理 | uv workspace |
| 部署 | Docker Compose（单机） |

---

## 12. 数据库新增表汇总

相对于原有工程方案中已定义的基础表（users, documents, parse_jobs, cleaning_jobs, sections, section_leases, section_comments, section_revisions, chunks, generation_runs, candidates, curated_items, curated_revisions, evidence_links, datasets, dataset_items, benchmarks, benchmark_cases），本 PRD 新增以下表：

| 表 | Release | 模块 |
|----|---------|------|
| `model_configs` | R1 | 项目控制中心 |
| `parser_profiles` | R1 | 项目控制中心 |
| `chunk_profiles` | R1 | 项目控制中心 |
| `export_profiles` | R1 | 项目控制中心 |
| `task_policies` | R1 | 项目控制中心 |
| `tasks` | R1 | 任务中心 |
| `llm_usage_logs` | R1 | 资源监控 |
| `taxonomy_nodes` | R2 | 知识分类 |
| `curated_item_taxonomy` | R2 | 知识分类 |
| `eval_runs` | R2 | 评测中心 |
| `eval_results` | R2 | 评测中心 |

现有表新增字段：
- `parse_jobs` + `parser_profile_id`, `task_id`
- `chunks` + `chunk_profile_id`
- `prompt_templates` + `task_type`, `version`, `input_schema`, `output_schema`, `recommended_model_id`, `is_default`, `few_shot_examples`, `language`
- `candidates` + `quality_score_json`, `quality_evaluated_at`, `source_type`
- `curated_items` + `source_type`
- `generation_runs` + `task_id`

---

## 13. Release 规划

### Release 1: Lab Pilot

**目标**：形成实验室内部可真实试用的"可信知识生产 MVP"。聚焦主链路闭环。

| 模块 | 交付内容 |
|------|---------|
| 认证与项目 | JWT 登录、项目 CRUD |
| 项目控制中心 | ModelConfig / ParserProfile / ChunkProfile / ExportProfile / TaskPolicy CRUD + 设置页面 |
| 文档接入 | PDF 上传、hash 去重、MinIO 持久化 |
| 文档解析 | MinerU 集成、异步解析、状态追踪 |
| 清洗工作台 | 四栏布局、Section 自动划分、编辑/提交/审核 |
| Chunking | hybrid_heading_recursive 默认策略 + ChunkProfile 绑定 |
| Prompt Template 中心 | knowledge_extraction + qa_generation + benchmark_case 模板，版本化 + 试跑 |
| Candidate 生成 | single_chunk 模式生成 + 证据审核 |
| CuratedItem | promote + 编辑 + evidence link |
| Dataset/Benchmark | 基础编组 + 导出 |
| 导出增强 | ExportProfile + SFT JSONL / QA JSON / Alpaca / ShareGPT 格式 + SnapshotManifest |
| 任务中心 | 统一 Task 层 + 任务面板 + WebSocket 推送 |
| 资源监控 | LlmUsageLog 采集 + 概览页 |
| Docker Compose | 一键部署 |

**不含**（后移到 R2）：Taxonomy 分类树、Playground、评测中心、AI 质量评分、section_context 模式

### Release 2: Lab Team

**目标**：支持 3~5 人稳定协作、知识分类、批量实验与模型评测。

| 模块 | 交付内容 |
|------|---------|
| Taxonomy 知识分类 | TaxonomyNode CRUD + LLM 建议生成 + CuratedItem 关联 + 覆盖度分析 |
| Section / Chunk 租约 | 租约获取/心跳/释放 + 前端锁定 UI |
| 评论与 revision | Section 评论 + CuratedItem 评论 + revision 历史 |
| WebSocket 完善 | section.lease.changed / candidate.updated 等全事件覆盖 |
| 评测中心 | EvalRun / EvalResult + LLM Judge + 评测页面 |
| 模型 Playground | 多模型对比 API + Playground 页面 |
| 质量评分 | quality_evaluation 模板 + 批量评分 |
| section_context 模式 | 相邻 chunk + heading path 拼接 |
| 导出增强 | 平衡策略 + train/test split + 下游框架配置 + 导出预览 + LLaMA Factory / Hugging Face 兼容 |

### Release 3: Quality Automation

**目标**：提升质量与效率，不扩大边界。

| 模块 | 交付内容 |
|------|---------|
| Parser Compare | 同文档多 parser 对比视图 |
| LLM 清洗建议 | clean_suggestion 模板 + Section 内联建议 |
| Claim Check | 逐声明事实验证 |
| Comment-Driven Revision | 基于评论的 LLM 自动修订 |
| Semantic Dedup | 嵌入相似度检测 + 合并 |
| 分类树增强 | 基于 CuratedItem 反向优化分类建议 |
| 多切分策略 | fixed_length / recursive_separator / 手工覆盖 |
| PaddleOCR / Vision 解析 | 兜底解析器，支持扫描件和复杂版式 |

### Release 4: Optional Extensions（仅在主路线稳定后考虑）

| 模块 | 交付内容 |
|------|---------|
| 多轮对话 | synthetic-derived，从 CuratedItem 生成，单独导出 |
| GA 多样性 | synthetic-derived，绑定 CuratedItem，单独导出 |
| Arena 盲测 | 多模型盲测对比 |
| Topic Distillation | 研究性预留，标注 source_type=distillation |
| 图片 QA | 暂不纳入正式路线 |

---

## 14. 能力项决策表

| 能力项 | 处理结论 | Release |
|--------|---------|---------|
| 项目控制中心 | 首发核心 | R1 |
| 模型配置中心 | 首发核心 | R1 |
| Prompt Template 中心 | 首发核心 | R1 |
| 四栏清洗工作台 | 首发核心 | R1 |
| Candidate 审核 + CuratedItem | 首发核心 | R1 |
| 任务中心 | 首发核心 | R1 |
| Token 用量追踪 | 首发核心 | R1 |
| 导出 + SnapshotManifest | 基础 R1，增强 R2 | R1/R2 |
| 标签树 Taxonomy | 保留，后移 | R2 |
| 评测中心 | 先做可执行评测 | R2 |
| Playground | 拆分：模型配置 R1，Playground R2 | R2 |
| AI 质量评分 | 辅助信号，不替代审核 | R2 |
| section_context 模式 | 增强生成质量 | R2 |
| 多轮对话 | synthetic-derived，按需扩展 | R4 |
| GA 多样性 | synthetic-derived，按需扩展 | R4 |
| Vision 解析 fallback | 兜底解析器 | R3 |
| 数据蒸馏 | 暂缓，不纳入正式路线 | Deferred |
| 图片 QA | 暂缓，不纳入正式路线 | Deferred |

---

## 15. 成功指标

### 生产效率

- 从文档上传到首批 CuratedItem 产出的中位耗时
- 每人每周完成的 accepted section 数
- 每份文档的平均人工返工次数
- 任务失败重试后的恢复成功率

### 质量

- Candidate 审核通过率
- CuratedItem 的 evidence completeness 覆盖率
- 导出后抽检错误率
- Benchmark 题目中"不可判定 / 证据不足"比例

### 治理

- 有效 snapshot 覆盖率（有完整 manifest 的导出占比）
- Token 成本可见率
- 模板版本绑定率
- 模型配置版本绑定率

### 运营

- 任务失败率
- 模型调用平均时延
- 单项目 Token 消耗
- 快照复现成功率

---

## 16. 关键风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| PDF 解析质量不稳定 | 污染全链路 | ParserProfile + Parser Compare + 清洗层优先 |
| 基础治理能力后置 | 新功能可做但不可运维 | 前置项目控制中心、模型中心、任务中心 |
| 审核成本过高 | 降低团队采用率 | Section 分工 + 批量视图 + AI 质量评分辅助 |
| Prompt 迭代失控 | 导出不可复现 | 模板版本化 + 绑定 SnapshotManifest |
| 合成扩展资产混入正式资产 | 稀释产品定位 | source_type 字段 + 资产分层 + 独立导出 |
| 快照边界不完整 | 导出与评测不可复现 | 强制增强 SnapshotManifest |
| 功能过宽导致首发失焦 | 交付拖慢 | R1 聚焦主链路闭环，Taxonomy/评测/Playground 后移 |
| 批量任务不可观测 | 用户体验差 | 统一 Task 层 + 任务中心 + WebSocket |
| LLM 调用成本不透明 | 经费管理困难 | 监控中心 + 费用估算 |
