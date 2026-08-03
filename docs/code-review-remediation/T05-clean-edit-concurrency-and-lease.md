# T05：清洗编辑并发与租约

- 状态：代码及自动化验收完成，待真实 R1 联调
- 优先级：P1
- 建议规模：L
- 直接依赖：T02、T04
- 可并行：可与 T06、T07 并行

## 1. 目标

消除清洗工作台中的串写、覆盖和静默丢稿：Section A 的迟到响应不得写入 Section B；同一 Section 的过期页面不得覆盖新版本；失去租约的客户端不得继续保存。并发合并不得通过无锁 `MAX(version)+1` 取得相同版本号，也不得覆盖同一个 MinIO 对象。租约与版本发布均以 PostgreSQL 为正确性来源，Redis 仅可作缓存或通知，Redis 故障不能放宽写入门禁。

## 2. 范围

1. 为 Section 内容引入单调递增的乐观并发版本。
2. 将租约获取改为数据库事务内的原子操作，并保证每个 Section 至多一个未释放租约。
3. 保存和提交审核时同时校验项目权限、租约所有权、租约期限与内容版本。
4. 心跳、释放均绑定具体 `lease_id`，旧客户端不能续期或释放新租约。
5. 前端增加请求代次/AbortController、dirty guard、租约丢失只读态与冲突恢复。
6. 覆盖切换章节、切换清洗来源、浏览器返回/关闭、网络延迟、Redis 不可用等场景。
7. 原子分配 `CleanedDocumentVersion.version`，冻结参与合并的 Section revision 向量和内容 hash。
8. 为每个合并版本使用不可复用的对象 key，禁止并发请求覆盖历史清洗产物。
9. 合并入口支持幂等键，并覆盖源 Section 在合并期间变化、数据库提交失败和对象清理场景。
10. 终审以 Document/Version 行锁和 `review_pending` CAS 串行化，保证 active clean version 只向更新版本前进。

## 3. 明确不做

- 不实现 CRDT、OT、多人实时协同编辑或自动三方合并。
- 不改造 Section 分派、终审规则和 CleanedDocumentVersion 的审批业务含义，也不引入版本分支/合并图。
- 不延长或永久保存浏览器本地草稿；仅保证当前页面中的未保存文本不被自动覆盖。
- 不以 Redis 分布式锁作为唯一正确性来源。
- 不修复本卡之外的对象级授权问题；资源归属校验复用 T02。

## 4. 数据库合同

### 4.1 Schema 与约束

- `sections` 新增 `content_revision BIGINT NOT NULL DEFAULT 0`，并加 `CHECK (content_revision >= 0)`。
- 每次成功修改 `cleaned_markdown` 时，在同一 SQL/事务内执行 `content_revision = content_revision + 1`；失败或冲突不得创建 `section_revisions`。
- `section_leases.id` 作为不可复用的 fencing token；客户端必须在保存、提交、心跳和释放时回传它。
- 新增部分唯一索引：`UNIQUE (section_id) WHERE released_at IS NULL`。
- 新增查询索引 `(section_id, expires_at)`；租约必须满足 `expires_at > acquired_at`。
- `section_revisions` 记录保存前的内容，并建议新增 `from_revision`、`to_revision`；约束 `to_revision = from_revision + 1`。

### 4.2 原子租约算法

1. `SELECT sections ... FOR UPDATE` 锁定目标 Section。
2. 将该 Section 中 `released_at IS NULL AND expires_at <= now()` 的租约标记为已释放。
3. 若存在同用户的有效租约，幂等返回原租约；若属于其他用户，返回冲突。
4. 否则插入新租约并提交；部分唯一索引作为并发请求的最终保护。
5. 心跳只更新 `id + section_id + user_id + released_at IS NULL + expires_at > now()` 同时匹配的记录。
6. 释放只处理请求中的 `lease_id`；重复释放返回成功，但绝不能删除、覆盖或缩短另一条租约。

