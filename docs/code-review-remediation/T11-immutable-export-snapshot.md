# T11：不可变导出与完整快照

- 状态：待实施
- 优先级：P0-Data
- 建议规模：L
- 直接依赖：T03、T06、T07、T08、T09、T10
- 可并行：仅可提前设计；实现应在依赖卡合并后进行

## 1. 目标

每次 Dataset/Benchmark 导出都生成独立、可校验、不可覆盖的历史产物。快照必须冻结导出时的成员顺序、CuratedRevision 内容、证据及从 Document/ParseJob/CleanedDocumentVersion/ChunkSet/GenerationBatch 到 Prompt/Model 的版本图；后续编辑、重导出或同扩展名格式不得改变旧下载。快照本身足以重新渲染并验证输出，不依赖可变业务表。

## 2. 范围

1. 导出请求先创建持久 Export 与 T07 Task，不再返回伪造的 completed Export。
2. 在一致性数据库快照中冻结完整 canonical manifest，并以 hash 封存。
3. 输出和 manifest 使用每次 Export 唯一、内容寻址且带对象版本的 MinIO key。
4. 记录 payload/manifest 的 SHA-256、字节数、bucket、object version id 和格式化器版本。
5. 数据库阻止 completed Export 与 SnapshotManifest 的更新/删除。
6. 下载固定到记录的对象 version，并在签发链接前校验元数据；提供深度验证 API。
7. 修正导出列表、manifest 查看、失败/重试和下载前端合同。
8. 对无法恢复的旧导出明确标记 `legacy_unverified`，不伪造完整性。
9. provenance 只消费依赖卡已经冻结并带 hash 的快照；禁止从当前 Parser/Profile/Prompt/Model/CuratedItem 配置反推历史。

## 3. 明确不做

- 不恢复已被固定 key 覆盖的历史字节；没有历史对象版本时不可推断原内容。
- 不新增导出格式；仅把现有 formatter 版本化、确定化和可复现化。
- 不绕过 T09 的审批/证据门禁，也不替 T10 修复 Dataset/Benchmark 编组。
- 不把 LLM API key、解析器凭证或其他秘密写入 manifest。
- 不在导出时把当前可变配置复制后冒充历史快照；缺少依赖卡冻结记录即阻断导出。
- 不在本卡建设长期归档、跨区域备份、签名证书体系或公开下载链接。

## 4. 数据库合同

### 4.1 exports

在现有表上新增或调整：

- `status`：`queued/processing/completed/failed`；只有 completed 可下载。
- `source_type` 与派生 `source_id`，并保留 FK；CHECK `num_nonnulls(dataset_id, benchmark_id) = 1`。
- `request_fingerprint CHAR(64)`、`profile_snapshot JSONB NOT NULL`、`formatter_version VARCHAR(100) NOT NULL`。
- `task_id UUID`（重试时指向当前 Task）、`retry_count`、`error_code`、`error_message`、`completed_at`。
- `bucket_name`、`minio_key`、`object_version_id`、`content_type` 在完成前可空。
- `output_sha256 CHAR(64)`、`file_size BIGINT`、`manifest_sha256 CHAR(64)`、`manifest_key`、`manifest_object_version_id`。
- `snapshot_manifest_id` 改为完成前可空；CHECK 保证 completed 时上述产物字段全部非空、`item_count >= 0`。
- `artifact_seal_id UUID UNIQUE REFERENCES export_artifact_seals(id) DEFERRABLE`；在 completed 时必须非空。跨表一致性由 deferred constraint trigger 校验，不能用无法跨表的普通 CHECK 冒充。
- `is_legacy BOOLEAN NOT NULL DEFAULT false`；新记录必须满足完整字段合同。
- 新对象 key 的部分唯一索引至少覆盖 `(bucket_name, minio_key, object_version_id) WHERE is_legacy=false`。

Export 允许 queued/processing/failed 在 T07 fenced attempt 下推进或重试；一旦 completed，数据库 trigger 拒绝 UPDATE/DELETE。`projects -> exports` 的删除关系改为 RESTRICT，项目删除不得级联抹掉审计历史。

### 4.2 snapshot_manifests

