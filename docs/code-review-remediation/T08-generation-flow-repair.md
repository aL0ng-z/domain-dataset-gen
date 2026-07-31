# T08：生成链路修复

- 状态：待实施
- 优先级：P0-Functional
- 建议规模：L
- 直接依赖：T04、T06、T07
- 被依赖：T09、T11

## 1. 目标与背景

恢复单 Chunk 与文档批量生成，使请求、任务、GenerationBatch、GenerationRun、Candidate 和 LLM 用量记录形成一条可查询的链。任何子项失败或取消都必须反映到父任务和批次，Document/Chunk 不得永久停在 `generating`。

## 2. 范围

1. 映射并实际使用已有 `generation_batches` 表；每次单项或批量请求都先持久化批次输入。
2. 修复单项路由导入不存在的 `run_generate`、重复创建 GenerationRun 以及参数形态不一致的问题。
3. 批量生成只读取 T06 的 active ChunkSet，支持全部 ready chunks 或请求指定子集。
4. 将父/子 Task、GenerationRun、Candidate、usage log 与 GenerationBatch 关联并聚合真实终态。
5. 在 Batch/Run 创建时冻结 PromptTemplateVersion、模板快照、非秘密 ModelConfig 快照、renderer 版本和渲染后 prompt hash，配置后续修改不得改变在途或历史生成。
6. failed/cancelled Batch 与 Run 保持不可变；retry 创建新的 Task、GenerationBatch 和 Run，只选择上一批次未成功的 Chunk。
7. 单项和批量页面完整选择 PromptTemplate 与 ModelConfig，异步跟踪任务、retry 派生链并展示结果/失败明细。
8. 使用 fake LLM 覆盖成功、非法 JSON、部分失败、取消、配置漂移和重试后的状态一致性。

## 3. 明确不做

- 不实现新的切分算法、重建 ChunkSet 或修改 token 上限；由 T06 负责。
- 不重写通用任务派发、retry/cancel 基础设施；使用 T07 提供的状态机和 dispatcher。
- 不审核 Candidate、不定义证据、不提升 CuratedItem；由 T09 负责。
- 不实现真实供应商模型质量评估、并发配额调度、成本预算或流式 token UI。
- 不把思维链作为必需输出；`thinking_text` 不从供应商私有推理字段复制，默认保持 `null`。
- 不把 API key、Authorization header、credential value 或 `extra_params` 中的秘密写入快照、Task payload、hash 输入或 API 响应。

## 4. 数据库合同

