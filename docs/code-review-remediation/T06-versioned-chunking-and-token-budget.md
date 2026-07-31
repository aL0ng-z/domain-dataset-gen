# T06：版本化切分与 Token 预算

- 状态：待实施
- 优先级：P1
- 建议规模：L
- 直接依赖：T02、T04、T05、T07
- 可并行：splitter/token 单元修复可提前进行；持久任务与发布链必须等待依赖合并

## 1. 目标

把每次切分建模为可追溯、不可覆盖的 `ChunkSet`：它必须绑定一个已接受的 `CleanedDocumentVersion` 和一份冻结的切分配置。重跑产生新版本并原子切换 `Document.active_chunk_set_id`，历史 Chunk 保留且默认查询不会混入。任一输出 Chunk（包含 overlap 和分隔符）都必须满足配置的 `max_tokens`。

## 2. 范围

1. 补全 `ChunkSet` 的版本、输入/输出 hash、配置快照、失败状态和幂等标识。
2. 所有新 Chunk 强制绑定一个 ChunkSet，并在集合内使用唯一 ordinal。
3. 切分任务仅消费 T05 已接受且仍为 Document active 的清洗版本，不再从多批 Section 猜测来源，也不允许旧版本重新成为 active 链路来源。
4. 切分成功后在事务中发布集合并切换 active pointer；失败保留旧 active set。
5. 修复 splitter 对超长段落和 overlap 后结果超预算的问题。
6. 默认 Chunk 列表、详情与后续生成入口明确展示/使用所属 ChunkSet。
7. 对重复点击、并发发起、worker 中途失败和历史孤儿 Chunk 制定确定行为。
8. 切分只通过 T07 持久 dispatcher 执行，并完整接入其 attempt、retry、cancel、run-token fencing 与崩溃恢复语义。

## 3. 明确不做

- 不实现新的语义切分、向量聚类、Embedding 或自动参数优化策略。
- 不修复生成链路；T08 负责让 GenerationBatch 消费 active ChunkSet。
- 不删除旧 ChunkSet、旧 Chunk 或其 Candidate/GenerationRun 引用。
- 不提供已完成 ChunkSet 的原地编辑；如需人工调整，应另行设计派生版本。
- 不声称能够还原迁移前孤儿 Chunk 的真实切分批次和配置。

## 4. 数据库合同

### 4.1 ChunkSet

在现有 `chunk_sets` 上新增或收紧以下字段：

- `version INTEGER NOT NULL`，唯一约束 `(document_id, version)`。
- `is_legacy BOOLEAN NOT NULL DEFAULT false`。
- `idempotency_key VARCHAR(100)`，非空的新记录唯一约束 `(document_id, idempotency_key)`。
- `source_sha256 CHAR(64)`：对规范化后的 `merged_markdown` 计算。
- `output_sha256 CHAR(64)`：按 ordinal 串联 canonical Chunk 记录后计算。
- `splitter_version VARCHAR(100)`：代码/算法版本，不得只写策略名。
- `error_message TEXT`、`completed_at TIMESTAMPTZ`、`task_id UUID UNIQUE REFERENCES tasks(id)`；`task_id` 表示首次派发 Task，对所有 `is_legacy=false` 记录必填，T07 人工重试通过 `retry_of_task_id` 链追溯而不覆盖它。
- `cleaned_document_version_id`、`chunk_profile_id`、`strategy`、`config_json`、`task_id` 对所有 `is_legacy=false` 记录非空；用 CHECK 表达该条件。
- `config_json` 必须冻结 `strategy/max_tokens/overlap_tokens/options/tokenizer_name/tokenizer_version/splitter_version`，后续 Profile 修改不改变历史集合。
- `chunk_set_status` 增加 `failed/cancelled`；completed/rejected 不可变，failed 只允许由 T07 有效 retry run token 转回 pending，cancelled 需以新请求/key 创建新 set。
- 部分唯一索引保证同一 Document 最多一个 `pending/processing` 集合，避免并发完成时“最后写入者获胜”。

版本号在锁定 `documents` 行后分配，不允许用无锁的 `MAX(version)+1`。所有 ChunkSet 的来源/config/task 关联均禁止通用 PATCH；completed/rejected 的输出不可修改，failed 重试只能复用原冻结输入并由 T07 fenced handler 执行。

### 4.2 Chunk 与配置约束