Redis key 若保留，value 必须包含 `lease_id`，设置和 compare-and-delete 使用原子命令/Lua；数据库结果始终优先。Redis 写失败只能降级缓存，不能令无租约保存成功。

### 4.3 迁移与回滚

- 升级时把现有 `content_revision` 回填为 `0`。
- 建唯一索引前，将已过期且未释放的记录置 `released_at = expires_at`；同一 Section 若仍有多条有效记录，仅保留 `expires_at` 最新、再以 `acquired_at/id` 排序的第一条，其余标记释放，并输出审计计数。
- 迁移必须先执行重复数据预检，并在同一事务或维护窗口完成清理与建索引，避免窗口期新增重复租约。
- downgrade 仅移除新增索引、约束和列，不删除既有租约/修订内容；降级前若应用仍发送新版请求，必须先停止流量。

### 4.4 CleanedDocumentVersion 原子发布

- `cleaned_document_versions` 增加 `source_revision_map JSONB`、`source_revision_sha256 CHAR(64)`、`content_sha256 CHAR(64)` 和 `merge_idempotency_key VARCHAR(128)`；新版本均非空，历史行按迁移审计策略回填或明确标记 legacy。
- 保留并验证 `UNIQUE (document_id, version)`，新增 `UNIQUE (document_id, merge_idempotency_key)` 与 `UNIQUE (artifact_key)`；已发布版本的正文、来源 revision、hash 和对象 key 禁止修改。
- 合并先按 ordinal 读取 Section id、`content_revision` 和正文，生成规范化 revision map、合并内容与 hash，并预生成 version UUID。对象 key 使用 `cleaned/{document_id}/{version_id}.md` 或等价 UUID/content-addressed key，不得只使用可竞争的 `v{version}.md`。
- 发布事务锁定 `documents` 行，并按稳定顺序锁定参与合并的 Sections；只有 revision 向量仍与计算输入一致时，才在该 Document 锁保护下分配下一 version、插入版本行并更新文档状态。不得使用无锁 `MAX(version)+1`。
- 对象写入使用唯一 key 和 create-only 语义；数据库提交前后失败必须留下可重试的清理记录或同步删除孤儿对象。正式行不得引用未完成上传或 hash 不匹配的对象。
- 相同幂等键返回同一个已创建版本；不同键的并发请求可以得到不同 version，但必须具有不同对象 key，且任一对象均不能被另一请求覆盖。
- 迁移必须审计重复 `(document_id, version)`、重复 `artifact_key`、对象缺失和正文/object hash 不一致；命中不可判定项时停止，不得静默重命名或覆盖历史文件。

### 4.5 CleanedDocumentVersion 并发终审

- final review 在同一事务内先锁定 `Document`、再锁定目标 `CleanedDocumentVersion`，并验证 version 属于 URL 中的 Document/cleaning job；所有调用使用同一锁顺序避免死锁。
- approve/reject 均以 `status = review_pending` 为前置 CAS。受影响行数不是 1 时返回 review conflict，不得覆盖另一 reviewer 已提交的终态、reviewer 或时间。
- approve 时读取当前 `active_clean_version_id` 并比较 version 号；若目标 version 旧于当前 active version，返回 `CLEAN_VERSION_STALE`，目标仍为 `review_pending`，active pointer 不变。
- 合法 approve 在一个事务内把目标置 `accepted`、写入 reviewer/time/review record，并更新 `Document.active_clean_version_id`、`clean_status/status`；active version 号只能单调递增。
- reject 只把目标从 `review_pending` CAS 为 `rejected` 并写审计记录；无论拒绝的是较新还是较旧版本，都不得清空、回退或改写已有 active pointer。
- 同一目标上的并发 accept/reject 恰有一个成功；不同版本并发 approve 经过 Document 锁串行化，最终 active 必须是成功接受版本中 version 最大者。

## 5. API 合同

以下路径沿用 T02/T04 最终确定的项目授权形式；若仍使用 `/api/sections/{sid}`，也必须从 `sid` 反查并校验真实项目。