- 为已有 `generation_batches` 增加 ORM model；新写入必须有 `document_id`、`chunk_set_id`、`model_config_id`、`prompt_template_id`、非空 `selected_chunk_ids`、`created_by`。
- `selected_chunk_ids` 是按 ordinal 排序、去重后的 UUID 字符串数组，创建后不可修改；必须全部属于同一 active ChunkSet。
- GenerationBatch 新增 `retry_of_generation_batch_id UUID NULL REFERENCES generation_batches(id)`；该 FK 使用 `RESTRICT`，并以唯一约束保证一条 batch 最多有一个直接 retry 后继，后续失败继续从最新后继形成线性链。
- GenerationBatch 新增并冻结 `prompt_template_version_id`、`prompt_template_snapshot JSONB`、`prompt_template_sha256 CHAR(64)`、`model_config_snapshot JSONB`、`model_config_sha256 CHAR(64)` 和 `renderer_version VARCHAR(100)`；所有非 legacy 新记录均非空。
- GenerationBatch 与 GenerationRun 均新增 `is_legacy BOOLEAN NOT NULL DEFAULT false`、`provenance_status VARCHAR(30) NOT NULL` 和 `provenance_error_code VARCHAR(80) NULL`；`provenance_status` 只允许 `verified|legacy_unavailable|invalid`，上述字段创建后不可通过业务 API 修改。
- `prompt_template_version_id` 必须指向所选模板的精确当前版本；若当前版本尚无不可变 `PromptTemplateVersion` 行，创建 Batch 的同一事务必须先物化该版本。模板版本增加唯一约束 `(template_id, version)`。
- `prompt_template_snapshot` 至少冻结 template/version/task_type/system prompt/user template/input schema/output schema；`model_config_snapshot` 至少冻结 config id/version/provider/model name/temperature/max_tokens 及经过字段白名单脱敏的额外参数和端点标识。两者都禁止任何 credential/header/secret 字段。
- 快照 hash 使用版本化 canonical JSON 后的 UTF-8 bytes 计算 SHA-256；renderer 只能读取这些快照生成消息，不得在 worker 中重新读取 PromptTemplate/ModelConfig 的可变参数。凭证仅通过 model config 的服务端引用在调用前即时解析，不进入快照。
- `GenerationRun` 增加可空的 `generation_batch_id` FK（仅兼容历史数据）；所有新 run 必填。增加唯一约束 `(generation_batch_id, chunk_id)`，一个批次对一个 Chunk 最多一个 run。
- GenerationRun 新增 `rendered_prompt_sha256 CHAR(64)`；现有 `input_prompt` 保存 renderer 实际提交的规范化消息 JSON，hash 必须对其精确 UTF-8 bytes 计算。新 run 在进入 processing 前两者均非空，并能由 Batch 快照、Chunk 内容和 `renderer_version` 独立重建验证。
- CHECK 至少保证：`is_legacy=false -> provenance_status='verified'`；Batch 为 verified 时模板/模型 snapshot、两个 hash、template version 和 renderer 全部非空；Run 为 verified 时 `generation_batch_id/input_prompt/rendered_prompt_sha256` 全部非空；`legacy_unavailable|invalid` 必须 `is_legacy=true` 且 `provenance_error_code IS NOT NULL`。受控 trigger/service 还必须保证 verified Run 所属 Batch 也是 verified。
- `Candidate.generation_run_id` 增加唯一约束；成功 run 最多产出一个 Candidate。`Candidate.source_generation_batch_id` 必须等于其 run 的 batch。
- GenerationBatch 新写状态机为 `pending -> processing -> completed|failed|cancelled`；GenerationRun 对应为 `queued -> processing -> completed|failed|cancelled`。这些终态均不可返回 processing、不可修改选择集/快照/hash/raw output，retry 只能创建新行。旧值 `review_pending` 仅兼容历史读取，不再由生成 worker 写入；迁移为相关 PostgreSQL enum 增加 `cancelled`。
- `total_chunks == len(selected_chunk_ids)`；`completed_chunks` 只计 Candidate 已 flush 成功的 run，且 `0 <= completed_chunks <= total_chunks`。
- `summary_json` 固定为 `{succeeded, failed, cancelled, candidate_ids, failures}`；`failures` 每项至少含 `chunk_id`、稳定 `code`、脱敏 `message`，计数之和等于 `total_chunks`（处理中的临时快照除外）。
- GenerationRun 终态必须设置 `completed_at`；失败必须有 `error_message`，成功必须有 `raw_output`。LLMUsageLog 对每次实际调用写一条 success/error 记录。
- Alembic migration 必须提供 upgrade/downgrade；所有迁移前 Batch/Run 先标记 `is_legacy=true`。只有能从原行、不可变 PromptTemplateVersion、精确 input_prompt 和关联配置审计数据独立重建并通过全部 hash/归属校验的行才可回填 `verified`；缺少必要输入标记 `legacy_unavailable`，发现互相矛盾的 FK、版本或内容标记 `invalid`。不得从当前模板/模型配置猜测历史快照。
- migration 必须输出三种 provenance 数量及非敏感错误码；建立 CHECK/trigger 前完成回填。加唯一约束前若发现历史重复，触发停止条件，不静默删 Candidate 或 Run。T11 对任一来源 Batch/Run 的 `provenance_status != verified` 必须拒绝新导出并返回稳定 provenance 错误，不能降级为完整快照。

## 5. API 合同

### 5.1 请求与接收响应

`POST /api/chunks/{cid}/generate` 与 `POST /api/projects/{pid}/documents/{did}/generate-batch` 均返回 `202`；两者共享模板、模型字段，批量端点另支持选择 Chunk：

```json
{
  "prompt_template_id": "uuid",
  "model_config_id": "uuid",
  "selected_chunk_ids": ["uuid"]
}
```

- 单 Chunk 请求只发送前两个字段，服务端固定选择 `[cid]`；批量端点的 `selected_chunk_ids` 可省略，省略表示 active ChunkSet 中全部 `ready` chunks，显式传入时必须非空。
- 接收响应统一为 `GenerateAcceptedResponse`：`task_id`、`generation_batch_id`、`status="queued"`；不得假装同步返回 Candidate。
- PromptTemplate、ModelConfig、Document、ChunkSet 和所有 Chunk 必须属于 URL 项目且处于可用状态。不存在或按 T02 不可见返回 `404 ErrorResponse`，状态/归属冲突返回 `409 ErrorResponse`。
- 路由在返回 `202` 前必须完成版本物化和非秘密快照/hash；若模板当前版本无法唯一物化、ModelConfig 含无法安全分类的秘密字段或 renderer 版本不可用，返回下表中的 `409 ErrorResponse` 且不创建 Batch/Task。
- `422` 仅用于请求 JSON、UUID、必填字段、枚举或 `selected_chunk_ids` 空数组等 Pydantic 结构校验，并严格使用 T04 `ValidationErrorResponse`；业务状态、来源、配置、snapshot、幂等和 retry 冲突不得借用 `422`。