- `chunks.chunk_set_id` 对迁移后的所有记录为 `NOT NULL`，FK 使用 RESTRICT/默认限制删除。
- 唯一约束 `(chunk_set_id, ordinal)`；`ordinal >= 0`、`token_count > 0`。
- 新增索引 `(chunk_set_id, ordinal)` 和 `(document_id, chunk_set_id)`。
- `chunk_profiles` 增加 DB CHECK：`max_tokens > 0`、`overlap_tokens >= 0`、`overlap_tokens < max_tokens`；Pydantic 使用同一规则。
- worker 写入前后均用快照指定的 tokenizer 复算 `token_count`，任何 `token_count > max_tokens` 都使整个集合失败，不能截断后静默发布。

### 4.3 迁移与回填

1. 按 `created_at, id` 为现有 ChunkSet 在各 Document 内分配稳定 version；无法明确绑定 T07 持久 Task 的迁移前记录一律置 `is_legacy=true`，不得用猜测的 task_id 通过新 CHECK。
2. 对 `chunk_set_id IS NULL` 的 Chunk，按 Document 创建一个 `is_legacy=true` 的隔离集合；按 `created_at, id` 稳定重排 ordinal 后绑定，Chunk id 不变，既有下游 FK 不受影响。
3. 迁移生成的 legacy set 不自动声称具有 cleaned version/profile/hash；`summary_json` 写入 `provenance=legacy_unverified` 和迁移计数。
4. 若 `Document.active_chunk_set_id` 已合法则保留；若为空或指向异文档，不猜测最新批次，清空非法指针并要求重新切分。
5. 回填完成并验证零 NULL 后，才收紧 `chunks.chunk_set_id` 和唯一约束。
6. downgrade 必须先确认不存在无法表示于旧 enum 的 `failed/cancelled` 集合；否则停止。回滚不得删除历史 Chunk，仅移除新增约束/列并把关联保留为旧 nullable 结构。

## 5. API 合同

- `POST /projects/{pid}/documents/{did}/chunk` 请求体：`{chunk_profile_id, cleaned_version_id?}`，请求头必须含 `Idempotency-Key`。
- `cleaned_version_id` 省略时取 `Document.active_clean_version_id`；显式提供时也必须等于 active id。没有 active version、版本非 `accepted` 或不属于该文档时返回 `409 CLEAN_VERSION_NOT_READY`，旧于/不同于 active 时返回 `409 CLEAN_VERSION_STALE`。
- 首次接受返回 `202 {task_id, chunk_set_id, reused:false, status:"pending"}`；相同 key/相同规范请求重放返回同一对象并置 `reused:true`，不得新建 Chunk。
- API 在一个数据库事务中创建 ChunkSet 与 T07 queued Task，Task handler/payload version 固定且 payload 明确携带 `chunk_set_id`；禁止 `BackgroundTasks` 或请求内直接运行 splitter。
- 相同 key 但请求摘要不同返回 `409 IDEMPOTENCY_KEY_REUSED`；另一个不同 key 的切分正在执行时返回 `409 CHUNK_RUN_IN_PROGRESS`。
- failed set 不因相同 key 自动重跑；对其 Task 调用 T07 retry 会创建真实后继 Task并复用同一 ChunkSet/冻结配置，handler 用新 run token 将 failed CAS 回 pending。若用户要一次独立重切分，则使用新 key 创建新 version。
- queued cancel 通过 T07 handler-specific cancel hook 令 Task 与 ChunkSet 原子变 cancelled 且 handler 不执行；processing cancel 进入 cancelling，handler 在写 Chunk 和切 active pointer 前 checkpoint，回滚/清除 staging Chunk 后令 set 为 cancelled。自动 retry attempt 不得创建第二个 set。
- `GET /projects/{pid}/documents/{did}/chunk-sets` 返回分页版本历史，含 version、source/profile、status、统计、hash、是否 active 和失败原因。
- `GET /projects/{pid}/chunk-sets/{csid}` 返回冻结配置和汇总；对象归属遵循 T02。
- `GET /projects/{pid}/documents/{did}/chunks` 默认限定 `active_chunk_set_id`；可显式传 `chunk_set_id` 查看历史，响应项新增 `chunk_set_id`。
- `GET /chunks/{cid}` 响应新增 `chunk_set_id` 和 `chunk_set_version`；已完成集合上的 `PATCH` 返回 `409 CHUNK_SET_IMMUTABLE`。

