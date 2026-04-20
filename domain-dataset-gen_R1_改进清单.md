# domain-dataset-gen 面向新 R1 方案的详细改进清单

## 1. 文档目的

本文档用于把当前 `domain-dataset-gen` 仓库，逐步调整到新的 R1 流程目标：

- `upload`：保持现状
- `parse`：保持现状
- `clean`：先切 section，再由管理员分派，多人逐 section 校对，最后形成完整 cleaned markdown 并由管理员终审
- `chunk`：围绕 **整套分块结果集** 进行版本化、可视化和管理员确认
- `generate`：围绕 **确认后的 chunk** 批量生成，并对每条生成结果进行协作校对/审核
- `export`：导出一份包含原始 markdown、清洗后 markdown、chunk 结果、生成结果与审核信息的完整 bundle

本文档的原则是：**尽量复用现有数据模型和代码骨架，避免大拆大改；在关键节点增加版本对象、审核对象和分派对象。**

---

## 2. 当前仓库的基线判断

### 2.1 已经存在的主链路能力

当前仓库已经具备以下基础能力：

1. 后端路由骨架完整，已经挂载了 `auth / users / projects / documents / sections / tasks / monitoring / chunks / prompt_templates / candidates / curated_items / datasets / benchmarks / exports` 等模块。
2. `documents` 路由已经具备：
   - PDF 上传
   - 解析触发
   - `cleaning/start`
   - `chunk`
   - `generate-batch`
   - section/chunk 列表获取
3. PRD 中已经将当前平台的主链路定义为：
   `Document → ParseJob → Section → Chunk → Candidate → CuratedItem → Dataset / Benchmark / Export`

因此，这次改造不是推翻重做，而是：

- 保留已有主骨架
- 强化 `clean / chunk / generate / export` 四段的状态机、版本对象和协作机制
- 调整页面交互，使其更接近“实验室多人协作生产平台”

---

## 3. 目标方案的总体重构原则

### 3.1 总体原则

1. **不改 upload / parse 主流程**，只补足后续治理与协作层。
2. **Section 继续作为 clean 阶段的核心工作单元**。
3. **Chunk 不再只当临时中间结果，而是升级为可审核、可冻结的结果集（ChunkSet）**。
4. **Generate 不要求一个 chunk 只有一个答案**，但要求正式导出前有明确审核记录。
5. **导出不是只导最终结果，而是导出整个证据链 bundle**。
6. **优先采用兼容性改造**：尽量扩展现有 `Section / Chunk / Candidate / Export / SnapshotManifest`，少做破坏性重命名。

### 3.2 推荐的对象层次

推荐把 R1 新方案落成下面这套层次：

```text
Document
├── ParseJob
├── CleaningRound / CleaningJob
│   ├── Section
│   └── CleanedDocumentVersion
├── ChunkSet
│   └── Chunk
├── GenerationBatch
│   ├── GenerationRun
│   ├── Candidate (或 GeneratedItem)
│   └── ReviewRecord
└── ExportBundle / SnapshotManifest
```

---

## 4. 分阶段改造总览

建议分成 6 个阶段推进：

### Phase 0：方案冻结与兼容性设计

目标：先把数据模型、状态机、命名和兼容策略定下来，避免后面边写边改。

### Phase 1：Clean 工作流改造

目标：把现有 `section 清洗 + review` 升级成“管理员分派 + 全文终审 + 冻结 cleaned markdown version”。

### Phase 2：ChunkSet 工作流改造

目标：让 chunk 结果成为独立的、可回看、可审核、可冻结的版本化产物。

### Phase 3：Generate 工作流改造

目标：把“批量生成”升级成“选定 chunk 范围 + 多条候选知识 + 非作者审核”的协作链路。

### Phase 4：Export Bundle 改造

目标：导出原始 markdown、cleaned markdown、chunk json、生成结果、审核记录和 manifest。

### Phase 5：前端收口、任务可视化、测试补齐

目标：页面、任务进度、状态流转与验收标准完整闭环。

---

## 5. 详细改进清单（按模块）

# 5.1 数据模型与数据库迁移

## 5.1.1 Document 扩展

### 需要新增字段

建议在 `documents` 上增加：

- `clean_status`
  - `not_started / section_planned / in_progress / review_pending / completed`
- `active_clean_version_id`
- `active_chunk_set_id`
- `active_generation_batch_id`（可选）
- `final_export_ready`（布尔，可选）

### 目的