- 新增 `export_id UUID NOT NULL UNIQUE`、`project_id UUID NOT NULL`、`schema_version`、`canonicalization_version`、`manifest_sha256`、`sealed_at`。
- `manifest JSONB` 保存完整冻结数据，不只保存 ID 列表；插入后 trigger 拒绝 UPDATE/DELETE。
- 现有单值 `chunk_set_id/cleaned_version_id/generation_batch_id` 仅保留为 legacy 兼容，不再代表多文档快照；新快照的完整多值关系写入 manifest。
- SnapshotManifest FK 以及 Export 到 manifest 的 FK 使用 RESTRICT；不允许业务实体级联删除已封存快照。

### 4.3 export_artifact_seals

新增一对一封存表，合同如下：

- `id UUID PRIMARY KEY`；`export_id UUID NOT NULL UNIQUE REFERENCES exports(id) ON DELETE RESTRICT`；`snapshot_manifest_id UUID NOT NULL UNIQUE REFERENCES snapshot_manifests(id) ON DELETE RESTRICT`。
- `seal_version VARCHAR(50) NOT NULL`、`seal_payload JSONB NOT NULL`、`seal_sha256 CHAR(64) NOT NULL UNIQUE`、`sealed_at TIMESTAMPTZ NOT NULL`。
- manifest 与 output 各自具有非空 `bucket/key/object_version_id/sha256/size/content_type`；两组 `(bucket,key,object_version_id)` 分别 UNIQUE，size 必须 `>= 0`，key/version/content type 不得为空，两个对象坐标不得相同。
- 所有 SHA-256 使用小写 64 位十六进制 CHECK；`export_id/snapshot_manifest_id`、两组对象字段和 formatter/schema 版本必须逐项出现在 `seal_payload` 且值相等。
- `artifact-seal-cjson-v1` 对不含 `seal_sha256` 的精确 `seal_payload` 做 UTF-8/NFC/稳定 key 与数字规范化，`seal_sha256 = SHA256(canonical_bytes)`；字段集合、null 规则和时间格式固定并有 golden fixture，禁止拼接字符串计算。
- `BEFORE INSERT` trigger 调用共享 canonical SQL/helper（使用 pgcrypto 或等价受控函数）复算 hash、核对 payload/列和关联 Export/Snapshot；不一致即拒绝。`BEFORE UPDATE OR DELETE` trigger 无条件拒绝。
- deferred constraint trigger 在事务提交时保证 completed Export 恰有一个 seal，`artifact_seal_id/export_id/snapshot_manifest_id` 互相匹配，且 Export 的 hash/key/version/size/content type 与 seal 完全一致。

该表只在两个对象上传完成后插入一次，避免在不可变 manifest 中回填上传后才可获得的 version id。若 seal INSERT 后 Export 的 fenced completed CAS 失败，两步处于同一数据库事务并一并回滚，不得留下“seal 已发布、Export 未完成”的半状态。

### 4.4 canonical manifest v1

canonicalization 定义为版本化规则：UTF-8、Unicode NFC、对象 key 排序、无非语义空白、稳定数字/布尔/null 表达、时间统一 UTC ISO-8601；hash 计算排除 `manifest_sha256` 自身。不得直接依赖未版本化的 `json.dumps` 默认行为。

manifest 至少包含：

- `schema/canonicalization/exporter/formatter` 版本、export id、项目 id、请求人和精确快照时间。
- source 类型/id/name/status、T10 composition revision/hash、成员总数与稳定 ordinal。
- ExportProfile 的 id/version/format 和完整非敏感配置快照/hash。
- 每个成员的 membership id、ordinal、CuratedItem id/type/status、被批准的 CuratedRevision id/version、冻结 `content` 与 content SHA-256。
- 每条 EvidenceLink 的冻结页码、heading、quote、offset（若 T09 提供）及 hash。
- 每条证据的 Document id/filename/SHA-256、ParseJob 与 ParserProfile 版本/脱敏配置。
- CleanedDocumentVersion id/version/source hash，ChunkSet id/version/input/output hash/冻结 tokenizer 与 splitter 配置。
- Chunk id/ordinal/冻结 content 或可独立验证的证据上下文及 hash。
- Candidate、GenerationRun、GenerationBatch、PromptTemplateVersion 的冻结 prompt/hash，以及 ModelConfig 的脱敏参数快照/hash。
- 预期输出格式与 formatter 版本；payload/manifest 的 SHA-256、字节数、对象 key/version id 位于独立 immutable artifact seal，避免 manifest 自引用或上传后改写。