发布前必须再次锁定 Document 并确认 `active_clean_version_id` 仍等于该 set 的来源；T05 终审已推进时本次切分以 `CLEAN_VERSION_STALE` 失败。失败/取消时旧 `active_chunk_set_id` 保持不变：有旧 active set 则文档仍可显示 `chunked`，没有则回到与 active clean version 对应的 `cleaned`，不能长期停在 `chunking`。

## 6. 前端合同

- 发起切分时选择或明确展示清洗版本、ChunkProfile 及 `max_tokens/overlap_tokens`；一次用户操作生成并复用一个 idempotency key，网络重试不得换 key。
- 切分按钮在该文档已有 pending/processing set 时禁用，并显示对应任务；409 后刷新服务端状态。
- Chunk 页面默认只展示 active set，页头显示 set version、来源 clean version、配置快照、数量、总 token 和 hash。
- 可切换历史 set 只读查看；不得把多个 set 的同 ordinal Chunk 混在一张表。
- 新 set 失败时展示失败原因并继续标识旧 active set；不得清空旧 Chunk 列表。
- Task 为 queued/processing/cancelling 时显示 T07 状态和 cancel；failed 时 retry 操作使用返回的新 Task，仍关联同一 ChunkSet version。
- 完成态 Chunk 不显示可保存的原地编辑入口；如保留详情编辑器，必须只读并说明“切分版本不可变”。

## 7. Token 预算合同

- 计数器由配置快照标识，并同时用于拆分、overlap 和最终校验。
- overlap 是总预算的一部分：先确定实际 overlap token 数，再以 `max_tokens - overlap - separator_tokens` 作为当前正文预算。
- `_merge_splits` 遇到单个 piece 超限时必须继续递归或 hard split，不能直接追加超限 piece。
- hard split 后仍须经过 overlap-aware 最终校验；不得生成空 Chunk、仅 overlap Chunk或 Unicode 损坏内容。
- 同一输入、配置和 splitter version 必须产生相同顺序、内容、token_count 与 `output_sha256`。

## 8. 预期修改面

- `apps/api/app/models/chunk_set.py`、`chunk.py`、`document.py`、`config.py`
- `apps/api/app/schemas/document.py`、`chunk.py` 及新增 ChunkSet schema
- `apps/api/app/workers/chunk_worker.py`、`services/chunk_service.py`
- T07 handler registry/ExecutionContext 中的 versioned chunk handler；删除对应 `BackgroundTasks` 调度
- `apps/api/app/routers/documents.py`、`routers/chunks.py` 及 ChunkSet 路由
- `libs/splitters/splitters/base.py`、`hybrid_heading.py`
- `apps/api/migrations/versions/` 新增迁移
- 文档详情、Chunk 列表/详情页面与生成 API 类型
- 后端单元/集成测试、前端合同/组件测试、`docs/logs/dev-log.md`

## 9. 依赖和风险

- 依赖 T02 的跨项目资源校验、T04 的 OpenAPI 类型与错误合同、T05 的 accepted/active CleanedDocumentVersion 单调终审语义，以及 T07 的持久 dispatcher、retry/cancel 和 fencing；测试复用 T00 fixture。
- legacy Chunk 可能混合多次历史运行，只能诚实标为 `legacy_unverified`，不能伪造溯源。
- PostgreSQL enum downgrade 复杂；迁移需提供带数据预检的可执行回滚路径。
- tokenizer 或库升级可能改变计数；必须冻结名称和版本，并用 golden cases 防漂移。
- 大文档全量 staging 会增加事务与内存压力；允许分批写临时状态，但发布 active pointer 必须原子，失败不得对默认查询可见。
- Task 与 ChunkSet 状态可能在崩溃窗口分叉；所有转换必须由同一 handler/run token 事务或可重放协调器收敛，UI 以两者明确映射而非猜测完成。
- 切分期间 T05 可能接受更新 clean version；发布事务重锁 Document 并比较 source id，旧计算只可失败，不能覆盖新 active 链路。

## 10. 实施步骤