- 区分 parse 状态与 clean/chunk/generate 的业务状态
- 让一个文档可以明确指向当前被确认的 cleaned version 和 chunk set

---

## 5.1.2 Section 扩展

当前 `Section` 已经存在，建议保留并扩展，而不是重做。

### 需要新增字段

- `assignment_status`
  - `unassigned / assigned / in_progress / completed / returned`
- `assigned_to`
- `assigned_by`
- `assigned_at`
- `completed_at`
- `reviewed_by`
- `reviewed_at`
- `return_reason`
- `lock_required`（可选）
- `clean_round_id`（若要支持多轮清洗）

### 建议保留现有能力

- `raw_markdown`
- `cleaned_markdown`
- `status`
- `SectionRevision`
- `SectionComment`
- `SectionLease`

### 改造目标

把现有的“编辑 + 提交 + reviewer accept/reject”升级成：

- 管理员先分派 section
- 编辑者进入自己负责的 section 进行校对
- section 完成后标记 completed
- 全部 section 完成后，文档级进入 merged review

---

## 5.1.3 新增 CleanedDocumentVersion

### 建议新增表

`cleaned_document_versions`

### 推荐字段

- `id`
- `document_id`
- `source_cleaning_job_id`
- `merged_markdown`
- `section_count`
- `version`
- `status`
  - `draft / review_pending / accepted / rejected`
- `created_by`
- `reviewed_by`
- `reviewed_at`
- `created_at`
- `updated_at`
- `artifact_key`（对象存储中的 md 文件）

### 目的

- 解决“所有 section 完成后如何形成一份完整 cleaned markdown”的问题
- 让管理员终审不是直接操作原 section 集合，而是对**合并后的完整文本版本**做终审

---

## 5.1.4 新增 ChunkSet

### 建议新增表

`chunk_sets`

### 推荐字段

- `id`
- `document_id`
- `cleaned_document_version_id`
- `chunk_profile_id`
- `strategy`
- `config_json`
- `status`
  - `pending / processing / review_pending / completed / rejected`
- `total_chunks`
- `total_tokens`
- `summary_json`
- `artifact_key`（整套 chunk json 文件）
- `created_by`
- `reviewed_by`
- `reviewed_at`
- `created_at`
- `updated_at`

### 目的

- 把“分块结果”提升为一个可审核、可回溯的独立对象
- 让管理员审的是一整套结果，而不是单个 chunk

---

## 5.1.5 Chunk 扩展

当前已有 `chunks` 表，建议保留并挂到 `ChunkSet` 下。

### 需要新增字段

- `chunk_set_id`
- `source_section_ids`（数组或 json）
- `display_order`
- `content_type`
  - `text / table / formula / mixed`
- `meta_json`
  - 可放图表、公式、表格计数等摘要信息
- `is_selected_for_generation`
- `selection_note`（可选）

### 目的

- 让 chunk 可以归属于某个已确认的 chunk set
- 让前端可以基于 chunk set 展示列表、统计和筛选

---

## 5.1.6 新增 GenerationBatch

### 建议新增表

`generation_batches`

### 推荐字段

- `id`
- `document_id`
- `chunk_set_id`
- `model_config_id`
- `prompt_template_id`
- `selected_chunk_ids`
- `status`
  - `pending / processing / review_pending / completed / failed`
- `total_chunks`
- `completed_chunks`
- `summary_json`
- `created_by`
- `created_at`
- `updated_at`

### 目的

- 表达一次“勾选一些 chunk + 选模型 + 选模板 + 点 generate”的批处理操作
- 让 generate 工作台有明确的批次级对象

---

## 5.1.7 GenerationRun 的角色调整

当前已有 `GenerationRun`，建议继续保留，但语义明确为：

- `GenerationBatch` 是“批次级对象”
- `GenerationRun` 是“单 chunk 的一次 LLM 调用记录”

### 建议新增字段

- `generation_batch_id`
- `chunk_set_id`
- `requested_by`
- `thinking_text`（可选，若模型有推理输出）
- `answer_text` / `parsed_output_json`
- `collapsed_by_default`（前端控制可不必入库）

---

## 5.1.8 Candidate 扩展（兼容方案）

当前已有 `Candidate`。从你的目标看，它其实可以继续承担“chunk 生成的知识条目”角色。

### 需要新增字段

- `author_id`
- `source_generation_batch_id`
- `review_status`
  - `pending / review_pending / completed / rejected / needs_revision`
- `review_stage`
  - `peer_review / reviewer_review / admin_spot_check`