### 5.2 ErrorResponse code

以下 code 是端点 OpenAPI 响应的有限联合，均复用 T04 `ErrorResponse`；`context` 仅包含表中列出的非敏感字段：

| HTTP | code | 适用场景 | 允许的 context |
|---|---|---|---|
| 404 | `GENERATION_CONFIG_NOT_FOUND` | scoped PromptTemplate/ModelConfig 不存在或不可见 | `config_type` |
| 404 | `GENERATION_SOURCE_NOT_FOUND` | scoped Document/ChunkSet/Chunk/Batch 不存在或不可见 | `source_type` |
| 409 | `GENERATION_CONFIG_UNAVAILABLE` | 配置禁用、凭证引用不可用或模板版本不能唯一物化 | `config_type`, `config_id` |
| 409 | `GENERATION_SNAPSHOT_UNSAFE` | 配置含不可安全分类字段、canonical snapshot/hash 无法建立 | `config_type`, `field_path` |
| 409 | `GENERATION_RENDERER_UNAVAILABLE` | renderer 版本未知或无法确定性重建 prompt | `renderer_version` |
| 409 | `GENERATION_SOURCE_NOT_READY` | 无 active ChunkSet、Chunk 不属 active set、状态不允许或选择集为空 | `document_id`, `chunk_set_id` |
| 409 | `IDEMPOTENCY_KEY_REUSED` | 同一 key 对应不同规范请求摘要 | `task_type` |
| 409 | `GENERATION_IN_PROGRESS` | 相同 source/Chunk 已有不允许并行的生成 | `task_id`, `generation_batch_id` |
| 409 | `GENERATION_NOT_RETRYABLE` | Task/Batch 非可重试状态或没有未成功项 | `task_id`, `generation_batch_id` |
| 409 | `GENERATION_RETRY_EXISTS` | 源 Batch 已有直接 retry 后继且本请求不能作为同 key 重放 | `task_id`, `generation_batch_id` |
| 409 | `GENERATION_PROVENANCE_INVALID` | Batch/Run provenance 非 verified 或 hash/归属校验失败 | `generation_batch_id`, `generation_run_id`, `provenance_status` |

认证、角色和跨项目不可见语义继续复用 T01/T02 已注册的 code；不得为同一含义新增 generation 私有别名。

### 5.3 查询合同

- 新增 `GET /api/projects/{pid}/generation-batches/{gbid}`，返回批次字段、计数、`retry_of_generation_batch_id`、`is_legacy/provenance_status/provenance_error_code`、脱敏快照的版本/hash/renderer 摘要和脱敏 summary，不返回 credential。
- 新增 `GET /api/projects/{pid}/generation-batches/{gbid}/runs?page&page_size`，返回参数化分页 `GenerationRunResponse`，包含 `is_legacy/provenance_status/provenance_error_code/rendered_prompt_sha256`；是否返回完整 `input_prompt` 继续受项目权限与敏感数据策略控制。
- 新增 `GET /api/chunks/{cid}/candidates?page&page_size`，返回 `PaginatedResponse[CandidateResponse]`，按 `created_at DESC, id DESC` 稳定排序。
- Task 查询和 WebSocket 事件沿用 T07；generation Task 的 `entity_type/entity_id` 指向其 GenerationBatch。前端以 Task 为执行状态源，以 GenerationBatch 为业务汇总源，并可沿 `retry_of_generation_batch_id` 展示派生链。

### 5.4 执行语义