1. 决定 splitter/tokenizer 版本标识和 canonical hash 格式并写 golden fixture。
2. 编写迁移、legacy 回填、约束预检与 downgrade。
3. 修复 splitter 的递归、hard split 和 overlap-aware 预算。
4. 在一次事务中创建带必填 `task_id` 的 ChunkSet 与 T07 Task，注册 versioned handler，移除请求内 BackgroundTasks。
5. handler 按冻结输入执行切分，在 checkpoint 后校验统计/hash，再以 run token 原子发布和切 active pointer。
6. 接入 T07 automatic/manual retry、queued/processing cancel 和崩溃恢复的 ChunkSet 状态收敛。
7. 更新列表/详情/API 幂等行为、completed set 不可变门禁和前端任务操作。
8. 跑迁移往返、并发、故障注入、属性/边界测试和全量门禁。

## 11. 自动化验收标准

1. 对中文、英文、Markdown 标题、超长无换行文本和多字节字符，所有非空 Chunk 均满足实际计数 `1..max_tokens`。
2. 开启 overlap 后仍无超限；`overlap_tokens=0`、`max_tokens=1`、接近边界均有测试。
3. 同一输入/config/version 连跑两次，输出内容、ordinal、token_count 和 hash 完全一致。
4. 相同 idempotency key 并发请求只创建一个 ChunkSet/Task；不同 key 并发时只允许一个活跃切分。
5. 连续两次显式切分得到两个 set，各自 ordinal 从 0 开始且唯一；默认 API 只返回 active set。
6. worker 在第 N 个 Chunk 故障时，新 set 为 failed、没有部分 active 结果，旧 active pointer 与默认列表不变。
7. Profile 后续修改不改变历史 `config_json`、hash 或历史 API 响应。
8. legacy 回填后 `chunks.chunk_set_id` 无 NULL、下游 Chunk FK 仍有效、非法 active pointer 未被猜测填充。
9. 每个新非 legacy ChunkSet 均有唯一有效 `task_id`，Task payload 指向该 set；API 进程在返回 202 后退出，T07 runner 仍可完成并发布。
10. transient failure 的 T07 automatic retry 不新建 set；failed Task 的人工 retry 真实执行同一冻结 set，成功后只有一组 Chunk 和一个 active pointer。
11. queued/processing cancel 均不发布 active set；迟到旧 run token 无法写 Chunk、改 set 状态或切换 pointer，既有 active set 保持不变。
12. 切分期间 T05 接受了更新 clean version 时，旧来源 set 返回 `CLEAN_VERSION_STALE` 且不切 active pointer；请求显式传历史 accepted version 也被拒绝。
13. Alembic 往返、pytest、Ruff、前端测试、TypeScript、ESLint 和 build 全部返回 0。

## 12. 停止条件

- 发现 legacy Chunk 的重新编号会破坏外部以 ordinal 为稳定标识的未建模消费者。
- 存在非法 ChunkProfile（如 overlap >= max）且产品未决定修正、禁用或保留策略。
- 产品要求同一 Document 并发发布多个 active set，需先定义 active pointer 冲突规则。
- tokenizer 无法固定版本或生产与测试环境计数器不同。
- 必须删除历史 Chunk/Candidate 才能完成迁移；停止并制定独立数据修复方案。
- T05 尚不能保证 active CleanedDocumentVersion 单调且 accepted，或 T07 没有可用的持久派发/run-token checkpoint；不得以临时 BackgroundTasks 绕过依赖。

## 13. 审查重点

- source clean version 和 config 是否真正冻结，是否仍从“当前 Section/Profile”读取历史结果。
- active pointer 是否只在完整校验后一次切换，失败是否保留旧版本。
- 唯一约束、幂等 key 和并发 active-set 约束是否由数据库兜底。
- 最终 token 校验是否包含 overlap、前缀和分隔符。
- 默认查询和生成入口是否可能混入 legacy/历史 set。
- 新 ChunkSet 是否强制绑定 T07 origin Task，retry/cancel/崩溃恢复是否复用冻结 set 且由有效 run token 才能发布。

## 14. 完成定义

- 新切分链路完整建立 `CleanedDocumentVersion -> ChunkSet -> Chunk` 溯源，且历史集合不可覆盖。
- 重跑、重复请求、并发请求和中途失败均满足上述确定语义。
- 切分已完全由 T07 dispatcher 执行，Task/ChunkSet 在 retry、cancel 和 worker 崩溃后可确定收敛。
- 所有自动化验收通过，迁移与 rollback 文档可执行。
- `docs/logs/dev-log.md` 已用中文记录实现、数据回填数量和验证结果。