任何缺失链路必须显式记录 `provenance_gap` 并阻断新导出；不得用 null 冒充“已完整验证”。manifest 中严禁 `api_key_encrypted`、Bearer token、预签名 URL 和未脱敏 secret option。

## 5. 一致性、上传与发布合同

1. runner 在 `REPEATABLE READ` 事务中只消费 T10 finalized composition snapshot、T09 approved CuratedRevision/Evidence snapshot、T08 GenerationBatch/Prompt/Model snapshot、T06 ChunkSet snapshot，以及由 T03 规则脱敏且被 ParseJob 固定引用的 ParserProfile version snapshot；构建完整 manifest 并一次 INSERT 封存。
2. 事务提交后，formatter **只从 SnapshotManifest** 渲染 payload；不得重新读取当前 CuratedItem/Evidence/ParserProfile/PromptTemplate/ModelConfig。任一冻结引用缺失或 hash 不符返回 `PROVENANCE_SNAPSHOT_MISSING`，禁止 fallback 到 current/default 配置。
3. 计算 payload hash 后使用 key：`{project_id}/exports/{export_id}/payload-{sha256}.{ext}`；manifest 使用同目录的 `manifest-{sha256}.json`。禁止 `dataset_{id}.json` 一类共享 key。
4. outputs bucket 必须启用 versioning（或等价 WORM/write-once 能力）；保存每次 PUT 返回的 `version_id`。若同 key 已存在，仅在 bytes/hash 完全一致时复用。
5. 对象 metadata 写入 `sha256/export_id/schema_version`；上传 payload 与 manifest 后，开启数据库事务，先 INSERT 经 trigger 验证的 artifact seal，再以 T07 run token CAS 更新 Export 的 seal/对象字段与 completed。deferred constraint 在 commit 前复核一对一关系；任一步失败则整笔回滚。
6. 上传成功、DB finalize 失败时，retry 复用已封存 snapshot 和相同 hash 对象；不得重新快照。取消/失败产生的未引用版本进入有保留期的 orphan 清理，不能立即误删可能已发布对象。

## 6. 迁移与回滚

- 现有 Export/SnapshotManifest 全部标记 `is_legacy=true/legacy_unverified`；迁移不得联网读取 MinIO 或为旧记录补造 hash。
- 重复 `minio_key` 保留但只允许 legacy；新记录受部分唯一索引和新 key 规则约束。
- 旧记录 manifest 响应明确返回 `integrity_status=unverified_legacy`；若 MinIO version history 仍在，可由独立、人工批准的取证脚本生成补充报告，但不改写原快照。
- 建 trigger 前完成所有 DB 回填；之后快照不可再更新。
- downgrade 先停止 export runner，并确认无 queued/processing 新格式 Export；只撤 schema/trigger，不删除对象版本。bucket versioning 不得由 Alembic 自动关闭。

## 7. API 合同

- `POST /projects/{pid}/datasets/{did}/export` 与 benchmark 等价接口返回 `202 {export_id, task_id, status:"queued"}`，不再伪造 `snapshot_manifest_id=task.id`。
- 请求为 `{export_profile_id, expected_source_revision, expected_source_sha256}` 并支持 `Idempotency-Key`；source 非 finalized、revision/hash 不符、存在未批准/证据不完整条目时返回稳定 409 错误。
- `GET /projects/{pid}/exports` 返回分页 `ExportResponse`：source_type/id、status、format、item_count、file_size、hash、integrity_status、task_id、created/completed/error。
- `GET .../exports/{eid}/manifest` 对新记录返回完整 manifest；legacy 返回现有内容并显式 unverified，不补造字段。
- `GET .../exports/{eid}/download` 仅对 completed 返回绑定 `object_version_id` 的短时 307；签发前 HEAD 校验 size/hash metadata，不符返回 `409 EXPORT_INTEGRITY_ERROR`。
- `POST .../exports/{eid}/verify?deep=true` 流式重算 payload 与 manifest hash，返回逐项结果；浅验证只核对 DB、version id 和 metadata。
- failed Export 可通过 T07 retry 复用同一 export id/snapshot；completed Export 返回 `409 EXPORT_IMMUTABLE`，不得重跑或改格式。