- 路由只校验并原子创建 GenerationBatch + parent Task，再通过 T07 dispatcher 派发；worker 使用独立 session，不捕获异常后伪装成功。
- 每个 Chunk 对应一个 child Task 和一个 GenerationRun。worker 在外部调用前从 Batch 快照和 Chunk 重建 `input_prompt`、核对 snapshot/hash/renderer，不读取模板/模型的当前可变参数；成功顺序为：持久化 raw output/Candidate/usage → 标记 run/child task completed → 更新批次计数。
- LLM 返回非 JSON 时该 run `failed`，不得用 `{ "raw_text": ... }` 创建看似合格的 Candidate；原始输出保留供诊断。
- 所有 child 成功时 batch/parent task 为 `completed`；任一 child 最终失败时为 `failed`；收到取消且未全部成功时为 `cancelled`。父任务不得在失败子任务存在时为 completed。
- chunk 在其 run 执行中为 `generating`，成功为 `generated`，失败/取消回到 `ready`。批次终态后重新计算 Document 状态，至少保证不残留 `generating`；详情页以最新批次而非 Document 粗粒度状态展示结果。
- T07 对 generation Task 的人工 retry 原子创建新 Task 与新 GenerationBatch；新 Batch 的 `retry_of_generation_batch_id` 指向旧 Batch，复制旧 Batch 的同一 verified 模板/模型快照、hash 和 renderer，只把旧 Batch 中没有 completed Run + Candidate 的 Chunk 写入新 `selected_chunk_ids` 并创建 verified 新 Run。旧 Batch/Run/Task 永不改写或复活；源 provenance 非 verified 时返回 `GENERATION_PROVENANCE_INVALID`。
- retry 后的新 Task 使用 T07 的 `retry_of_task_id` 指向旧 Task，且 `entity_id` 指向新 Batch；已成功旧 Run/Candidate 不复制、不重跑、不新增 usage。若没有未成功项则返回 `409 GENERATION_NOT_RETRYABLE`。
- 同一 retry `Idempotency-Key` 并发请求只创建一个新 Task/Batch；同一源 Batch 已有直接 retry 后继时返回该后继或 `409`，不能产生分叉链。

## 6. 前端合同

- 单 Chunk 页同时加载项目 PromptTemplates 和 ModelConfigs；二者均有明确选择，缺一时禁用生成并给出配置入口。
- 请求字段只使用 `prompt_template_id`、`model_config_id`；收到 `202` 后显示 queued/processing，而不是立即提示“生成完成”。
- 通过 T07 WebSocket 或有界轮询跟踪 parent task；终态后读取 GenerationBatch 及该 Chunk 的 Candidate 列表。
- 文档详情页“批量生成”必须有实际 handler，并在对话框中选择模板、模型及“全部 ready / 已选 chunks”；重复提交期间禁用按钮。
- 部分失败时展示成功/失败/取消计数和可定位的 Chunk 列表；不得只显示统一成功 toast。
- 页面卸载或切换文档时取消轮询/请求；刷新页面可通过 task/batch id 恢复状态，不能只依赖组件内布尔值。
- Batch 详情展示所用模板/模型版本、renderer 版本和 hash 前缀；不得展示 credential 或把当前配置名称误当成历史快照。
- retry 成功后 UI 切换跟踪响应中的新 Task/Batch，并保留“重试自”链接和旧批次只读结果；不得等待旧 Task 变回 queued，也不得把新 Batch 合并覆盖旧批次。
- Batch/Run 页面明确展示 verified、legacy unavailable、invalid；后两者禁用 retry，并说明不能进入可信导出。前端不得把 `is_legacy=false` 自行推断为 verified，必须读取 `provenance_status`。
- 生成页和 retry UI 按 T04 判别联合处理上表 code；配置、snapshot、source、in-progress 和 retry 冲突分别给出可恢复动作。字段级 `422 ValidationErrorResponse` 定位到表单字段，不与业务 `ErrorResponse` 混用或匹配中文 message。

## 7. 预期修改面

- `apps/api/app/models/generation.py`、`prompt_template.py`、`models/__init__.py`、Alembic migration。
- `apps/api/app/schemas/chunk.py`、`document.py`、新增/扩展 generation schema。
- `apps/api/app/routers/chunks.py`、`documents.py`、新增 generation-batches router。
- `apps/api/app/services/chunk_service.py`、generation orchestration service、`workers/generate_worker.py`。
- Task dispatcher/handler 注册点（只接入 T07，不改变其通用状态机）。
- 单 Chunk 页、文档详情页、任务/批次状态组件和生成类型。
- canonical snapshot/prompt renderer 模块、secret-field filter，以及后端集成测试、worker 测试、前端组件测试、开发文档与 `docs/logs/dev-log.md`。

## 8. 依赖