- `thinking_text`
- `answer_text`（若不想全放在 `content_json`）
- `is_supplemental`
- `supersedes_candidate_id`（可选）
- `finalized_at`

### 核心原则

- 一个 chunk 可以有多条 Candidate
- 同一 chunk 不要求唯一答案
- 但进入正式导出前，Candidate 必须有清晰的审核状态

---

## 5.1.9 新增 ReviewRecord

### 建议新增表

`review_records`

### 推荐字段

- `id`
- `entity_type`
  - `candidate / chunk_set / cleaned_document_version`
- `entity_id`
- `reviewer_id`
- `action`
  - `approve / reject / needs_revision / agree / disagree`
- `reason`
- `comment`
- `created_at`

### 目的

- 把你提出的“同意/不同意，不同意需要说明原因”从普通 comment 升级为结构化审核记录
- 支持后续统计、回溯和导出

---

## 5.1.10 Export / SnapshotManifest 扩展

当前已有 `Export` 和 `SnapshotManifest`，建议保留。

### 需要扩展的内容

让 manifest 覆盖：

- 原始 markdown 路径
- cleaned markdown version 路径
- chunk set json 路径
- 生成结果 jsonl / json 路径
- review record 路径
- 采用的模型版本、提示词模板版本、chunk profile 版本
- 通过审核的 candidate 数量与统计

---

# 5.2 后端接口与服务改造

## 5.2.1 documents router 改造

### 需要新增/调整的接口

#### clean 相关
- `POST /api/documents/{did}/cleaning/plan`
  - 从 parse 结果生成初始 sections
- `POST /api/documents/{did}/cleaning/assign`
  - 批量分派 section 给不同账号
- `POST /api/documents/{did}/cleaning/merge`
  - 生成 merged cleaned markdown preview/version
- `POST /api/documents/{did}/cleaning/final-review`
  - 管理员 accept/reject 整份 cleaned version

#### chunk 相关
- `POST /api/documents/{did}/chunk-sets`
  - 创建一个新的 chunk set
- `GET /api/documents/{did}/chunk-sets`
- `GET /api/chunk-sets/{id}`
- `POST /api/chunk-sets/{id}/review`
  - accept/reject 整套 chunk set

#### generate 相关
- `POST /api/documents/{did}/generation-batches`
  - 基于选中的 chunk 启动生成
- `GET /api/documents/{did}/generation-batches`
- `GET /api/generation-batches/{id}`

### 原有接口建议保留但调整语义

- `/cleaning/start`
  - 从“开始清洗”收敛为“创建 section 初稿 / 创建 cleaning round”
- `/chunk`
  - 逐步被 `/chunk-sets` 替代
- `/generate-batch`
  - 逐步被 `/generation-batches` 替代

---

## 5.2.2 section_service / sections router 改造

### 需要新增能力

- 分派 section
- 变更 assignee
- 查询某用户待处理 section 列表
- 完成 section
- 退回 section
- section 完成率统计
- merged preview 前校验：是否全部 completed

### 必做的约束

- 编辑 section 必须持有 lease 或为被分配人
- reviewer/admin 可强制接管
- 非管理员不能修改他人 section 分派关系

---

## 5.2.3 chunk_service / chunks router 改造

### 需要新增能力

- 依据 `CleanedDocumentVersion` 创建 `ChunkSet`
- 在生成 chunk set 时实时更新进度
- 输出 chunk 统计信息
- 支持 chunk 列表过滤：
  - token 数区间
  - 来源 section
  - 内容类型
- 管理员确认 chunk set

### 需要输出的统计

- 总 chunk 数
- 总 token 数
- 平均 token 数
- 最大 / 最小 token 数
- 按 section 的 chunk 数分布
- 按 content_type 的分布

---

## 5.2.4 generate_worker / candidate_service 改造

### 需要新增能力

- 生成时支持“选中的 chunk 列表”而不是默认全量 ready chunk
- 记录每个 chunk 的生成结果和进度
- 若模型返回思考过程，则存储但前端默认折叠
- 允许一个 chunk 多条候选知识
- 支持生成后进入 review_pending

### 推荐的协作规则

- 管理员负责分配 chunk 范围或发起批次
- 编辑者负责生成和修正
- 正式通过必须由**非作者**审核

### 推荐的审核动作

- `approve`
- `reject`
- `needs_revision`

不建议 R1 让管理员审核所有生成条目；管理员更适合抽查和设置导出门槛。

---

## 5.2.5 export_service / export_worker 改造