## 8. 前端合同

- 发起导出时显示 finalized source revision、ExportProfile 版本和条目数；一次操作复用同一 idempotency key。
- 导出页按真实响应展示 source type/id、queued/processing/failed/completed、大小、完成时间、hash 前缀与 integrity 状态。
- 只有 completed 且完整性非失败时显示下载；链接始终指向后端 download endpoint，不缓存预签名 URL。
- manifest 点击时按需请求，不假定列表内嵌；legacy 使用醒目的“历史不可验证”提示。
- failed 显示 Task 错误与 retry；retry 后继续观察同一 Export 的新 task，不创建虚假 completed 行。
- 提供“验证”操作并区分浅验证/深度验证耗时；校验失败后禁用普通下载并提示管理员。

## 9. 预期修改面

- `apps/api/app/models/export.py`、`schemas/export.py`、`services/export_service.py`
- `apps/api/app/workers/export_worker.py` 与 formatter/canonical JSON 模块
- `apps/api/app/routers/datasets.py`、`benchmarks.py`、`exports.py`
- `libs/storage/storage/minio_client.py`：versioned put/head/presign-version/stream verify
- `apps/api/migrations/versions/` 新增迁移和不可变 trigger
- canonical manifest/seal helper、pgcrypto（或等价）迁移与跨语言 golden fixtures
- outputs bucket 初始化/运维配置、orphan 清理 runbook
- 导出发起页、导出历史页、生成 API 类型和前端测试
- 完整快照 golden fixtures、存储/数据库故障集成测试、`docs/logs/dev-log.md`

## 10. 依赖和风险

- T03 提供凭证隔离和可安全封存的解析器配置边界；T06 提供 ChunkSet snapshot；T07 提供 fenced retry/cancel；T08 提供 GenerationBatch/Prompt/Model snapshot；T09 提供 approved revision/evidence snapshot；T10 提供 finalized composition snapshot。
- 完整 manifest 可能较大；先保证正确性，可同时存 DB JSONB 与压缩对象，但两份必须有同一 canonical hash。
- MinIO bucket 若无 versioning，应用唯一 key 仍防正常覆盖，但不能抵御外部同 key 覆写；验收不得降级此要求。
- 并发编辑会破坏一致快照；finalized 集合应由 T10 阻止编辑，仍以 REPEATABLE READ 和 revision 校验兜底。
- 完整 prompt/source 上下文可能敏感；沿用项目权限、私有 bucket、短时 URL并做 manifest secret scanner。
- 应用 canonicalizer 与数据库 seal helper 漂移会误报或漏报篡改；两端共享版本号/golden bytes，版本不匹配时禁止写 seal。

## 11. 实施步骤

1. 定义 manifest/canonicalization/formatter v1 和脱敏白名单，建立 golden 文件。
2. 编写 legacy 标记、Export 状态字段、seal FK/UNIQUE/CHECK、canonical hash helper、Snapshot/Seal/Completed Export trigger 与回滚预检迁移。
3. 扩展 storage adapter，启用并验证 bucket versioning、version-specific HEAD/download。
4. 把导出拆为“一致快照封存 -> 从快照渲染 -> versioned upload -> seal INSERT + fenced Export finalize 同事务”，并移除所有 current config fallback。
5. 改造 dataset/benchmark 触发与 export list/detail/manifest/download/verify API。
6. 更新前端真实状态、manifest 懒加载、版本固定下载、失败重试和验证交互。
7. 加入并发修改、重复导出、同扩展格式、存储/DB 分段失败、secret 扫描测试。
8. 执行迁移往返、历史下载回归和全量门禁，记录 legacy 数量及 bucket 配置。

## 12. 自动化验收标准