- T04：请求/响应生成类型、分页合同和类型化 client。
- T06：不可变 active ChunkSet、Chunk 归属和 `ready` 集合。
- T07：可恢复 payload、dispatcher、合法状态机、真实 retry/cancel 与事件。
- T02 通过 T06 的依赖链先行完成；所有新增查询仍必须运行 T02 跨项目回归。
- T09 依赖本卡稳定产出带 batch/run 溯源的 Candidate。
- T11 只接受本卡 `provenance_status=verified` 的 Batch/Run；其他状态是可查询历史，但不是可导出的可信来源。

## 9. 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| worker 内复用请求 session 导致提交/回滚失效 | 路由提交派发记录后，worker 每次从 session factory 创建独立事务 |
| 部分成功后父任务错误完成 | 单一聚合函数从 run/child task 持久状态计算，不依赖循环是否抛异常 |
| retry 重复收费或重复 Candidate | 新 Batch 只选择上一批未成功项；batch/chunk、Candidate/run 和 retry 后继唯一约束共同兜底 |
| 取消与 LLM 返回竞态 | T07 取消令牌检查放在调用前和落 Candidate 前；终态条件更新防覆盖 cancelled |
| 模型输出包含敏感信息 | raw output 仅项目成员可读，task/summary 错误脱敏，不把 API key 写入日志 |
| 模板/模型在排队期间被修改 | 创建 Batch 时冻结版本化快照；worker 只读快照并核验 hash/renderer |
| snapshot 或 extra params 混入秘密 | 严格非秘密字段白名单、递归 secret scanner 和负向 fixture；无法分类即停止创建任务 |
| legacy 数据被误标为 verified | migration 只接受可独立重建且 hash/归属全通过的行；其余诚实标记 unavailable/invalid，T11 fail closed |
| 领域错误继续返回中文 detail | OpenAPI code 有限联合、统一异常映射与前端 exhaustiveness test |

## 10. 实施步骤

1. 固定 canonical snapshot/hash、PromptTemplateVersion 物化和 renderer version 合同，再设计 migration、GenerationBatch ORM 与 run/batch 关联。
2. 合并单项/批量创建逻辑为一个 orchestration service，原子冻结快照并写 batch、runs/tasks 和 payload。
3. 将 worker 改为只读冻结快照、核验 rendered prompt hash、返回明确结果或抛出明确失败；删除不存在的导入和重复建 run 路径。
4. 接入 T07 dispatcher、取消检查和 generation retry planner；retry 原子派生新 Task/Batch/Run，旧终态保持不变。
5. 补齐 Document/Chunk 终态恢复、批次聚合与 retry 线性链唯一约束。
6. 实施 legacy provenance 审计/回填、CHECK/trigger，并增加批次、run 和 chunk candidates 查询 API。
7. 为生成/查询/retry 端点注册 ErrorResponse code 联合，重新生成 T04 类型并增加未覆盖 code 的编译门禁。
8. 完成两个前端生成入口、provenance/快照摘要、业务错误分支、异步状态/失败明细及新 Batch retry 跟踪 UI。
9. 使用 fake LLM 补齐矩阵测试，执行全量门禁并写中文 DevLog。

## 11. 自动化验收标准