- `GET /sections/{sid}` 的响应新增 `content_revision: integer`，并可返回当前租约的只读摘要 `lease: {id, user_id, expires_at} | null`。
- `POST /sections/{sid}/lease/acquire`：同一用户重试幂等；成功 `200` 返回 `id/section_id/user_id/acquired_at/expires_at`；他人持有时 `409 SECTION_LEASE_HELD`。
- `POST /sections/{sid}/lease/heartbeat` 请求为 `{ "lease_id": "uuid" }`；成功 `200`；过期、已释放或不属于当前用户时 `409 SECTION_LEASE_LOST`。
- `POST /sections/{sid}/lease/release` 请求为 `{ "lease_id": "uuid" }`；首次和重复释放均为 `204`，且不得影响其他 lease。
- `PATCH /sections/{sid}` 请求为 `{ "cleaned_markdown": "...", "expected_revision": 3, "lease_id": "uuid" }`；成功响应 revision 为 `4`。
- 保存时版本不符返回 `409 SECTION_VERSION_CONFLICT`，错误体至少含 `current_revision`；租约无效返回 `409 SECTION_LEASE_LOST`。
- `POST /sections/{sid}/submit` 请求至少包含 `expected_revision` 与 `lease_id`；服务端必须确保提交的是该 revision，成功后释放该 lease。
- `POST /projects/{pid}/documents/{did}/cleaning/merge?cleaning_job_id=...` 要求 `Idempotency-Key`，成功 `201` 返回 `id/version/source_revision_sha256/content_sha256/artifact_key/status`；相同 key 重放返回同一资源，不重复上传。
- 合并计算后任一来源 Section revision 已变化时返回 `409 CLEAN_SOURCE_CHANGED`，不得发布版本或改变 Document 状态；同文档版本/幂等键竞争由服务层转换为确定的成功重放或 `409`，不得泄漏为 `500`。
- `POST .../cleaning/final-review` 对目标执行 `review_pending` CAS；同一版本终审竞态返回 `409 CLEAN_VERSION_REVIEW_CONFLICT`，接受旧于 active 的版本返回 `409 CLEAN_VERSION_STALE`，错误体包含目标与 active version 号。
- final-review 的 reject 成功响应必须继续返回未变化的 `active_clean_version_id`；前端不得把“本次目标 rejected”解释为文档已无 active clean version。
- 未认证为 `401`，角色不足为 `403`，跨项目或不存在统一为 T02 规定的 `404`；不得用 `200` 携带失败信息。

所有错误码进入 OpenAPI，并由前端生成类型消费；不得靠匹配中文 `detail` 判断分支。

## 6. 前端合同

- 每次选择 Section 时生成请求代次并取消前一组详情、评论和 acquire 请求；只有“当前 section id + 当前代次”匹配的响应可更新状态。
- 编辑器仅在详情和租约均属于当前 Section 时可写；获取租约期间显示明确加载态，冲突时只读。
- `editedMarkdown` 与服务端基线不同即为 dirty。切换 Section、清洗来源或路由时显示“保存 / 放弃 / 留在当前页”；浏览器关闭使用 `beforeunload` 提示。
- 保存携带当前 `content_revision` 和 `lease_id`。成功后原子更新基线、revision 和 dirty；409 时保留本地文本，不自动覆盖，并提供“重新载入”和“复制本地内容”。
- 心跳失败或标签页恢复后发现 lease 过期，立即停止自动保存、切为只读并保留草稿；重新获取 lease 后仍须重新拉取 revision，不能直接提交旧基线。
- effect cleanup 只释放其捕获的 `lease_id`；旧 Section 的 cleanup 不得释放新 Section 的 lease。
- 保存/提交中的按钮防重复点击；网络超时视为结果未知，先重新读取 Section/lease 再决定是否重试。
- 一次“合并清洗版本”用户意图生成并复用一个幂等键；收到未知结果时先按该 key/版本列表查询，不得换 key 盲目再次合并。
- 合并返回 `CLEAN_SOURCE_CHANGED` 时保留当前编辑状态并刷新变更提示，由用户确认后发起新的合并意图。
- final-review 收到 stale/conflict 时刷新版本列表和 Document active pointer；不得本地覆盖赢家状态或自动重复提交。