### 导出内容建议

导出一个 bundle：

```text
export_bundle.zip
├── manifest.json
├── source/
│   ├── raw_markdown.md
│   ├── cleaned_markdown_v1.md
│   └── document_meta.json
├── chunks/
│   └── chunk_set_v1.json
├── generation/
│   ├── candidates.jsonl
│   ├── reviewed_candidates.jsonl
│   └── review_records.jsonl
└── summary/
    └── stats.json
```

### manifest 至少包含

- document id / sha256
- parser profile / parser version
- cleaned version id
- chunk profile / chunk set id
- model config / prompt template version
- 导出时间 / 导出人
- 候选条目总数 / 已通过数 / 驳回数

---

# 5.3 Worker 与任务中心改造

## 5.3.1 Task 类型扩展

建议在 `TaskType` 中增加或明确：

- `clean_plan`
- `clean_merge`
- `chunk_set_create`
- `generation_batch`
- `export_bundle`

## 5.3.2 进度更新粒度

### clean 阶段
- 已分派 section 数 / 总 section 数
- 已完成 section 数 / 总 section 数

### chunk 阶段
- 已处理 section 数 / 总 section 数
- 已生成 chunk 数 / 预估 chunk 数

### generate 阶段
- 已生成 chunk 数 / 选中 chunk 数
- 已审核 candidate 数 / 待审 candidate 数

### export 阶段
- 已打包文件数 / 总文件数

## 5.3.3 WebSocket 推送新增事件

建议新增：

- `section.assigned`
- `section.completed`
- `clean.version.created`
- `chunk_set.created`
- `chunk_set.reviewed`
- `generation_batch.created`
- `candidate.reviewed`
- `export.bundle.ready`

---

# 5.4 前端页面与交互改造

## 5.4.1 Clean 工作台改造

### 目标页面
`/projects/[id]/documents/[did]/clean`

### 需要新增的 UI 分区

1. **section 规划与分派侧栏**
   - section 列表
   - 合并/拆分操作
   - assignee 下拉选择
   - 状态筛选（unassigned / assigned / in_progress / completed）

2. **清洗主工作区**
   - 左：PDF 原文
   - 中左：raw markdown
   - 中右：cleaned markdown 编辑器
   - 右：预览 + 评论 + 完成按钮

3. **文档级终审区**
   - 所有 section 完成后显示 merged cleaned markdown preview
   - 管理员点击 accept/reject

### 需要新增的页面能力

- section 批量分派
- 我的 section 快速筛选
- section 完成率进度条
- merged preview 自动生成
- 文档级终审按钮

---

## 5.4.2 Chunk 工作台改造

### 目标页面
建议新增：
`/projects/[id]/documents/[did]/chunks/workbench`

### 需要新增的 UI 分区

1. **左侧配置区**
   - 选择 cleaned version
   - 选择 chunk strategy / chunk profile
   - 参数表单
   - 开始分块按钮

2. **中间结果区**
   - 分块进度
   - 统计卡片
   - chunk 数、总 token 数、平均 token 数

3. **右侧列表/详情区**
   - chunk 列表
   - 点击查看 chunk 内容
   - 来源 section / 页码 / heading path
   - token 数

4. **管理员确认区**
   - accept/reject 整个 chunk set

---

## 5.4.3 Generate 工作台改造

### 目标页面
建议新增：
`/projects/[id]/documents/[did]/generate`

### 需要新增的 UI 分区

1. **配置区**
   - 选择 chunk set
   - 选择模型
   - 选择提示词模板
   - 批量勾选 chunk
   - 启动 generate

2. **批次区**
   - generation batch 列表
   - 进度条
   - 失败/成功统计

3. **结果详情区**
   - chunk 原文
   - LLM 输出答案
   - 若有思考过程，默认折叠
   - 支持多条 candidate 切换

4. **审核区**
   - 同意 / 不同意 / 需修改
   - 说明原因
   - 评论流

### 必须实现的交互规则

- 一个 chunk 可对应多条 candidate
- 同一用户不能直接审核自己生成的 candidate
- 审核通过后 candidate 状态进入 completed
- export 默认只导出 completed candidate

---

## 5.4.4 Export 页面改造

### 目标页面
`/projects/[id]/exports`
或
`/projects/[id]/documents/[did]/export`

### 需要展示

- raw markdown 版本
- cleaned markdown 版本
- chunk set 版本
- generation batch 版本
- 可导出的 candidate 统计
- manifest 预览
- bundle 下载按钮

---