1. 导入 `apps.api.app.routers.chunks` 不再出现 `run_generate` ImportError。
2. 单 Chunk 请求准确持久化 1 batch、1 parent task、1 child task、1 run、1 Candidate、1 usage log，以及模板/模型快照、版本、hash、renderer 和 rendered prompt hash；所有 FK 和计数一致。
3. 三 Chunk 批量全成功时仅调用 LLM 三次，产生三个 Candidate，batch/parent/children 全部 completed，进度 100。
4. 第二个 Chunk 失败时 batch 与 parent 为 failed，`completed_chunks=2`，成功 Candidate 保留，失败 run 有脱敏错误，父任务绝不 completed。
5. 取消时未开始 Chunk 不调用 LLM、不产出 Candidate，batch/task 为 cancelled，Chunk 不残留 generating。
6. 对含两个成功、一个失败的 Batch retry 时，旧 Task/Batch/Run 字节级不变；只创建含失败 Chunk 的新 Task/Batch/Run，只额外调用 LLM 一次，成功项 Candidate/usage 数不变。
7. 对 failed/cancelled Batch 或 Run 做任何原地复活/快照修改均被 service/数据库门禁拒绝；两个并发 retry 只形成一个直接后继，不产生分叉。
8. 创建 Batch 后修改或删除当前 PromptTemplate/ModelConfig 的非秘密参数，worker 仍使用冻结值；由快照和 Chunk 重建的 input prompt hash 与 Run 完全一致。
9. secret scanner 证明 Batch/Run/Task/API 响应不含测试 API key、Authorization、credential 或嵌套 secret extra param；无法安全快照的配置在派发前失败。
10. legacy migration fixture 分别得到 verified、legacy_unavailable、invalid；只有可独立重建且 hash 一致的行进入 verified，重复 migration 结果一致且输出非敏感计数。
11. Batch/Run CHECK 拒绝 `is_legacy=false + non-verified`、verified 但缺 snapshot/hash/input prompt，以及 non-verified 但无 error code；verified Run 不能关联 non-verified Batch。
12. T11 preflight/合同 fake 对 legacy_unavailable/invalid Batch 或 Run 返回 `409 GENERATION_PROVENANCE_INVALID`，不创建 SnapshotManifest/Export 产物。
13. 表中每个领域 code 至少一个 API 合同测试；响应符合 T04 ErrorResponse 且 OpenAPI code 联合完整。结构错误单独断言 `422 ValidationErrorResponse`，不存在字符串 detail。
14. 跨项目模板、模型、Chunk、Document 或 batch 组合返回 T02 的 `404 ErrorResponse`，不派发任务、不调用 LLM。
15. 前端组件测试覆盖双选择、202 等待、刷新恢复、全部成功、部分失败、取消、retry 跟随新 Task/Batch、provenance 状态、每类错误恢复动作和重复点击。
16. T04 合同漂移检查、Ruff、pytest、lint、TypeScript、组件测试和 build 全部返回 0。

## 12. 停止条件

- T06 尚不能提供唯一 active ChunkSet，无法确定批量输入集合；
- T07 没有可持久化 payload/真实派发接口，retry 只能改数据库状态而不能执行；
- 历史 GenerationRun/Candidate 存在会阻止唯一约束的重复且缺少数据处置决定；
- 无法判定存量 Batch/Run 属于 legacy_unavailable 还是 invalid，或相关方要求在缺少原始输入时将其标为 verified；
- 当前 PromptTemplate 版本无法唯一物化，或历史版本表存在同 template/version 多份且内容不一致；
- ModelConfig/extra params 无法区分可冻结参数与 secret，或凭证只能复制进 Task/Batch 才能执行；
- renderer 无稳定版本标识，或同一 snapshot + Chunk 在生产与测试不能重建相同 `input_prompt` bytes/hash；
- 产品要求“部分成功”算 completed，但尚未定义导出和审核如何识别缺项；
- 模型供应商无法提供可测试的 fake adapter 边界，默认测试会访问真实付费服务；
- 取消语义需要强杀进程或破坏已提交 Candidate 才能实现。

## 13. 审查重点

- 创建、派发和 worker 是否只有一条路径，且事务边界清晰。
- batch/run/task/candidate 的关联和终态是否能从数据库独立复核。
- `is_legacy/provenance_status` 的 CHECK、跨表约束和迁移是否 fail closed，T11 是否明确拒绝非 verified 来源。
- template/model 快照是否在创建时冻结、递归脱敏并带 canonical hash，worker 是否完全避免读取可变参数。
- rendered prompt 是否绑定 renderer 版本且可从快照与 Chunk 独立重建验证。
- 异常是否向聚合器传播，是否还存在吞异常后标 completed。
- retry 是否创建新 Task/Batch/Run、保持旧终态不变，并且只调用上一批未成功项。
- 所有业务错误是否复用 T04 ErrorResponse 并使用注册 code，`422` 是否只保留给 ValidationErrorResponse。
- 前端是否把 `202 Accepted` 与业务完成明确区分。

## 14. 完成定义

- 单项与批量生成均能从页面完成并追踪到真实终态。
- GenerationBatch、GenerationRun、Task、Candidate、usage log 形成完整一致的溯源链；模板/模型快照、版本、renderer 与 rendered prompt hash 可独立复核且不含秘密。
- 新 Batch/Run 均为 verified；存量来源被诚实分类为 verified/legacy_unavailable/invalid，T11 对非 verified fail closed。
- 成功、失败、部分失败、取消和 retry 自动化测试全部通过，且不访问真实 LLM。
- failed/cancelled Batch/Run 永不复活；每次 retry 都形成可追踪的新 Task/Batch 线性派生链，成功旧项不重跑。
- 不存在卡在 `generating/processing` 或父任务伪 completed 的已知路径。
- OpenAPI/前端生成类型已更新，全量门禁通过，中文 DevLog 已记录实施与验证结果。