## 7. 预期修改面

- `apps/api/app/models/section.py`
- `apps/api/app/models/cleaned_document_version.py`
- `apps/api/app/schemas/section.py`
- `apps/api/app/schemas/cleaned_version.py`
- `apps/api/app/services/section_service.py`
- `apps/api/app/services/clean_version_service.py`
- `apps/api/app/routers/sections.py`
- `apps/api/app/routers/documents.py` 的 merge 路径
- `apps/api/migrations/versions/` 新增一条迁移
- 对象存储 create-only/清理适配层及一致性审计脚本
- `apps/web/src/app/(dashboard)/projects/[id]/documents/[did]/clean/page.tsx`
- 前端生成 API 类型及对应后端集成、前端组件测试
- 根目录 `DevLog.md`（实施本卡时记录，本任务卡编写阶段不修改）

## 8. 依赖

- T02 提供 Section 到 Project 的对象级授权与角色检查。
- T04 提供结构化错误体、OpenAPI 类型生成和前端合同测试设施。
- T00 的 PostgreSQL/Redis 集成 fixture 用于并发与降级测试。

## 9. 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| 迁移前已有多条有效 lease | 预检、确定性保留、记录清理数量后再建唯一索引 |
| Redis 与数据库短暂不一致 | 数据库为真源；Redis 仅缓存且 value 携带 `lease_id` |
| 保存成功但响应丢失 | 客户端重新 GET，以 revision/内容判断结果，不盲目重复写 |
| 长时间编辑期间 lease 过期 | 提前心跳；失败即只读，重新获取后重基线 |
| dirty guard 阻塞正常导航 | 三选项对话框，并对未修改内容零打扰 |
| 并发合并得到同一 version 或对象 key | Document 行锁分配版本，数据库唯一约束与 UUID 对象 key 双重兜底 |
| Section 在对象上传期间变化 | 冻结 revision map，发布事务重新锁定并比较，不一致则拒绝发布 |
| 对象上传成功但数据库回滚 | 唯一 staging/key、清理记录与孤儿扫描；历史对象绝不覆盖复用 |

## 10. 实施步骤

1. 写迁移预检、重复租约清理、`content_revision` 与唯一索引。
2. 在 service 中实现 Section 行锁、原子 acquire、条件 heartbeat/release。
3. 用条件 UPDATE 实现租约与 revision 双校验，确保修订记录和正文同事务。
4. 更新 Pydantic/OpenAPI 合同和稳定错误码。
5. 改造前端请求取消、代次检查、dirty guard、只读及冲突恢复。
6. 改造合并服务：冻结 revision/hash、使用 UUID 对象 key、Document 行锁分配版本、幂等发布与失败清理。
7. 将 final-review 改为 Document/Version 固定锁序、`review_pending` CAS 和 active version 单调门禁。
8. 补齐编辑/租约/合并/终审并发集成测试、fake timer/延迟响应组件测试和 Redis/MinIO 故障测试。
9. 执行迁移往返、存量对象审计与全量质量门禁，并用中文更新 DevLog。

## 11. 自动化验收标准