# 5.5 权限与协作规则改造

## 5.5.1 推荐角色职责

### admin
- 分配 section
- 审核 cleaned document version
- 触发/确认 chunk set
- 分配或管理 generation batch
- 控制最终导出

### editor
- 清洗 assigned sections
- 生成 chunk 对应知识条目
- 修正 needs_revision 的结果
- 评论他人内容

### reviewer
- 审核 candidate
- approve / reject / needs_revision
- 不负责项目配置和最终导出

## 5.5.2 协作规则建议

1. `clean`：管理员分派 section，编辑者逐 section 完成。
2. `chunk`：不做多人并行编辑，只做管理员确认整套结果。
3. `generate`：允许一对多 candidate，但正式通过必须非作者审核。
4. `export`：管理员控制导出门槛。

---

# 5.6 兼容性与迁移策略

## 5.6.1 建议采用“扩展而非重命名”

### 保留并扩展
- `Section`
- `Chunk`
- `GenerationRun`
- `Candidate`
- `Export`
- `SnapshotManifest`

### 新增对象
- `CleanedDocumentVersion`
- `ChunkSet`
- `GenerationBatch`
- `ReviewRecord`

### 不建议现在就做的大动作
- 不建议把 `Candidate` 全面重命名为 `GeneratedItem`
- 不建议把 `Chunk` 从数据库中移除，只留 json 文件
- 不建议用“所有人都能自由生成并自动并入导出”的开放策略

## 5.6.2 数据迁移思路

### 对已有文档
- 已有 section：补默认 `assignment_status`
- 已有 chunk：可以自动归入一个历史 `ChunkSet`
- 已有 candidate：补默认 `review_status`
- 已有 export：manifest 增加兼容字段，不要求回填全部历史 bundle

---

# 5.7 测试与验收改造

## 5.7.1 后端测试

至少补齐以下测试：

1. parse -> clean plan -> section assign -> section complete -> merge -> final review
2. cleaned version -> chunk set create -> review accept
3. chunk set -> generation batch -> candidate create -> reviewer approve/reject
4. export bundle -> manifest 完整性校验
5. 非作者审核约束测试
6. lease 冲突与强制接管测试

## 5.7.2 前端联调测试

至少覆盖：

1. 管理员分派 section
2. 编辑者只能看到/处理自己分配的 section
3. 全部 section 完成后自动出现 merged preview
4. chunk 工作台可看到统计和详情
5. generate 工作台支持批量勾选 chunk
6. candidate 审核动作能正确改变状态
7. export 下载到完整 bundle

## 5.7.3 R1 验收标准

### clean 验收
- 能自动切出 section
- 管理员能分派
- section 能多人并行清洗
- 全部完成后能自动合并
- 管理员能终审整篇 cleaned markdown

### chunk 验收
- 能选择 chunk 策略并生成 chunk set
- 能显示进度与统计
- 能查看 chunk 详情
- 管理员能确认 chunk set

### generate 验收
- 能选择模型与模板
- 能勾选 chunk 批量生成
- 能查看 chunk 原文与生成内容
- 能进行非作者审核
- 通过后的 candidate 状态正确

### export 验收
- 能导出 raw markdown、cleaned markdown、chunk json、candidate 结果、review 记录、manifest

---

## 6. 推荐实施顺序（按优先级）

## P0：方案冻结（1 次设计迭代）

- [ ] 明确对象命名：是否采用 `CleanedDocumentVersion / ChunkSet / GenerationBatch / ReviewRecord`
- [ ] 明确 `Candidate` 是否继续沿用现名
- [ ] 明确 generate 的审核门槛：是否强制“非作者审核”
- [ ] 明确导出默认只导 `completed candidate`

## P1：数据库与模型（优先）

- [ ] 为 `documents` 增加 clean/chunk/generate 业务状态字段
- [ ] 为 `sections` 增加分派字段和完成字段
- [ ] 新增 `cleaned_document_versions`
- [ ] 新增 `chunk_sets`
- [ ] 为 `chunks` 增加 `chunk_set_id`
- [ ] 新增 `generation_batches`
- [ ] 为 `generation_runs` 增加 `generation_batch_id`
- [ ] 为 `candidates` 增加审核状态、作者、思考内容等字段
- [ ] 新增 `review_records`
- [ ] 扩展 `snapshot_manifests`

## P2：Clean 工作流（优先）