1. 同一 Dataset 连续导出两次得到不同 export id/key/version；第二次导出及后续内容编辑不改变第一次下载的 bytes/hash。
2. `qa_json/messages/alpaca/sharegpt` 即使扩展名同为 `.json` 也绝不共享对象 key。
3. 从 SnapshotManifest 独立重渲染得到与记录相同的 payload SHA-256 和字节数。
4. manifest 中每个成员均有冻结 content/revision/evidence 和完整版本图；故意断开任一新链路时导出失败而非降级。
5. secret scanner 证明 manifest/对象不含 API key、token、预签名 URL；配置仅含白名单字段。
6. DB 直接 UPDATE/DELETE completed Export 或任一 SnapshotManifest 被 trigger 拒绝，项目级联删除也不能移除它们。
7. 外部写入同 key 的新 MinIO 版本后，旧 Export 仍按保存的 version_id 下载原 bytes；metadata/hash 不符时 API 拒绝下载。
8. 模拟“快照后上传失败”“上传后 finalize 失败”“取消发生在上传后”均可安全 retry，且不重新读取可变业务表、不覆盖旧对象。
9. 并发修改 source 时要么导出请求因 revision 冲突失败，要么得到单一一致版本，绝无混合成员/内容。
10. legacy 记录显示 unverified 且无伪造 hash；Alembic 往返、pytest、Ruff、前端测试、TypeScript、ESLint、build 全部返回 0。
11. 直接 INSERT 错误 seal hash、错配 export/snapshot、空 object version、重复对象坐标或负 size 均被 DB 拒绝；直接 UPDATE/DELETE seal 被 immutable trigger 拒绝。
12. 直接把 Export 标 completed 而不提供 seal、交换两个 Export 的 seal、或令 Export 字段与 seal 不一致，均在 deferred constraint 检查时回滚；合法 seal+completed 原子提交成功。
13. 创建 provenance 后修改当前 ParserProfile/PromptTemplate/ModelConfig/CuratedItem，再导出仍只出现 T03/T08/T09/T10 冻结值；删除任一冻结快照则返回 `PROVENANCE_SNAPSHOT_MISSING`，绝不从当前配置补齐。

## 13. 停止条件

- T09 不能提供稳定 approved CuratedRevision 或证据版本，导致无法冻结被批准的具体内容。
- T10 没有 composition revision/finalized 不可变语义，且无法在事务中锁定一致成员集。
- T03/T08/T09 任一卡不能提供脱敏、不可变且带 hash 的被引用快照；不得从当前配置表反推。
- MinIO/兼容存储无法返回并按 version id 下载，也没有获批的 WORM/write-once 等价能力。
- 旧导出已覆盖而相关方要求“恢复原文件”；应停止并转入备份/对象版本取证流程。
- 完整 manifest 的数据分类不允许保存 prompt/source 上下文，且尚未批准可独立验证的替代封存格式。

## 14. 审查重点

- formatter 是否只读封存快照，而不是导出时再次读取当前业务表。
- object key、version id、hash 和 DB trigger 是否共同保证旧历史不被覆盖/改写。
- seal 的 FK/UNIQUE/CHECK、canonical hash trigger 与 deferred completed constraint 是否在数据库层拒绝错配和直接篡改。
- manifest 是否冻结真实 approved revision、证据和全版本图，并严格排除秘密。
- exporter 是否只消费 T03/T08/T09/T10（及 T06）冻结引用，是否残留 current/default 配置 fallback。
- 重试/取消跨越 DB 与 MinIO 的失败窗口时是否幂等，是否可能错误删除已发布对象。
- legacy 是否诚实标记不可验证，API 是否仍可能返回伪造 placeholder Export。

## 15. 完成定义

- 每个新 Export 均有完整 immutable manifest、唯一 versioned payload、双 hash 和可执行验证结果。
- 每个 completed Export 均与唯一、数据库校验且不可修改的 artifact seal 原子绑定。
- 历史下载在重导出、业务编辑、进程失败和对象新版本出现后仍返回原字节。
- 所有自动化验收与 R1 导出主链通过，迁移、bucket versioning、orphan 处置和验证 runbook 完整。
- `docs/logs/dev-log.md` 已用中文记录实施、legacy 数量、对象存储配置和验证结果。