1. 两个用户并发 acquire 同一 Section，恰好一个成功，另一个稳定返回 409；数据库最多一条未释放 lease。
2. 同一用户重复 acquire 返回同一 `lease_id`，不新增有效记录。
3. 旧 `lease_id` 的 heartbeat/release/save 均不能续期、释放或修改后来者的 lease/内容。
4. 两客户端从 revision 5 保存，只有一个变为 6；另一个得到 409，正文和修订历史无双写。
5. Redis 断开时，合法持租者仍按数据库合同保存；无租约者仍被拒绝。
6. A→B 快速切换且 A 响应最后到达，编辑器最终只显示 B；测试使用延迟 API mock 可重复复现。
7. dirty 状态切换、路由离开、租约丢失均保留本地文本并出现正确提示；选择“放弃”才清空。
8. Alembic `upgrade -> downgrade -> upgrade` 通过，重复 lease 回填 fixture 得到确定结果。
9. 20 个不同幂等键并发合并同一 Document，所有成功行的 version、version UUID 和 artifact key 唯一；对象内容 hash 与数据库一致，任何历史对象未被覆盖。
10. 20 个相同幂等键并发合并只得到一个版本和一个正式对象；其余请求稳定重放该结果，不出现 `500`。
11. 在内容计算与发布之间修改任一 Section，合并返回 `409 CLEAN_SOURCE_CHANGED`，不产生正式版本；上传失败、提交失败后的孤儿对象可被自动化清理验证移除。
12. 两个 reviewer 对同一 `review_pending` version 并发 accept/reject，只有一个成功；失败方得到 review conflict，终态、reviewer 与 review record 唯一一致。
13. 较新 version 与较旧 version 并发 accept 后，active pointer 不会回退：新版本先成功时旧版本得到 `CLEAN_VERSION_STALE`；旧版本先成功时新版本随后可前进为 active。
14. reject 任意 pending version（包括 version 高于 active）后，既有 active id/status/content 均不变化；迟到 reject 不能清空刚接受的 active。
15. `pytest`、Ruff、前端测试、TypeScript、ESLint 和 build 全部返回 0。

## 12. 停止条件

- T02 尚未给出 Section 的项目归属校验入口，导致本卡只能继续使用全局登录权限。
- 无法取得迁移前重复/过期 lease 的数量与处置许可，可能误释放真实活跃编辑者。
- 产品要求多人实时合并而不是单写者租约；应停止并另做协同编辑架构设计。
- 必须依赖不可用的 Redis 才能保证正确性，且无法改为 PostgreSQL 原子路径。
- 需要改变 Section 分派或终审语义才能完成，应拆出新卡而非扩张本卡。
- 存量 `CleanedDocumentVersion` 出现重复对象 key、对象缺失或 hash 冲突，且没有经确认的数据保留决策。
- 对象存储无法提供唯一 key/可验证 hash，且团队不接受数据库提交失败后短暂存在可清理孤儿对象的一致性模型。

## 13. 审查重点

- 唯一索引与行锁是否真正覆盖多进程并发，而非仅靠进程内变量。
- 所有写路径是否同时校验 lease、用户、项目、到期时间和 expected revision。
- 旧 cleanup 是否可能释放新 lease；Redis 删除是否 compare-and-delete。
- 冲突/超时是否保留用户文本，迟到响应是否有代次保护。
- 修订记录与正文是否同事务、revision 是否严格单调。
- CleanedDocumentVersion 是否在 Document 行锁下分配 version，artifact key 是否与可竞争的显示版本号解耦。
- revision map/hash 是否在发布前复核，对象写入失败或数据库回滚是否可能覆盖/伪造正式版本。
- final-review 是否按 Document→Version 固定顺序加锁，所有终审是否只允许 `review_pending` CAS，reject 是否绝不写 active pointer。
- 并发接受不同版本时 active version 是否严格单调，旧版本 stale 错误是否在事务内判断。

## 14. 完成定义

- 数据库、API 和前端合同均已实现并进入 OpenAPI 生成类型。
- 自动化验收全部通过，包含真实 PostgreSQL 双连接并发与 Redis 故障用例。
- 不再存在可复现的 A/B 串写、旧版本覆盖或旧 lease 释放新 lease。
- 并发合并不会产生重复版本号、重复正式产物或被覆盖的历史对象，且每个版本可由 revision map 与 hash 复核。
- 并发终审不会双写 review 结果或使 `active_clean_version_id` 回退/清空。
- 迁移、回滚和运维处置说明齐全，根目录 `DevLog.md` 已用中文记录实施与验证结果。