- [ ] 新增 clean planning 接口
- [ ] 新增 section 分派接口
- [ ] 新增 section 完成接口
- [ ] 新增 merged cleaned markdown 生成接口
- [ ] 新增文档级终审接口
- [ ] 更新 section lease 逻辑，和分派逻辑联动

## P3：Chunk 工作流（优先）

- [ ] 新增 chunk set 创建接口
- [ ] 新增 chunk set 查询接口
- [ ] 新增 chunk set 审核接口
- [ ] 让 worker 输出 chunk 统计和对象存储 json 文件
- [ ] 给任务中心增加 chunk set 进度展示

## P4：Generate 工作流（核心）

- [ ] 新增 generation batch 创建接口
- [ ] 支持勾选 chunk 批量生成
- [ ] 允许一个 chunk 多条 candidate
- [ ] 记录 thinking output 并前端折叠显示
- [ ] 新增结构化审核记录
- [ ] 增加“非作者不可自审”约束
- [ ] 支持 `approve / reject / needs_revision`

## P5：Export Bundle（核心）

- [ ] 重新设计 export bundle 文件结构
- [ ] 导出 raw markdown / cleaned markdown / chunk set / candidate / reviews / manifest
- [ ] 支持 bundle 下载
- [ ] manifest 覆盖版本链

## P6：前端与联调（收口）

- [ ] clean 工作台改成“分派 + 清洗 + 文档终审”模式
- [ ] 新增 chunk 工作台
- [ ] 新增 generate 工作台
- [ ] export 页面支持 bundle 预览和下载
- [ ] 任务中心加入分阶段统计与进度
- [ ] 全链路联调和回归测试

---

## 7. 建议的代码改造落点（按目录）

## 7.1 后端 models

- [ ] `apps/api/app/models/document.py`
- [ ] `apps/api/app/models/section.py`
- [ ] `apps/api/app/models/chunk.py`
- [ ] `apps/api/app/models/generation.py`
- [ ] `apps/api/app/models/export.py`
- [ ] 新增 `apps/api/app/models/cleaned_document_version.py`（或并入现有文件）
- [ ] 新增 `apps/api/app/models/chunk_set.py`
- [ ] 新增 `apps/api/app/models/review_record.py`

## 7.2 后端 schemas

- [ ] `document.py`
- [ ] `section.py`
- [ ] `chunk.py`
- [ ] `candidate.py`
- [ ] `export.py`
- [ ] 新增 `chunk_set.py`
- [ ] 新增 `generation_batch.py`
- [ ] 新增 `review_record.py`

## 7.3 后端 services

- [ ] `document_service.py`
- [ ] `section_service.py`
- [ ] `chunk_service.py`
- [ ] `candidate_service.py`
- [ ] `export_service.py`
- [ ] `task_service.py`
- [ ] 新增 `clean_version_service.py`
- [ ] 新增 `generation_batch_service.py`

## 7.4 后端 routers

- [ ] `documents.py`
- [ ] `sections.py`
- [ ] `chunks.py`
- [ ] `candidates.py`
- [ ] `exports.py`
- [ ] 视情况新增：
  - [ ] `chunk_sets.py`
  - [ ] `generation_batches.py`

## 7.5 workers

- [ ] `clean_worker.py`
- [ ] `chunk_worker.py`
- [ ] `generate_worker.py`
- [ ] `export_worker.py`

## 7.6 前端 pages / components

- [ ] `projects/[id]/documents/[did]/clean/page.tsx`
- [ ] 新增 `projects/[id]/documents/[did]/chunks/workbench/page.tsx`
- [ ] 新增 `projects/[id]/documents/[did]/generate/page.tsx`
- [ ] `projects/[id]/exports/page.tsx`
- [ ] `task-floating-panel.tsx`
- [ ] `status-badge.tsx`
- [ ] 新增 chunk 统计与 candidate 审核组件

---

## 8. 最终建议

如果目标是“尽快把仓库拉到你现在定义的新 R1 方案”，最优路线不是全面翻修，而是：

1. **复用现有 Section/Chunk/Candidate 主链路**
2. **补上 4 个关键对象：CleanedDocumentVersion / ChunkSet / GenerationBatch / ReviewRecord**
3. **把 clean 做成分派制，把 chunk 做成结果集确认制，把 generate 做成条目级协作审核制**
4. **最后用 export bundle 把整条链路导出来**

一句话概括：

**当前仓库已经有主链路骨架，下一步最重要的不是继续“加模块”，而是把 `clean → chunk → generate → export` 四段从“能跑”升级成“可治理、可协作、可冻结、可导出”。**

