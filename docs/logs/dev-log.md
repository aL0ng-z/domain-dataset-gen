# Development Log

> 本文件记录项目开发进度，每个阶段完成后更新。

---

## T06 版本化切分与 Token 预算（2026-08-03）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `libs/splitters/` | `canonical.py`（splitter/tokenizer 版本标识 + source/output hash）；`token_util.py`（tiktoken 字节对齐切分防 U+FFFD）；`hybrid_heading.py` 修复超长段落/overlap 后超预算（正文预算 = max_tokens - overlap - separator - joiner，单 piece 超限继续递归/hard split） | 已完成 |
| `apps/api/app/models/chunk_set.py` | 新增 version/is_legacy/idempotency_key/source/output hash/splitter_version/error_message/completed_at/task_id + 唯一/部分唯一/CHECK 约束 | 已完成 |
| `apps/api/app/models/chunk.py` | chunk_set_id 收紧 NOT NULL + 唯一(chunk_set_id,ordinal) + token_count>0/ordinal>=0 CHECK + 索引 | 已完成 |
| `apps/api/app/models/config.py` | ChunkProfile DB CHECK（max_tokens>0、overlap>=0、overlap<max_tokens） | 已完成 |
| `apps/api/migrations/versions/t06_versioned_chunking.py` | chunk_set_status 枚举重建含 failed/cancelled；ChunkSet 版本化字段；legacy 回填（无法绑定 T07 持久 Task 或缺必填字段的旧 set 置 legacy_unverified；孤儿 Chunk 按 Document 建隔离 legacy set 稳定重排 ordinal）；非法 active pointer 清空不猜测；downgrade 预检 failed/cancelled 停止 | 已完成 |
| `apps/api/app/services/chunk_set_service.py` | Document 行锁内分配 version；幂等复核；冻结 config_json；事务内创建 ChunkSet + T07 queued Task | 已完成 |
| `apps/api/app/workers/chunk_worker.py` | chunk_document:v2：只消费 accepted+active 清洗版本，冻结 config/tokenizer，worker 复算 token_count 超限即集合 failed，发布前重锁 Document 复核来源；成功路径原子发布，失败/取消经 terminal hook 收敛 ChunkSet | 已完成 |
| `apps/api/app/workers/execution.py` / `runner.py` | ExecutionContext 增加 set_terminal_hook；runner 失败/取消回滚后调用业务终态钩子 | 已完成 |
| `apps/api/app/routers/*.py` | POST chunk（Idempotency-Key 必填/cleaned_version 校验/幂等复用/409 并发）；GET chunk-sets 历史；GET chunks 默认 active set；PATCH completed set 409 不可变 | 已完成 |
| `apps/web` | 发起切分幂等 key 复用；活跃切分禁用按钮；Chunk 列表页 active set + 版本历史只读切换 + 失败展示；详情页版本展示与不可变说明 | 已完成 |
| 测试 | `tests/unit/test_chunk_splitter.py`（16 项）+ 前端 `chunk-versioning.test.tsx`（4 项） | 已完成 |

### 设计决策

- **版本号在 Document 行锁内分配**：不允许无锁 `MAX(version)+1`，避免并发完成时最后写入者获胜；幂等复核也在锁内，同 key 命中直接重放。
- **overlap 纳入总预算**：正文预算 = max_tokens - overlap - separator(overlap 标记) - joiner(空行)，任何最终 Chunk 超过 max_tokens 都使整个集合 failed，绝不截断静默发布。
- **tiktoken 字节对齐切分**：cl100k_base 对 CJK 用字节碎片 token，直接按 token 边界 decode 会产生 U+FFFD；`token_util.hard_split`/`clean_suffix` 用 `decode_single_token_bytes` 做字节级对齐，只在完整 UTF-8 字符边界切分/取 overlap。
- **ChunkSet 与 Task 同事务创建**：ChunkSet 绑定首次派发 Task（task_id 唯一约束），retry 通过 `retry_of_task_id` 链追溯而不覆盖它；切分只经 T07 dispatcher 执行，移除请求内 BackgroundTasks。
- **发布原子性与终态收敛**：成功路径业务写入（staging Chunk + completed set + active pointer）由 runner 完成事务原子提交；失败/取消 runner 回滚 staging 后，terminal hook 用 CAS 把 ChunkSet 收敛为 failed/cancelled，绝不覆盖已完成产物。
- **CLEAN_VERSION_STALE 语义**：发布前重锁 Document 复核 `active_clean_version_id` 仍等于来源；T05 终审推进时本次切分失败，不覆盖新 active 链路。
- **legacy 回填诚实性**：无法明确绑定 T07 持久 Task 或缺失必填字段的迁移前 set 一律 `is_legacy=true` + `summary_json.provenance=legacy_unverified`，不伪造 task_id/溯源；孤儿 Chunk 按 Document 建隔离 legacy set 稳定重排 ordinal，Chunk id 不变。

### 验证状态

- 后端 splitter 单元测试 16 项通过（预算/overlap/边界/确定性/hash/Unicode 安全）。
- 迁移往返 `upgrade head → downgrade 58918ea257fd → upgrade head` 通过；专用库验证 legacy/orphan/active-pointer 回填语义正确。
- 前端 60 项测试通过（新增 4 项切分版本合同）；lint/tsc/api:check/build 通过。

---

## T05 清洗编辑并发与租约（2026-08-03）

## T07 任务生命周期、派发、重试与取消（2026-08-03）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `apps/api/app/models/section.py` | sections 新增 `content_revision`（CHECK 非负）；SectionLease 部分唯一索引 `(section_id) WHERE released_at IS NULL` + `(section_id, expires_at)` 索引 + `expires_at > acquired_at` CHECK；SectionRevision 新增 `from_revision/to_revision` + 连续约束；Section.lease 只读关系 | 已完成 |
| `apps/api/app/models/cleaned_document_version.py` | 新增 `source_revision_map/source_revision_sha256/content_sha256/merge_idempotency_key` + `UNIQUE(document_id, version)` 保留、`UNIQUE(document_id, merge_idempotency_key)`、`UNIQUE(artifact_key)` | 已完成 |
| `apps/api/app/services/section_service.py` | 原子 acquire（Section 行锁 + 过期租约清理 + 同用户幂等 + 唯一索引兜底）；条件 heartbeat/release 绑定 `lease_id`；保存/提交双校验（lease + revision）；Redis 仅缓存且 compare-and-delete | 已完成 |
| `apps/api/app/services/clean_version_service.py` | 合并两阶段：计算冻结 revision 向量/hash → Document 行锁内幂等复核、稳定序锁 Section 复核、锁内分配版本、UUID 对象 key create-only 上传；终审 Document→Version 固定锁序 + `review_pending` CAS + active 单调门禁 | 已完成 |
| `apps/api/app/schemas/*` + 路由 | `SectionResponse.content_revision`/`lease` 摘要；保存/提交/心跳/释放请求体含 `lease_id`/`expected_revision`；merge 要求 `Idempotency-Key`；6 个稳定错误码进入 OpenAPI | 已完成 |
| `apps/api/migrations/versions/` | `merge_t02_t03_heads`（收敛 T02/T03 双 head）+ `t05_clean_edit_concurrency_lease`（重复租约预检清理、索引/约束/列、审计计数）；upgrade→downgrade→upgrade 往返通过 | 已完成 |
| `apps/web/src/hooks/use-cleaning-workbench.ts` | 请求代次/AbortController、仅当前 section+代次可更新、acquire/心跳/释放绑定 lease_id、保存带 revision+lease、409 保留本地文本、冲突对话框、自动保存防抖、beforeunload、cleanup 只释放自身 lease | 已完成 |
| `apps/web/src/app/.../clean/page.tsx` | dirty guard 三选项对话框、租约丢失/冲突只读、合并幂等键复用、final-review stale/conflict 刷新赢家状态 | 已完成 |
| `apps/web/src/lib/api.ts` | 支持 `headers`（Idempotency-Key） | 已完成 |
| 测试 | `tests/integration/test_clean_edit_concurrency.py`（12 项验收 1-14）+ `use-cleaning-workbench.test.ts`（6 项 A→B 切换/冲突/只读/cleanup）+ 前端 50 项回归 | 已完成 |
| 审计 | `scripts/clean_version_audit.py` 存量重复版本/artifact key/hash 字段一致性审计 | 已完成 |

### 设计决策

- **数据库为唯一正确性来源**：租约获取在 Section 行锁保护的事务内原子完成，Redis 仅缓存（值携带 `lease_id`），Redis 故障只降级缓存、绝不放宽写入门禁。保存/提交同时校验租约所有权、期限与 `expected_revision`，修订记录与正文同事务，`content_revision` 严格 +1。
- **合并两阶段 + 幂等**：计算阶段无锁读 Section 生成规范化 revision 向量/hash，预生成 version UUID；发布阶段先锁 Document 行，锁内幂等复核（同 key 命中直接重放、不重复上传），再稳定顺序锁 Section 复核向量，锁内分配下一 version，最后 create-only 上传 UUID 对象 key。对象 key 与显示版本号解耦，杜绝历史对象被覆盖。
- **终审并发**：固定 Document→Version 锁序避免死锁；`review_pending` CAS + 行锁串行化，并发 accept/reject 恰一个成功；approve 校验目标旧于 active 则 `CLEAN_VERSION_STALE`；reject 绝不清空/回退 active pointer。
- **identity-map 陈旧读修复**：SQLAlchemy 同一 session 内 `FOR UPDATE` 重新查询会命中 identity map 返回加锁前的旧状态，导致并发 CAS 失效；统一加 `execution_options(populate_existing=True)` 强制刷新。
- **401 头透传**：全局异常处理器此前丢弃 `HTTPException.headers`，`WWW-Authenticate: Bearer` 丢失；补透传并保留业务 422 的 message。
- **测试库 schema 漂移**：迁移 smoke 与 pytest 共用 `datasetgen_test`；`create_all` 不更新已存在表。fixture 的 `create_parse_job` 填充 T03 snapshot 字段以满足迁移后 CHECK 约束；测试前保持迁移到 head 的 schema。

### 验证状态

- 后端 T05 专项集成测试 12 项通过（并发 acquire/revision/合并幂等/终审单调）。
- 前端 hook 测试 6 项 + 全量 56 项通过；`npm run lint`、`npm exec tsc -- --noEmit`、`npm run api:check`、`npm run build` 通过。
- 迁移往返 `upgrade head → downgrade -1 → upgrade head` 通过；重复租约预检清理审计计数输出。
- 审计脚本 `scripts/clean_version_audit.py` 无重复版本/artifact key/hash 字段缺失。
- 全量 `python -m pytest -q` 210 项通过（在 T05 迁移 head 的干净测试库上；并行 worktree 会将测试库迁移到 T07 等后续 head，导致 `tasks.next_run_at` 等列缺失而破坏 `create_task`，属跨 worktree schema 污染而非本卡缺陷）。
- 顺带修复两个预存问题：`parser_profile` GET 跨项目可读（补项目作用域校验 404）；`CuratedItemUpdate` 拒绝 `revision_note`（服务端已支持，schema 补字段）。
- `python -m ruff check apps/api libs tests scripts` 通过；`python scripts/export_openapi.py --check` 通过。

---

| `apps/api/migrations/versions/t07_task_lifecycle.py` | Task 扩展（handler/payload/state_version/attempt/lease/cancel/retry）+ task_attempts 审计表 + task_status 枚举 cancelling + 双 head merge | 已完成 |
| `apps/api/app/workers/queue.py` | 原子 create/claim（SKIP LOCKED）/heartbeat/CAS transition/reaper 回收 | 已完成 |
| `apps/api/app/workers/execution.py` | ExecutionContext：checkpoint 校验 run token/lease/timeout/cancel；HandlerRegistry 稳定名+version 分派 | 已完成 |
| `apps/api/app/workers/runner.py` | 独立 runner 轮询持久队列；成功路径业务写入+completed 原子提交，失败/取消回滚后单独落库 | 已完成 |
| `apps/api/app/workers/*_worker.py` | 7 个 handler（parse/clean/chunk/generate_single/generate_batch/export_dataset/export_benchmark）持久化 | 已完成 |
| `apps/api/app/routers/*.py` | 触发 API 移除 BackgroundTasks，改为事务内创建持久 Task；接入 Idempotency-Key | 已完成 |
| `apps/api/app/routers/tasks.py` | GET 详情/attempts、POST cancel/retry（仅 failed + Idempotency-Key） | 已完成 |
| `apps/web` | 任务中心展示 cancelling/attempt/retry 后继/state_version 乱序丢弃/轮询兜底 | 已完成 |
| `infra/docker/Dockerfile.worker` + compose | 独立 worker 服务 | 已完成 |
| `docs/runbooks/task-lifecycle.md` | 部署/监控/故障/迁移回滚 runbook | 已完成 |
| 测试 | `tests/integration/test_task_lifecycle.py` / `test_task_runner.py` / `test_task_api.py`（20 用例） | 已完成 |

### 设计决策

- **PostgreSQL 持久队列替代 FastAPI 进程内 BackgroundTasks**：Task 成为可恢复真实执行记录，
  API 重启不丢任务；runner 独立进程轮询领取。
- **至少一次执行 + 幂等 handler**：不虚假承诺 exactly-once；可重试错误按 Policy 退避，
  永久错误只执行一次；未知 handler 永久失败。
- **run token + 数据库 CAS 防迟到写**：心跳/状态提交携带 run token，过期 worker 影响 0 行；
  所有转换 `WHERE id=? AND status=? AND state_version=?`。
- **取消在发布前设门禁**：handler 在外部调用/批次循环/发布事务前 checkpoint；
  失败/取消路径回滚全部业务写入后再单独落库，取消前不留下正式业务产物。
- **父任务聚合**：全部子任务终态才 completed，任一失败则 failed；父取消向子任务传播。
- **双 head 迁移收敛**：t07 同时依赖 T02/T03 两个 head 做 merge，修复 master `upgrade head`
  报 Multiple head 的问题；mergepoint 的 `downgrade -1` 歧义改为显式目标 58918ea257fd。

### 验证状态

- 后端：`tests/integration/test_task_lifecycle.py` / `test_task_runner.py` / `test_task_api.py`
  共 20 用例通过。
- 迁移：upgrade head -> downgrade 58918ea257fd -> upgrade head 往返通过。
- Ruff：`python -m ruff check apps/api libs tests` 通过。
- 前端：`npm run lint`、`npm exec tsc -- --noEmit`、`npm run api:check`、`npm test -- --run`（50 passed）、
  `npm run build` 全部通过。
- 注：master 既有 10 个失败用例（T01/T02/T03/T04 领域：parser-profiles/auth tokens/role matrix/
  protected-token-types/parse_job snapshot）在干净 master 上同样失败，与本卡无关。

---

## T03 解析器出站与凭证安全（2026-08-01）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `libs/parsing/parsing/egress.py` | URL 规范化、hostname IDNA/尾点规范化、IP 分类（含混淆 IPv4/云元数据/IPv6）、origin 与受控域后缀匹配 | 已完成 |
| `libs/parsing/parsing/transport.py` | 统一安全 HTTP 传输层：DNS 固定防 rebinding、禁止自动重定向（有界逐跳重验）、敏感头跨 origin 不转发、响应体/解压大小限制、记录型 fake | 已完成 |
| `libs/parsing/parsing/snapshot.py` | parser_options 字段白名单与禁止字段递归扫描、规范 JSON SHA-256、快照/URL/header 递归脱敏 | 已完成 |
| `apps/api/app/security/registry.py` | 服务端 ParserEndpointRegistry：启动校验重复 ref/scheme/凭证绑定/网络区域，只读安全投影 | 已完成 |
| `apps/api/app/models/parse.py` + Alembic | ParseJob 冻结快照字段、不可变触发器、CHECK 约束、legacy_unavailable 迁移回填 | 已完成 |
| `apps/api/app/services/parse_freeze_service.py` | ParseJob 创建时原子冻结 profile/policy 快照与 SHA-256 | 已完成 |
| `apps/api/app/workers/parse_worker.py` | 执行前复核快照/hash/秘密扫描，全局 Token 最晚注入，错误消息脱敏 | 已完成 |
| `apps/api/app/schemas/config.py` + CRUD | ParserProfile 收紧为 endpoint_ref+白名单，递归拒绝网络/秘密字段，端点只读列表 | 已完成 |
| `libs/parsing/parsing/*_parser.py` | MinerU/PaddleOCR/local parser 全部迁移到统一安全 transport | 已完成 |
| `scripts/migrate_parser_profiles.py` | 存量 profile 受控迁移：dry-run/check/migrate，未知端点 fail closed | 已完成 |
| `apps/web` | 设置页移除自由网络字段，改为服务端端点选择 + 前端安全测试 | 已完成 |
| 文档 | `apps/api/.env.example` registry 配置、`docs/runbooks/parser-egress-security.md`、README 验收命令、本日志 | 已完成 |

### 设计决策

- **endpoint_ref 是不透明稳定 ID**：ParserProfile 只保存 endpoint_ref 与功能参数，
  真实 URL/Token 由服务端 registry 派生；数据库不保存 registry 的 URL 或 Token。
- **统一安全传输层位于 libs/parsing**：使所有解析器（remote / managed-local）共享同一
  安全边界，apps/api 通过 `app.security` 复用同一实现，避免两层各实现一份。
- **凭证只在请求局部最晚注入**：worker 从冻结快照读功能参数 + registry 安全上下文，
  全局 Token 仅注入到绑定 credential origin 的请求，signed upload/archive 请求不带
  Authorization。
- **ParseJob 原子冻结**：同一事务写入 profile/policy 快照/hash/ref/version/frozen_at；
  数据库触发器禁止 UPDATE 改写任一快照字段，状态更新不受影响。
- **DNS rebinding 防护**：连接使用已验证并固定的解析结果（pinned_ips），全部 A/AAAA
  必须为允许公网地址，任一危险整体拒绝。
- **存量迁移 fail closed**：仅精确匹配 registry base_url 的旧 URL 映射为 endpoint_ref；
  未知端点使迁移写入前整体失败，输出 profile ID 与 URL hash。

### 验证状态

- 后端：`python -m pytest -q` 全量通过；T03 专项测试
  `tests/unit/security/test_parser_egress.py`、`tests/contract/test_parser_profiles.py`、
  `tests/integration/test_parse_job_config_snapshot.py`、
  `tests/integration/test_parse_job_snapshot_migration.py` 全部通过。
- Ruff：`python -m ruff check apps/api libs tests` 通过。
- 迁移：`migrate_parser_profiles.py` 实测 dry-run/check/migrate；check 返回 0。
- 迁移 smoke：`run-migration-smoke` upgrade head → downgrade → upgrade head 通过。
- 前端：`npm exec tsc -- --noEmit`、`npm run lint`、`npm test -- --run`（15 passed）、
  `npm run build` 全部通过。

---

## 项目总览

| Release | 名称 | 状态 | 备注 |
|---------|------|------|------|
| R1 | Lab Pilot | 测试进行中 | 主链路 MVP；远程 API 可用；本地 MinerU/MLX 与 PaddleOCR-VL 本地 API 均已作为 ParseJob 解析方式接入；解析入口与清洗来源隔离、清洗按页 Section 切分已修整；Windows conda 启动脚本默认不安装依赖 |
| R1+ | Slice 1: Clean 协作升级 | 后端冒烟通过，前端 UI 待目检 | 数据模型扩展 + Clean 分派/合并/终审工作流 |
| R2 | Lab Team | 未开始 | 多人协作、评测中心 |
| R3 | Quality Automation | 未开始 | 质量自动化 |
| R4 | Optional Extensions | 未开始 | 多轮对话、Arena 等 |

---

## T00 自动化质量基线与测试底座（2026-08-01）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `pyproject.toml` / `apps/api/pyproject.toml` | pytest pythonpath/markers/coverage 配置；pytest-cov 依赖；uv.lock 同步 | 已完成 |
| `scripts/test-backend.*` / `test-frontend.*` / `test-infra.*` | 一键质量门禁与测试基础设施启停脚本 | 已完成 |
| `infra/docker/docker-compose.test.yml` + `.env.test.example` | 隔离 PostgreSQL(55432)/Redis(56379)/MinIO(19000) 测试环境 | 已完成 |
| `tests/safety.py` + `tests/unit/test_safety.py` | 强制安全校验：TESTING=1、数据库名白名单、Redis/MinIO 隔离前缀 | 已完成 |
| `tests/conftest.py` | 双项目、admin/reviewer/editor/viewer 四角色、Token helper、资源工厂；每测试 TRUNCATE 清理 | 已完成 |
| `tests/integration/` | 登录/me/refresh、401/403、项目隔离最小集成测试 | 已完成 |
| `tests/contract/` | LLM 与 MinIO adapter fake 契约测试（不请求真实服务） | 已完成 |
| `apps/web` | Vitest + Testing Library + 统一 API mock 层；api/ws/登录页测试 | 已完成 |
| Ruff / ESLint | 全部阻断项机械清理，形成零错误零新增 warning 基线 | 已完成 |
| `.github/workflows/ci.yml` | 后端 lint+test+覆盖率、迁移 smoke、前端 lint+tsc+test+build | 已完成 |
| `scripts/run-migration-smoke.*` | 空库 upgrade head → downgrade → upgrade head | 已完成 |

### 设计决策

- **测试隔离**：测试数据库固定为 `datasetgen_test`，由 `tests/safety.py` 强制校验
  （`TESTING=1`、数据库名白名单、Redis/MinIO 前缀），不满足即拒绝执行 destructive
  fixture。集成测试每测试结束对全部业务表 `TRUNCATE CASCADE` 并清理测试 Redis key，
  经实测验证结束后无残留业务数据。
- **事件循环**：pytest 配置 session 级 asyncio 循环，避免跨测试引擎/连接释放问题。
- **事务策略**：由于仓库路由器直接调用 `db.commit()`，集成测试不依赖 begin/rollback
  事务包裹，改用 TRUNCATE 清理。
- **前端 mock**：统一 API mock 层按真实 HTTP 状态码与 JSON 响应运行，未注册路由返回
  404，保证测试不会误连真实服务；支持延迟、AbortSignal、401→refresh 序列与 WebSocket mock。
- **lint 清理**：Ruff 的 UP042（str+Enum→StrEnum）、B904（raise...from e）、SIM105、
  E402、N817 等全部机械修复；前端 `react-hooks/set-state-in-effect` 通过延迟到下一
  事件循环触发请求解决（行为等价且避免卸载后 setState）。未改动任何业务语义。

### 验证状态

- 后端：`python -m ruff check apps/api libs tests` 通过；`python -m pytest -q` 45 passed。
- 前端：`npm run lint` 0 problems；`npm exec tsc -- --noEmit` 通过；
  `npm test -- --run` 11 passed；`npm run build` 通过（Google 字体网络偶发导致失败，
  重试即成功，与本次改动无关）。
- 迁移 smoke：`run-migration-smoke.ps1` 在 `datasetgen_test` 上完成
  upgrade head → downgrade -1 → upgrade head。
- 数据隔离：测试结束后实测 36 张业务表 0 行、Redis 无残留 key。

---

## T01 JWT 令牌语义与前端认证状态（2026-08-01）

## T04 API/前端合同单一事实源（2026-08-01）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `apps/api/app/core/jwt.py` | 统一 JWT 解码/签发模块：TokenType、encode/decode_token、resolve_user_id | 已完成 |
| `apps/api/app/services/auth_service.py` | access 明确 type=access；refresh 保持 type=refresh；refresh_tokens 只接受 refresh | 已完成 |
| `apps/api/app/dependencies.py` | get_current_user 只接受 access token；401 携带 WWW-Authenticate: Bearer | 已完成 |
| `apps/api/app/routers/documents.py` | PDF 入口复用统一 access 校验，删除重复 jose.decode | 已完成 |
| `apps/api/app/ws/task_ws.py` | WebSocket 复用统一 access 校验；未认证关闭码 4401 | 已完成 |
| `apps/api/app/schemas/auth.py` | TokenResponse/RefreshRequest 字段说明补充 | 已完成 |
| `apps/web/src/lib/auth.ts` | TokenStore 唯一读写入口 + single-flight 刷新 + 令牌代数 + 跨标签页同步 | 已完成 |
| `apps/web/src/lib/api.ts` | 401 仅对非登录/刷新请求单次刷新重试；失败原子清理 | 已完成 |
| `apps/web/src/lib/ws.ts` | 经 TokenStore 取令牌；轮换重连、logout 停止重连 | 已完成 |
| `apps/web/src/contexts/auth-context.tsx` | bootstrapping/authenticated/refreshing/anonymous 状态机 | 已完成 |
| `tests/integration/test_auth_tokens.py` | access/refresh 分离、负向矩阵、停用用户、日志脱敏、WS 订阅 | 已完成 |
| `tests/integration/test_protected_token_types.py` | 受保护入口矩阵、WWW-Authenticate、jwt.decode 静态守卫 | 已完成 |
| `apps/web` 测试 | auth/api/ws/auth-context 并发、乱序、退出回归测试 | 已完成 |
| `tests/conftest.py` | 集成测试清理改为单语句 TRUNCATE，消除跨测试死锁 | 已完成 |

### 设计决策

- **单一令牌语义**：access token 明确 `type=access`，refresh 保持 `type=refresh`；
  全仓仅 `app/core/jwt.py` 负责解码与签发，HTTP/PDF/WebSocket 一律复用
  `decode_token`/`resolve_user_id`，静态搜索守卫验证无业务入口直接调用 `jwt.decode`。
- **声明校验**：python-jose 的 `options.require` 不强制声明存在，`decode_token`
  在解码后手工校验 sub/type/iat/exp 存在、type 与预期一致；sub 的 UUID 解析失败
  统一按 401 处理，不产生 500。
- **用户事实源**：令牌解码后仍从数据库确认用户存在且启用；停用后未过期 token 立即失效。
- **日志脱敏**：认证失败仅记录不带 token 的告警文案，测试断言日志不含原 token。
- **前端 single-flight**：`refreshToken` 共享同一 Promise，并发 401 只发一次刷新；
  令牌代数（generation）保证晚到的旧刷新响应不覆盖新登录会话；失败仅触发一次
  原子清理与认证失败回调，路由守卫据此跳登录页。
- **前端状态机**：AuthContext 区分 bootstrapping/authenticated/refreshing/anonymous，
  避免初始化时误跳登录页；storage 事件同步跨标签页登录/刷新/退出。
- **部署影响**：历史缺少 type 声明的 token 全部失效，发布后用户需重新登录
  （与任务卡"明确不做"一致，不引入兼容后门）。
- **测试底座修复**：原 `db_session` 清理按表循环 `TRUNCATE CASCADE`，逐表获取
  ACCESS EXCLUSIVE 锁，在连续多个 org-based 集成测试时与下一测试的未提交 INSERT
  并发形成 PostgreSQL 死锁（原有测试即可复现）。改为单语句 TRUNCATE 一次声明全部
  业务表，PostgreSQL 原子获取全部锁，彻底消除跨测试死锁。

### 验证状态

- 后端集成测试：`python -m pytest -q tests/integration/test_auth_tokens.py
  tests/integration/test_protected_token_types.py` 21 passed；
  与既有 `test_auth_login_flow.py` 合并运行 28 passed；全仓 `python -m pytest -q`
  66 passed。
- Ruff：`python -m ruff check apps/api/app/services/auth_service.py
  apps/api/app/dependencies.py apps/api/app/routers/auth.py apps/api/app/ws/task_ws.py tests`
  全部通过。
- 前端：`npm test -- --run` 32 passed（新增 29 用例）；
  `npm run lint` 0 problems；`npm exec tsc -- --noEmit` 通过；
  `npm run build` 通过。
- 全仓搜索除 `app/core/jwt.py` 外不存在业务入口直接调用 `jwt.decode`。
- 验收命令逐项：
  - access 可访问 /me、refresh 对 /me 稳定 401：`test_access_can_me_refresh_cannot`
  - refresh 对业务 API/PDF/WS 全部拒绝且不建订阅：`test_refresh_rejected_*`、
    `test_refresh_token_ws_rejected_without_subscription`
  - 刷新负向矩阵（缺 type/过期/伪造/非 UUID sub）：`test_refresh_accepts_only_refresh_token`
  - 停用用户两类 token 即时失效且日志无 token：`test_deactivated_user_*`
  - Authorization 缺失/格式错误/算法不匹配参数化：`test_protected_endpoint_auth_header_variants`
  - 20 并发 401 只 1 次刷新：`auth.test.ts / api.test.ts`
  - 旧响应不覆盖新会话：`auth.test.ts 令牌代数`
  - WS 令牌轮换重连/logout 停止重连：`ws.test.ts`

---

## T02 项目资源对象级授权（2026-08-02）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `apps/api/app/authz.py` | 集中式 `check_project_member` + `ProjectResourceResolver`（SQL join 校验归属链）、`authorize_flat_resource`、`verify_project_chain` | 已完成 |
| `apps/api/app/dependencies.py` | `require_project_member` 委托 authz 模块，语义不变 | 已完成 |
| 嵌套路由（documents/tasks/config/prompt_templates/curated_items/datasets/benchmarks/exports） | 全部 GET/PATCH/DELETE/action 经 scoped resolver；请求体外键引用同项目校验 | 已完成 |
| 平铺路由（sections/chunks/candidates/cleaned-versions） | 经归属链反查 project_id 后校验成员/角色再 scoped load | 已完成 |
| PDF | 移除 query token，仅 Bearer + 项目 viewer；`Cache-Control: private, no-store`；授权先于 MinIO | 已完成 |
| WebSocket | accept/订阅前完成 token + 启用用户 + 项目 viewer；广播前复核；撤权先关闭(4403)再发送；停用用户 4401 | 已完成 |
| 后台任务 worker | parse/clean/chunk/generate/export 复核 task/资源/配置项目链，不一致标记失败无部分写入 | 已完成 |
| 前端 | `api.getBlob` 携带 Authorization 下载 PDF Blob + revoke；WS 4401 走认证恢复、4403 停止重连 | 已完成 |
| 测试 | `tests/integration/authorization/` 49 用例 + PDF 4 + WS 5 + 静态合同 2；前端新增 WS 4401/4403 与 PDF 授权 | 已完成 |
| 迁移 | `52eb455d64d3` 归属链 join 索引；upgrade→downgrade→upgrade 通过 | 已完成 |
| 审计 | `scripts/data-ownership-audit.py` 存量孤儿/跨项目引用审计通过 | 已完成 |
| 文档 | `docs/authorization-matrix.md` 权限矩阵 + OpenAPI Bearer 关联静态校验 | 已完成 |

### 设计决策

- **对象归属是唯一授权事实**：任何客户端提供的父子 ID（did/cid/bid/tid 等）都不
  作为授权依据；所有查询带项目谓词（join/EXISTS），资源不存在或不属于 pid 统一
  404，不泄露哪一个 ID 存在。
- **admin 只绕过成员/角色，不绕过路径绑定**：`check_project_member` 对全局 admin
  直接返回，但资源归属校验在 resolver 中独立执行，admin 用错误 pid 访问真实资源
  仍 404。
- **平铺路由反查项目**：sections/chunks/candidates/cleaned-versions 无 URL pid，
  通过 `Document.project_id` 外键链反查唯一项目后复用同一授权函数。
- **冗余外键一致性**：Chunk.section_id 与其 document_id 指向不同 Document 时拒绝
  访问并记录安全告警（任务卡 §4）。
- **WS 跨事件循环**：授权查询使用短生命周期 NullPool engine（`_ws_auth_session`），
  避免 TestClient 等独立线程循环复用全局连接池导致 asyncpg InterfaceError。
- **并发线性化**：广播前逐一复核成员与启用状态；撤销提交后的新请求全部 403；
  WS 已连接后撤权，先关闭(4403)再发送，客户端收不到事件。

### 验证状态

- 后端授权套件：`python -m pytest -q tests/integration/authorization
  tests/integration/test_pdf_authorization.py
  tests/integration/test_task_websocket_authorization.py` 全部通过。
- 迁移 smoke：空库 `upgrade head → downgrade -1 → upgrade head` 通过。
- 存量数据审计：`scripts/data-ownership-audit.py` 未发现孤儿或跨项目引用。
- OpenAPI：134/137 操作关联 Bearer（另 3 个为公开的 login/refresh/health）。
- 前端：`npm test -- --run` 38 passed（新增 PDF Blob 授权 + WS 4401/4403）；
  `npm run lint` 0 problems；`npm exec tsc -- --noEmit` 通过；
  `npm run build` 通过。

---

| `libs/domain/domain/schemas.py` | 统一错误 envelope（ErrorResponse/ValidationErrorResponse）、`RequestSchema(extra=forbid)`、参数化 `PaginatedResponse[T]` | 已完成 |
| `apps/api/app/errors.py` | 全局异常映射：业务错误→ErrorResponse（稳定 code），422→ValidationErrorResponse（loc/msg/type），500 不泄漏堆栈 | 已完成 |
| `apps/api/app/openapi.py` | 定制 OpenAPI：错误模型注入 components、每个操作注入 401/403/404/409/422/500 响应 | 已完成 |
| `apps/api/app/routers/*` | 全部前端调用路由补齐稳定 `operation_id`；Dataset items / Benchmark cases 由裸数组改为 `PaginatedResponse[T]`；202 异步响应声明模型；请求模型 `extra=forbid` | 已完成 |
| `apps/api/app/schemas/*` | Candidate/CuratedItem `content`、`review_evidence_spans`、`source_pages` 声明为 JSON object（非字符串） | 已完成 |
| `scripts/export_openapi.py` | 确定性 OpenAPI 导出（不连数据库/Redis/MinIO）+ `--check` 漂移门禁，提交规范化快照 `apps/api/openapi.json` | 已完成 |
| `apps/web` | `openapi-typescript` 生成 `src/lib/api/generated.ts`；类型化 client（`api.get/post/patch/delete` 按路由模板推导 path/query/body/response）；ApiError 判别联合；`formatJsonPreview`/`parseJsonObject` helper | 已完成 |
| `apps/web/src/app/.../projects/` | 全部页面迁移到生成类型，删除手写镜像接口与 `api.get<T>()` 覆盖；JSON 字段按 object 展示 | 已完成 |
| `apps/web/scripts/generate-api.mjs` / `check-api.mjs` | `npm run api:generate` / `api:check`（漂移 + 旧合同模式静态检查） | 已完成 |
| `tests/contract/test_openapi.py` | operationId 唯一/稳定、分页参数化、错误 envelope 注入、JSON 字段类型、生成确定性 | 已完成 |
| `tests/contract/test_core_response_shapes.py` | 运行时响应形状：Dataset/Benchmark 分页空集/单页/越界页、content object、旧字段 422、401/403/404 envelope | 已完成 |
| `apps/web/src/lib/api-contract.test.ts` | 前端 mock 合同测试：四键分页、JSON 渲染、422 validation、业务 code 判别、204 void | 已完成 |
| `.github/workflows/ci.yml` | 增加 OpenAPI 导出漂移检查、合同测试、前端 `api:check` 门禁 | 已完成 |

### 设计决策

- **OpenAPI 为唯一事实源**：前端类型与类型化 client 全部由 `apps/api/openapi.json` 生成；任何后端 schema/路由变更后开发者漏跑 `npm run api:generate`，`api:check` 会在 CI 可靠失败。
- **分页合同**：所有可增长集合统一 `{"items":[],"total":0,"page":1,"page_size":20}`；Dataset items / Benchmark cases 由裸数组切换为参数化分页，`total` 为过滤后总数，空页/越界页仍返回四键。
- **JSON 字段**：Candidate/CuratedItem `content`、`review_evidence_spans`、`source_pages` 在 schema 中声明为 object；页面用 `formatJsonPreview` 只读展示，编辑用 `JSON.stringify` 初始化并在发送前 `JSON.parse` 校验。
- **错误 envelope**：业务错误统一 `{"code","message","context","request_id"}`，前端按稳定 `code` 判别，不匹配中文 message/detail；422 保留 `ValidationErrorResponse`（loc/msg/type）供字段定位。响应不泄漏堆栈/SQL/Token。
- **请求严格性**：请求模型统一 `extra=forbid`，旧请求字段（如字符串 `content`、`template_id`）返回 422 而非静默忽略。
- **类型化 client**：保留 access-token、refresh、AbortSignal、超时与 FormData 上传行为；成功响应从 operations 推导（排除错误 envelope），页面禁止 `api.get<T>()` 手写覆盖。
- **静态检查**：`check-contract-patterns.mjs` 禁止 `api.<T>()`、`as unknown as`、`as never` 绕过。

### 验证状态

- 后端：`python -m ruff check apps/api libs tests scripts` 通过；`python -m pytest -q tests/contract/test_openapi.py tests/contract/test_core_response_shapes.py` 27 项通过；全量 `python -m pytest -q` 72 项通过。
- 前端：`npm run lint` 0 problems；`npm exec tsc -- --noEmit` 通过；`npm test -- --run` 19 项通过（含 api-contract 6 项）；`npm run build` 通过。
- 合同门禁：`python scripts/export_openapi.py --check` 通过（连续两次生成字节一致）；`npm run api:check` 通过（generated.ts 与 openapi.json 一致 + 无旧合同模式）。
- 测试基础设施修复：conftest 的逐表 TRUNCATE DO 循环改为单语句 TRUNCATE，测试引擎关闭 asyncpg 语句缓存（`statement_cache_size=0`），session 开始时清空业务表，并对 teardown TRUNCATE 按 sqlstate 40P01（deadlock_detected）退避重试，消除多 worktree 并发访问同一测试库时的间歇性死锁与残留数据冲突。

---

## Code Review 修复方案与任务卡拆分（2026-07-31）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `docs/code-review-remediation/README.md` | 明确可信数据集生产主链目标，建立 code review 问题闭环映射、任务依赖 DAG、实施波次和共同合并门槛 | 已完成 |
| `T00`–`T04` | 自动化基线、JWT 语义、项目对象级授权、解析器出站/ParseJob 快照、OpenAPI 与统一错误合同 | 已完成 |
| `T05`–`T08` | 清洗编辑/版本发布并发、不可变 ChunkSet 与 Token 预算、持久任务生命周期、单项/批量生成与配置快照 | 已完成 |
| `T09`–`T11` | Candidate/CuratedItem 证据审批、固定 approved revision 的编组/finalize、不可变导出/manifest/artifact seal | 已完成 |
| `docs/README.md` | 增加 code review 修复任务卡目录入口 | 已完成 |

### 设计决策

- 将项目目标收敛为可授权、可人工审查、可复现、可验证的 `Document → CleanedDocumentVersion → ChunkSet → GenerationBatch → Candidate → CuratedItem → Dataset/Benchmark → Export` 版本链。
- 共拆分 12 张可独立实施、测试和审查的任务卡；每张均包含范围与明确不做、数据库/API/前端合同、预期修改面、依赖和风险、自动化验收标准及停止条件。
- 安全修复优先于业务主链；T05 与 T07 可并行，T06 在两者之后合并，T08–T11 按生成、审批、编组、导出顺序推进。
- 跨卡合同明确冻结 ParseJob 和 GenerationBatch 配置、批准的 CuratedRevision/证据、composition revision/hash 与 export artifact seal，禁止从当前可变配置反推历史事实。

### 验证状态

- 已严格按 UTF-8 解码全部 12 张任务卡，并确认必需章节齐全、Markdown 代码围栏成对。
- 已确认索引中的 12 个任务卡链接全部存在；任务卡元数据与索引依赖一致，12 节点依赖图无环。
- 本轮仅编写修复方案文档，未修改运行时代码，因此未执行后端/前端运行时测试；最终执行 `git diff --check` 作为文档变更校验。

---

## README 工作目录路径修正（2026-07-02）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `README.md` | 将手动启动示例中的旧 `C:\Work\Postdoc\Test\03_LLM\domain-dataset-gen` 绝对路径改为从仓库根目录出发的相对路径，适配当前 `E:\Work` 迁移后的目录 | 已完成 |

### 验证状态

- 已搜索 README / docs / scripts / apps / infra / libs / tests，确认不再残留 `C:\Work` 或 `C:/Work` 路径。

---

## 停止脚本行为修正（2026-07-02）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `scripts/dev-stop.ps1` | 输出改为英文 ASCII，避免 Windows PowerShell 中文乱码 | 已实现 |
| Docker 停止行为 | `-All` 从 `docker compose down` 改为 `docker compose stop`，只停止 Docker 容器，不再移除容器和网络 | 已实现 |
| `README.md` | 明确 `.\scripts\dev-stop.ps1 -All` 会停止 Docker 容器，但容器仍保留在 Docker Desktop 中 | 已实现 |

### 说明

- 之前执行 `docker compose down` 后，Docker Desktop 中看不到容器是正常现象：容器和网络被移除了，但 image 和 volume 没有删除。
- 当前本地仍保留 `datasetgen_pgdata`、`datasetgen_miniodata`、`docker_pgdata`、`docker_miniodata` 数据卷。
- 已验证 `.\scripts\dev-stop.ps1 -All` 输出不再乱码，并且 `datasetgen-postgres-1`、`datasetgen-redis-1`、`datasetgen-minio-1`、`datasetgen-minio-init-1` 会保留为 `Exited` 状态。

---

## conda 启动脚本健康检查修复（2026-07-02）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `scripts/dev-start-conda.ps1` | 增加本地源码 `PYTHONPATH`，在不安装 editable 包的前提下让 API 能直接导入 `domain`、`storage`、`parsing`、`cleaning`、`splitters`、`llm` | 已实现 |
| API 启动 | 去掉 `uvicorn --reload`，测试启动改为单进程模式，减少 Windows reloader 子进程导致的健康检查和日志干扰 | 已实现 |
| Web 启动 | 改为显式调用 `npm.cmd run dev`，避开当前 PowerShell 环境中 `npm.ps1` shim 解析为 `Unknown command: "pm"` 的问题 | 已实现 |
| 运行时前置检查 | 启动 Docker/API/Web 前先验证后端源码导入和前端 `node_modules\.bin\next.cmd` 是否存在；缺依赖时直接提示手动命令并退出，不再等待超时 | 已实现 |
| 健康检查 | 改用 `curl.exe --max-time 2` 访问 `127.0.0.1`，避免 `Invoke-WebRequest` 在 Windows PowerShell 中卡住；API 未就绪时会输出最近 API 日志并退出；API 就绪后再启动 Web，Web 未就绪时输出最近 Web 日志并退出 | 已实现 |

### 验证状态

- 已确认旧失败原因为 API 子进程无法导入 `domain`。
- 已确认设置本地源码 `PYTHONPATH` 后，`import app.main, domain, storage, parsing, cleaning, splitters, llm` 通过。
- 已确认 `npm.cmd` 可正常调用，避免 `npm.ps1` 的 `Unknown command: "pm"`。
- 已确认 `curl.exe --noproxy "*"` 可正常访问 `http://127.0.0.1:8000/api/health`。
- 已确认当前前端依赖缺少 `apps\web\node_modules\.bin\next.cmd`，需要用户手动执行 `cd apps\web; npm ci` 后再启动。
- 已重新运行 `.\scripts\dev-start-conda.ps1`，脚本在前端依赖缺失时 6 秒内明确退出并提示手动执行 `cd apps\web; npm ci`，不再卡在 API/Web 健康检查等待。
- 待完成：用户手动补齐前端依赖后，重新运行 `.\scripts\dev-start-conda.ps1` 做端到端启动验证。

---

## Docker Compose 项目名固定（2026-07-02）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `infra/docker/docker-compose.yml` | 新增顶层 `name: datasetgen`，避免 Docker Desktop 中 Compose 项目显示为目录名 `docker` | 已实现 |
| 本地 Docker 状态 | 已执行 `docker compose -p docker -f infra/docker/docker-compose.yml --env-file infra/docker/.env down`，停止并移除旧 `docker` 项目组容器和网络 | 已完成 |

### 说明

- 旧数据卷未删除；后续启动会使用新的 `datasetgen_pgdata` 与 `datasetgen_miniodata`，因为当前测试数据不重要。
- 已执行 `docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up -d`，Docker Desktop 中应显示 `datasetgen` 项目组。
- 当前运行容器为 `datasetgen-postgres-1`、`datasetgen-redis-1`、`datasetgen-minio-1`，端口映射保持 `5432`、`6379`、`9000-9001`。
- 旧 `docker_miniodata` 和 `docker_pgdata` 数据卷仍保留，后续确认不需要后可手动清理。

---

## conda 启动脚本去参数化（2026-07-02）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `scripts/dev-start-conda.ps1` | 移除入口 `param(...)`，不再支持 `-InfraOnly`、`-NoApi`、`-NoWeb`、`-ExpectedEnvName`、`-PythonVersion` 等 CLI 可选参数；传入任何参数都会直接报错退出 | 已实现 |
| 启动行为 | 脚本固定执行完整本地测试启动：准备环境文件、检查 `DatasetGen` / Python 3.11、启动 Docker、迁移、种子、后台启动 API/Web、输出访问地址 | 已实现 |
| `README.md` | Windows + conda 一键脚本说明只保留 `.\scripts\dev-start-conda.ps1` 一种运行方式，并移除启动脚本可选参数示例 | 已实现 |

### 验证状态

- `scripts/dev-start-conda.ps1` PowerShell 语法解析通过。
- `git diff --check` 通过，仅有 Git 提示后续可能按 CRLF 处理文本文件。
- `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev-start-conda.ps1 -InfraOnly` 已验证会直接报错并提示只运行 `.\scripts\dev-start-conda.ps1`。
- `scripts/dev-start-conda.ps1` 和 README 中已无 conda 启动脚本可选参数示例残留。

---

## conda 启动脚本移除依赖安装（2026-07-02）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `scripts/dev-start-conda.ps1` | 移除依赖安装阶段，删除 `-SkipInstall` 参数、后端 editable 安装逻辑和前端 `npm ci` 检查逻辑；启动流程从 8 步调整为 7 步 | 已实现 |
| 前端启动检查 | 保留 `npm` 命令存在性检查，仅用于启动 Web，不再自动安装或同步前端依赖 | 已实现 |
| `README.md` | 明确 Windows + conda 一键脚本不安装 Python 或 npm 依赖，默认当前 conda 环境和前端依赖已准备好 | 已实现 |

### 验证状态

- `scripts/dev-start-conda.ps1` 与 `scripts/dev-stop.ps1` PowerShell 语法解析通过。
- `git diff --check` 通过，仅有 Git 提示后续可能按 CRLF 处理文本文件。
- `scripts/dev-start-conda.ps1` 内已无 `pip install`、`npm ci`、`-SkipInstall` 或旧 8 步编号残留。
- `conda activate DatasetGen; .\scripts\dev-start-conda.ps1 -InfraOnly` 已通过，完成当前环境检查、Docker 基础设施启动、Alembic 迁移和种子脚本，未执行依赖安装，也未启动 API/Web；2026-07-02 后续已移除 `-InfraOnly` 参数，脚本改为零参数完整启动。

---

## Windows + conda 一键启动脚本（2026-07-01）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `scripts/dev-start-conda.ps1` | 新增 Windows + conda 专用一键启动脚本；不创建 `.venv`，不切换 conda 环境，直接使用当前 `DatasetGen` 环境的 `python.exe` 执行后端命令 | 已实现 |
| conda 环境检查 | 默认要求当前 shell 已执行 `conda activate DatasetGen`；检查 `CONDA_DEFAULT_ENV=DatasetGen` 与 Python 3.11，不再自动创建或切换环境 | 已实现 |
| 启动编排 | 自动创建本地 `.env`、启动 Docker 基础设施、执行迁移与种子脚本、后台启动 API/Web；不执行 Python/npm 依赖安装 | 已实现 |
| Windows 原生命令执行 | 对 Docker Compose 等原生命令按退出码判断失败，避免正常 stderr 进度在 PowerShell 5.1 下被误判为 `NativeCommandError` | 已实现 |
| `scripts/dev-stop.ps1` | 说明更新为可停止 `dev-start.ps1` 或 `dev-start-conda.ps1` 启动的 API/Web 进程 | 已实现 |
| Docker Compose 配置 | 移除 `infra/docker/docker-compose.yml` 顶层 `version` 字段，避免 Docker Compose v2 废弃警告在 PowerShell 中被当成错误 | 已实现 |
| `README.md` | 推荐入口调整为 `.\scripts\dev-start-conda.ps1`，保留 uv/.venv 脚本和手动 conda 流程作为备选 | 已实现 |

### 验证状态

- `scripts/dev-start-conda.ps1` PowerShell 语法解析通过。
- `scripts/dev-stop.ps1` PowerShell 语法解析通过。
- 已确认当前 `DatasetGen` conda 环境为 Python 3.11。
- Docker、Node.js、npm 命令可用。
- 在 `base` 环境执行脚本时，会提示先运行 `conda activate DatasetGen` 并退出，避免把依赖误装到错误环境。
- 在 `DatasetGen` 环境执行脚本时，已确认脚本使用当前环境的 `python.exe`，不再调用 `conda run`、`conda activate` 或创建 conda 环境。
- `docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env config --quiet` 通过，已确认移除 `version` 字段后不再输出 Compose v2 废弃警告。
- 使用 `conda activate DatasetGen; .\scripts\dev-start-conda.ps1 -InfraOnly -SkipInstall` 复测时，发现并修复 Docker Compose 正常进度 stderr 被 PowerShell 误判为错误的问题。
- 修复后 `conda activate DatasetGen; .\scripts\dev-start-conda.ps1 -InfraOnly -SkipInstall` 已通过，完成当前环境检查、Docker 基础设施启动、Alembic 迁移和种子脚本；2026-07-02 起 `-SkipInstall` 已随依赖安装阶段一并移除。
- 本轮未执行完整 API/Web 后台启动；首次实际运行时需观察 `logs/R1plus-API.log` 与 `logs/R1plus-Web.log`。

### 下一步

- 首次完整运行 `.\scripts\dev-start-conda.ps1` 后，记录 API/Web 启动耗时、常见失败点和 Windows 本地 MinerU 可用性。
- 若 conda 脚本稳定，可将 `docs/runbooks/new-machine-runbook.md` 同步改为 Windows 优先使用 conda 脚本。

---

## README 运行指南重写（2026-07-01）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `README.md` | 重写根目录 README，修复迁移后显示乱码的问题，并按“一键脚本优先、conda-only 手动流程备用、开发信息其次”的结构重新组织项目说明 | 已实现 |
| Windows + conda 启动说明 | 补齐 conda 环境创建、Docker 基础设施、后端 editable 安装、Alembic 迁移、种子数据、API/Web 启动步骤 | 已实现 |
| 页面使用流程 | 补齐从登录、上传 PDF、解析、清洗、分块、生成、审核提升到数据集/评测集导出的主链路说明 | 已实现 |
| 解析器与 LLM 配置说明 | 明确 PyMuPDF、MinerU 本地模型、远程 API、本地服务型解析器的适用边界；说明生成 Candidate 前必须配置 OpenAI-compatible ModelConfig | 已实现 |

### 已知问题

- 本次只更新文档，没有修改启动脚本；`scripts/dev-start.ps1` 仍走 `uv sync` 并会创建 `.venv`，README 已单独给出 conda-only 手动流程。
- Windows 下本地服务型 MinerU/PaddleOCR 仍主要沿用原 macOS/MLX 方案说明；Windows 新手优先使用 `PyMuPDF4LLM（本地）` 或 `MinerU2.5-Pro（本地模型）`。

### 下一步

- 如后续确认长期采用 conda-only 工作流，可考虑新增 `scripts/dev-start-conda.ps1` 或给现有脚本增加 conda 模式，避免 README 与脚本模式分叉。
- 后续完成 R1 端到端验收时，应同步更新 README 的“当前项目状态”和验收边界。

---

## 环境变量示例模板补齐（2026-06-04）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `apps/api/.env.example` | 新增后端本机运行示例，覆盖 PostgreSQL、Redis、MinIO、JWT、远程解析器 Token 与 FastAPI 监听配置 | 已实现 |
| `apps/web/.env.example` | 新增前端示例，覆盖浏览器访问 API 与 WebSocket 的公开地址 | 已实现 |
| `infra/docker/.env.example` | 补齐 `MINIO_HOST_PORT` 与 `MINIO_CONSOLE_PORT`，使 Docker 基础设施端口示例与一键启动脚本读取项一致 | 已实现 |

### 说明

- 当前真实运行配置仍位于 `infra/docker/.env`、`apps/api/.env` 与 `apps/web/.env.local`，不提交真实密钥。
- 一键启动脚本仍会在缺少真实配置时自动生成本机默认值；示例模板用于新机器手动检查和部署说明。

---

## 一键启动前端依赖同步增强（2026-05-29）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `scripts/dev-start.sh` | 前端依赖检查从“仅判断 `node_modules` 是否存在”升级为检查 `node_modules/.package-lock.json` 与 `apps/web/package.json` / `package-lock.json` 时间戳，并通过 `npm ls --depth=0` 校验依赖完整性；锁文件更新或依赖缺失时自动执行 `npm ci` | 已实现 |
| `scripts/dev-start.ps1` | Windows 一键启动脚本同步采用同一判断逻辑，保证新拉取的 Markdown/KaTeX 等前端依赖会被补齐，且能修复不完整的 `node_modules` | 已实现 |
| `apps/web/package.json` / `package-lock.json` | `remark-math`、`rehype-katex`、`katex` 已作为普通前端运行依赖入锁，依赖安装限定在 `apps/web/node_modules` | 已确认 |

### 问题原因

- 本次 Markdown 预览增强确实新增了 `remark-math`、`rehype-katex`、`katex` 三个前端依赖。
- 原一键启动脚本在全新电脑上会通过 `npm ci` 安装完整依赖，但在已有 `apps/web/node_modules` 的旧环境中，只会认为“前端依赖已存在”，不会感知 `package-lock.json` 新增条目。

### 验证状态

- `scripts/dev-start.sh` 执行 `bash -n` 语法检查通过。
- `apps/web` 执行 `npm ls --depth=0` 通过，确认新增 Markdown/KaTeX 依赖已安装在本地前端依赖树。
- `apps/web` 执行 `npm exec tsc -- --noEmit` 通过。
- 当前 macOS 环境未安装 `pwsh`，`scripts/dev-start.ps1` 未做本地 parser 级验证；本轮按 PowerShell 语法静态审阅。

### 已知问题与下一步

- 使用 `--skip-install` / `-SkipInstall` 时仍会按用户显式要求跳过依赖同步；若此时锁文件已有新增依赖，前端启动仍可能缺包。
- `npm ci` 需要访问 npm registry；新电脑首次安装或锁文件更新补包时应保证网络可用，或后续单独引入离线 npm 缓存策略。

---

## 清洗工作台 Markdown 预览增强（2026-05-29）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `apps/web/.../documents/[did]/clean/page.tsx` | Markdown 预览接入 `remark-math` + `rehype-katex`，支持 `$...$`、`$$...$$`、`\(...\)`、`\[...\]` 公式渲染；预览前增加 PDF parser 常见转义归一化，让 `\#`、`\*\*` 等展示时恢复为可解析 Markdown | 已实现 |
| `apps/web/src/app/layout.tsx` | 引入 `katex/dist/katex.min.css`，保证 KaTeX 公式样式生效 | 已实现 |
| `apps/web/src/app/globals.css` | 新增 `.markdown-preview` 样式，补齐 Tailwind reset 下缺失的标题、加粗、列表、表格、代码块和公式显示层级 | 已实现 |
| `apps/web/package.json` / `package-lock.json` | 新增 `remark-math`、`rehype-katex`、`katex` 依赖 | 已实现 |

### 问题原因

- `react-markdown` 本身可以解析普通 Markdown，但部分解析器会把 Markdown 控制符转义为 `\#`、`\*\*`，渲染后视觉上仍像原始 Markdown 符号。
- 公式语法不属于基础 Markdown，原页面只接入 `remark-gfm`，没有数学扩展，因此 `$E=mc^2$` 等只能作为普通文本显示。
- 当前项目未安装 Tailwind Typography，`.prose` 类没有实际排版增强；同时 Tailwind reset 会重置标题默认样式，导致即使 AST 已正确渲染，标题层级也不明显。

### 验证状态

- `apps/web` 执行 `npm exec tsc -- --noEmit` 通过。
- 清洗页面与 root layout 定向 `eslint` 通过。
- 用本地 React SSR 小样验证：转义标题、转义加粗、`\(...\)` 与 `$$...$$` 可分别渲染为 `<h1>`、`<strong>` 与 KaTeX 节点。

### 已知问题与下一步

- 预览仍不渲染任意 raw HTML，以避免把解析器返回的 HTML 直接注入页面；如后续需要表格 HTML 支持，应单独引入 sanitize 策略。
- KaTeX 对极少数非标准 LaTeX 宏会降级显示错误标记；当前已关闭 throw，避免单个公式阻断整页预览。

---

## 清洗工作台 Markdown 预览与按页 Section 切分（2026-05-29）

### 本轮总览

| 模块 | 内容 | 状态 |
|------|------|------|
| `apps/web/.../documents/[did]/clean/page.tsx` | 修复 Markdown 预览与编辑器状态不同步的边界：`cleaned_markdown` 使用空字符串时不再回退 raw；预览增加软换行处理，使编辑器中的普通换行可在预览栏按行展示 | 已实现 |
| `libs/cleaning/cleaning/splitter.py` | 清洗 Section 切分策略调整为优先按页：先读取 `structured_json.pages` 页级 markdown，再使用 `page_mapping.markdown_start/end`，再识别显式页标记；仅在旧解析产物没有页级信息时回退 H1/H2 标题切分 | 已实现 |
| `apps/api/app/workers/clean_worker.py` | 清洗 worker 下载 ParseJob 的 `structured.json`，把 `structured_json` 与 `page_mapping` 一并传入 splitter，确保 CleaningJob 按指定解析来源的页级产物生成 Section | 已实现 |
| `libs/parsing/parsing/pymupdf_parser.py` | 默认 `pymupdf4llm` 改为读取 `page_chunks=True`，保存 `structured_json.pages` 和 `page_mapping` 的 markdown 字符区间，补齐按页清洗所需边界 | 已实现 |
| `libs/parsing/parsing/paddleocr_parser.py` / `paddleocr_local_service_parser.py` | PaddleOCR 远程与本地服务继续按 `layoutParsingResults[*].markdown.text` 组合全文，同时补齐每页 markdown 区间 | 已实现 |
| `libs/parsing/parsing/mineru_parser.py` / `mineru_local_service_parser.py` | MinerU 远程与本地服务从 `content_list` 按 `page_idx` 聚合页级 markdown fallback；本地 transformers 解析器沿用已有逐页 markdown 产物 | 已实现 |

### 设计边界

- 新 CleaningJob 会优先呈现“第 N 页”作为左侧 Section；这更贴近 PDF 原文对照清洗，也避免无 H1/H2 的论文或手册全文落成单 Section。
- 已存在的旧 ParseJob 如果当时没有保存页级 markdown 或字符区间，无法无损反推真实页边界；这类历史任务仍会回退到标题切分。需要按页清洗时应重新解析后进入新的清洗工作台。
- MinerU 远程/本地服务在缺少直接页级 markdown 时使用 `content_list` 聚合文本型字段作为页级 fallback，表格/公式字段会尽量保留 `table_body` / `latex`，但质量仍取决于服务返回结构。

### 验证状态

- `pytest -q` 通过，共 21 项；其中新增/相关 `tests/test_cleaning_splitter.py` 与 `tests/test_remote_parsers.py` 共 8 项通过。
- `apps/web` 执行 `npm exec tsc -- --noEmit` 通过。
- 清洗页面定向 `eslint` 通过。
- 修改文件执行 `ruff check` 与 `python -m py_compile` 通过。

### 已知问题与下一步

- 旧清洗工作台不会自动重切已有 Section，按页策略只在新建 CleaningJob 时生效。
- PDF iframe 仍未实现随 Section 自动跳页/高亮；现在左侧 Section 已有 `source_pages=[N]`，下一步可用它驱动 PDF 页码联动。
- Markdown 预览仍未引入数学公式渲染（KaTeX/MathJax）和 raw HTML 渲染；如果压气机教材公式较多，后续可作为独立预览增强项处理。

---

## 本地解析服务环境自动检查（2026-05-28）

### 一键启动前置环境补齐

| 模块 | 内容 | 状态 |
|------|------|------|
| `scripts/dev-start.sh` | 在 `[3/7] 检查并安装依赖` 阶段增加 MinerU 与 PaddleOCR-VL 本地 API 服务环境检查；平台依赖完成后，自动确认两个本地服务的按需启动前置条件 | 已实现 |
| `scripts/dev-start.sh` -> `scripts/mineru_local_service.py` | 检测 `models/MinerU2.5-Pro-2604-1.2B/model.safetensors` 与 `.venv-mineru-service/bin/mineru-api`；缺少服务环境时复用既有 `setup`，并刷新 `mineru.json` 配置 | 已实现 |
| `scripts/dev-start.sh` -> `scripts/paddleocr_local_service.py` | 检测 `models/PaddleOCR-VL-1.5-0.9B/model.safetensors`、PaddleX 环境、MLX-VLM 环境、`PP-DocLayoutV3` 与 MLX 转换产物；缺少时分别调用既有 `setup`、`download-layout`、`convert` 与 `config` | 已实现 |

#### 设计边界

- 不新增第三套安装脚本；`dev-start.sh` 保持项目启动编排入口，重型模型服务的安装、下载、转换和配置继续复用已经验证过的专用 helper 脚本。
- MinerU 与 PaddleOCR-VL 的主模型权重属于本地已准备资源：如果 `models/.../model.safetensors` 不存在，启动脚本给出明确路径提示并跳过对应服务环境自动安装，避免在用户不知情时下载超大权重。
- `PP-DocLayoutV3`、PaddleOCR-VL 的 MLX 转换产物和两个独立服务虚拟环境属于可由本仓库 helper 自动补齐的运行前置条件，因此纳入一键启动检查。
- 使用 `--skip-install` 时沿用原语义：跳过依赖检查与安装，也会跳过本地服务环境补齐。

#### 验证状态

- `bash -n scripts/dev-start.sh scripts/dev-stop.sh` 通过。
- 本轮未执行完整 `dev-start.sh` 首次安装链路，避免重复触发大型依赖安装和模型转换；逻辑复用此前已分别实测通过的 MinerU / PaddleOCR helper 子命令。

#### 已知问题与下一步

- 仍建议在全新机器或清空独立服务环境后执行一次完整 `./scripts/dev-start.sh`，记录首次自动补齐耗时、网络下载提示和失败提示是否足够清晰。

---

## PaddleOCR-VL 本地服务接入平台（2026-05-28）

### 第三步：作为 ParserProfile 解析方式并行接入

| 模块 | 内容 | 状态 |
|------|------|------|
| `libs/parsing/parsing/paddleocr_local_service_parser.py` / `__init__.py` | 新增 `paddleocr_local_service` parser，调用自有 PaddleX `/layout-parsing`，不注入官方 Token，提取页级 Markdown、`prunedResult` 和页映射 | 已实现 |
| `apps/api/app/services/paddleocr_local_service_manager.py` | 新增双进程按需启动管理器：先启动 `9021` 内部 MLX-VLM，再启动 `9020` PaddleX API；校验服务环境、MLX 权重与 `PP-DocLayoutV3` 完整性 | 已实现 |
| `apps/api/app/workers/parse_worker.py` | 选择 `paddleocr_local_service` 时解析前自动确保本地服务可用，与 MinerU 本地服务模式平行 | 已实现 |
| `scripts/init_seed.py` | 为新数据库和既有项目幂等补齐 `PaddleOCR-VL（本地部署服务 / MLX）` ParserProfile | 已实现 |
| `apps/web/.../settings/page.tsx` | 设置页增加本地 PaddleOCR-VL 服务类型、PaddleX API 地址与内部 MLX-VLM 地址配置；明确无需官方 Token | 已实现 |
| `scripts/dev-stop.sh` | 停止脚本新增 `PaddleOCR-API.pid` 与 `PaddleOCR-VLM.pid` 清理 | 已实现 |
| `docs/runbooks/paddleocr-local-service.md` | 更新为平台已接入状态，补充默认 profile、PID、日志与停止方式 | 已更新 |

#### 设计边界

- `paddleocr_local_service` 与远程 `paddleocr` 并存：远程方式继续读取服务端 `PADDLEOCR_API_TOKEN`，本地服务方式不需要官方 Token。
- ParserProfile 默认指向本机 `http://127.0.0.1:9020/layout-parsing` 与内部 `http://127.0.0.1:9021`；外部服务地址不会被平台误启动。
- 当前 Mac 使用 MLX-VLM；未来迁入 GPU 时，可保持外层 PaddleX `/layout-parsing` 协议不变，只替换内部 VLM 服务地址与后端。

#### 验证状态

- 后端定向测试通过：`pytest tests/test_remote_parsers.py tests/test_parser_credentials.py tests/test_mineru_local_service_manager.py tests/test_paddleocr_local_service_manager.py -q`，共 17 项。
- `python -m py_compile` 与 `ruff check` 覆盖新增 parser、manager、worker、seed 与测试文件，均通过。
- 前端 `npm exec tsc -- --noEmit` 与设置页定向 `eslint` 通过。
- `bash -n scripts/dev-stop.sh scripts/dev-start.sh` 通过。

#### 已知问题与下一步

- 已完成 mock 层平台适配验证；还需用真实 PDF 通过前端选择 `PaddleOCR-VL（本地部署服务 / MLX）` 发起 ParseJob，确认自动启动服务、任务状态、解析产物和进入清洗工作台全链路表现。
- Mac 首次真实解析会加载 MLX 模型，耗时明显高于普通远程 API；前端任务页当前只展示状态文字，不展示实时百分比，符合此前进度展示调整。

---

## PaddleOCR-VL 完整布局链路补齐（2026-05-28）

### 第二步：补齐 PP-DocLayoutV3 并验证完整 `/layout-parsing`

| 模块 | 内容 | 状态 |
|------|------|------|
| `scripts/paddleocr_local_service.py` | 新增 `download-layout` 命令、本地 `PP-DocLayoutV3` 路径校验、`LayoutDetection.model_dir` 配置写入；完整模式默认要求本地布局模型齐全 | 已实现并实测 |
| `models/PP-DocLayoutV3/` | 下载 `PaddlePaddle/PP-DocLayoutV3` 的 Paddle 推理格式文件：`inference.json`、`inference.pdiparams`、`inference.yml` | 已补齐 |
| `docs/runbooks/paddleocr-local-service.md` | 将运行步骤从整页冒烟更新为可执行完整布局检测链路；保留 `--whole-page-smoke` 作为调试降级路径 | 已更新 |

#### 实测结果

- `models/PP-DocLayoutV3` 约 `126M`，核心权重 `inference.pdiparams` 约 `125M`。
- 生成的 `logs/paddleocr-local-service/config/PaddleOCR-VL-1.5.yaml` 已设置 `use_layout_detection: true`，并将 `SubModules.LayoutDetection.model_dir` 指向本地 `models/PP-DocLayoutV3`。
- 启动完整 API 时日志显示 `Creating model: ('PP-DocLayoutV3', '/Users/liuyuze/Desktop/domain-dataset-gen/models/PP-DocLayoutV3', None)`，确认未走远程布局模型下载。
- 不带 `--whole-page-smoke` 执行 `smoke`，`/layout-parsing` 返回 HTTP `200`，Markdown 输出为 `PaddleOCR local API smoke test`；响应 `prunedResult` 包含 `layout_det_res`、`parsing_res_list` 等完整 pipeline 字段。
- `python -m py_compile scripts/paddleocr_local_service.py` 与 `ruff check scripts/paddleocr_local_service.py` 通过。

#### 已知问题与下一步

- 本轮使用自动生成的单页 PDF 完成协议与模型加载验证；仍需选择一份真实压气机教材/论文 PDF 小样本，观察标题、正文、表格、图片和公式区域的 Markdown 质量。
- 独立服务已具备平台接入条件；下一步可新增 `paddleocr_local_service` 解析类型、双服务按需启动/停止管理器、ParserProfile 种子和前端无 Token 配置入口。
- Mac 上继续使用 MLX-VLM；未来迁入 GPU 后，将内部 `9021` 推理层替换为 vLLM/FastDeploy，外层 `9020 /layout-parsing` 协议保持不变。

---

## PaddleOCR-VL 本地 API 冒烟验证（2026-05-27）

### 第一阶段：Apple Silicon 双服务与本地权重链路

| 模块 | 内容 | 状态 |
|------|------|------|
| `scripts/paddleocr_local_service.py` | 新增 PaddleX 完整 API 与 MLX-VLM 内部推理的双服务准备、模型转换、配置、健康检查和 PDF-to-Markdown 冒烟工具；使用 `9020` / `9021` 与 MinerU `9010` 隔离 | 已实现并实测 |
| `.gitignore` | 忽略 `.venv-paddleocr-service/` 与 `.venv-paddleocr-mlx-service/`，服务环境不进入平台依赖树 | 已实现 |
| `docs/runbooks/paddleocr-local-service.md` | 记录 Mac 环境、端口、MLX 转换、整页冒烟与未来 GPU 内部推理替换边界 | 已更新 |

#### 设计边界

- 本阶段只验证独立本地服务，不新增平台 `ParserProfile`、ParseJob manager 或前端选项；远程 `paddleocr` 与 MinerU 平台路径均不改变。
- 对外协议由 PaddleX 提供 `http://127.0.0.1:9020/layout-parsing`；内部 MLX-VLM 服务位于 `http://127.0.0.1:9021`。未来迁入 GPU 时可替换内部推理引擎而保留平台对外协议。
- 仓库已有 `models/PaddleOCR-VL-1.5-0.9B` 是原始本地权重；Mac 的 MLX 服务读取由该目录离线转换得到的 `logs/paddleocr-local-service/models/PaddleOCR-VL-1.5-MLX`，整个请求链路不需要官方 PaddleOCR API token。

#### 实测结果

- API 环境已安装 `paddleocr==3.5.0`、`paddlex==3.5.2` 与 `paddlepaddle==3.3.1`；独立 MLX 环境已安装 `mlx-vlm==0.3.10`、`mlx-lm==0.30.5`、`mlx==0.31.1`、`mlx-metal==0.31.1`、`transformers==5.0.0rc3`、`torch==2.12.0` 与 `torchvision==0.27.0`，`uv pip check` 通过。
- 已将原始权重离线转换为约 `1.7G` 的 MLX 产物，并验证 MLX 可直接加载该模型及本地处理器代码。
- 以 `api-serve --whole-page-smoke` 关闭布局检测后，`health` 成功访问 `/layout-parsing`；提交自动生成的一页 PDF 返回 HTTP `200`，Markdown 输出为 `PaddleOCR local API smoke test`。MLX 服务日志记录了本地模型加载与 `/chat/completions` 成功生成。
- 实测中修正了三项阻塞：`mlx-vlm==0.3.9` 不含 `paddleocr_vl` 支持；MLX 服务需信任转换产物随附的本地处理器实现；PaddleX 的内部 `server_url` 必须使用服务根地址而非附加 `/v1`。启动环境同时显式排除本机地址代理影响。
- `python -m py_compile scripts/paddleocr_local_service.py` 与 `ruff check scripts/paddleocr_local_service.py` 通过。

#### 已知问题与下一步

- 2026-05-28 已补齐本地 `PP-DocLayoutV3` 并通过完整布局链路冒烟；后续问题转入真实 PDF 质量验收与平台接入。

---

## MinerU 本地服务按任务自动启动（2026-05-27）

### 解析时自动拉起 MLX 服务

| 模块 | 内容 | 状态 |
|------|------|------|
| `apps/api/app/services/mineru_local_service_manager.py` | 新增本机服务管理器：解析前探测健康状态、自动启动 `.venv-mineru-service` 中的服务、等待就绪、输出 PID/日志供停止与排错使用 | 已实现 |
| `apps/api/app/workers/parse_worker.py` | 选择 `mineru_local_service` 时，在 parser 调用前按需保证本机服务可用，不再将可用性绑定到平台启动参数 | 已实现 |
| `scripts/dev-start.sh` / `dev-stop.sh` | 普通启动即支持本地服务解析；旧 `--with-mineru-service` 仅保留兼容；停止脚本可停止任务自动启动的服务 | 已实现 |
| `apps/web/.../settings/page.tsx` | 本地服务提示改为“首次本机解析自动启动”，消除需要人工预启动的误导 | 已实现 |
| `tests/test_mineru_local_service_manager.py` | 覆盖健康服务复用、本机地址按需启动、外部服务地址不误启动 | 已完成 |

#### 行为说明

- 本机 `http://127.0.0.1:<port>` / `http://localhost:<port>` 的 `mineru_local_service` 配置在首次 ParseJob 中自动启动服务；之后的任务复用健康服务。
- GPU 主机或其他外部服务地址不触发本机进程启动，保持未来 `vlm-http-client + vLLM` 部署边界清晰。
- 独立服务环境仍属于一次性部署准备项：环境尚未安装时，任务会返回包含 `scripts/mineru_local_service.py setup` 的明确处理提示，而非模糊的连接拒绝。

#### 验证状态

- `ruff check`、`python -m py_compile`、`bash -n scripts/dev-start.sh scripts/dev-stop.sh` 通过。
- `pytest tests -q` 通过，共 11 项测试通过。
- `apps/web` 执行 `tsc --noEmit` 与设置页定向 `eslint` 通过。
- 在 `127.0.0.1:9010` 未预启动服务的状态下，直接执行后端按需管理逻辑并调用平台 `MineruLocalServiceParser`，成功自动启动服务并解析单页 PDF，返回 Markdown `MinerU local service PDF smoke test`。
- 按需启动的服务日志明确记录 `Using mlx-engine as the inference engine for VLM.`；验收完成后已停止临时服务进程。

---

## MinerU 本地部署服务接入平台（2026-05-27）

### 第二阶段：MLX 服务作为解析方式接入

| 模块 | 内容 | 状态 |
|------|------|------|
| `libs/parsing/parsing/mineru_local_service_parser.py` / `__init__.py` | 新增 `mineru_local_service` parser，通过自部署 `mineru-api` 的异步任务接口上传 PDF、轮询结果并提取 Markdown、页映射与结构化产物 | 已实现并真实联通 |
| `scripts/init_seed.py` | 为新数据库和既有项目幂等补齐 `MinerU（本地部署服务 / MLX）` ParserProfile；默认指向 `http://127.0.0.1:9010` 和 `vlm-auto-engine` | 已实现 |
| `apps/web/.../settings/page.tsx` | 设置页增加本地服务类型、服务地址与推理路径配置；明确 Mac/MLX 和未来 GPU/vLLM 的选择边界；不要求官方 Token | 已实现 |
| `scripts/dev-start.sh` / `dev-stop.sh` | 曾提供 `--with-mineru-service` 预启动入口；同日后续已替换为 ParseJob 按需自动启动，并保留旧参数兼容 | 已被按需启动方案替代 |
| `tests/test_remote_parsers.py` / `test_parser_credentials.py` | 覆盖本地服务异步协议解析、页映射产物及不注入远程 MinerU Token 的凭据边界 | 已完成 |
| `docs/runbooks/mineru-local-service.md` | 补充平台启用入口、解析器选择和 GPU/vLLM 迁移配置方式 | 已更新 |

#### 设计边界

- `mineru_local_service` 与既有 `mineru_local + transformers` 并存：前者将模型生命周期隔离在独立服务，后者保留为原有兼容路径，不在本轮强制替换历史配置。
- 平台只依赖 `mineru-api` 的服务协议。当前 Mac 的 `vlm-auto-engine` 实际落到 MLX；迁入 GPU 后可将同一 ParserProfile 改为 `vlm-http-client` 并填写 vLLM 服务地址，而无需修改 ParseJob 或清洗流程。
- 本地服务读取仓库已有模型权重，不注入 `MINERU_API_TOKEN`，因此用户无需官方 API Token 即可完成该解析方式的任务。

#### 验证状态

- `ruff check`、`python -m py_compile` 和 `bash -n scripts/dev-start.sh scripts/dev-stop.sh` 通过。
- `pytest tests -q` 通过，共 7 项 parser 与凭据边界测试通过。
- `apps/web` 执行 `tsc --noEmit` 与设置页定向 `eslint` 通过。
- 实际启动本机 `mineru-api` 后，以平台 `MineruLocalServiceParser` 对单页 PDF 发起 `/tasks -> 状态轮询 -> /result` 调用，成功返回 Markdown `MinerU local service PDF smoke test`、单页映射及结构化 parser 标识。
- 同次真实调用的服务日志显示 `Using mlx-engine as the inference engine for VLM.`，确认当前 Mac 执行的是 MLX 本地推理，而不是远程 API 或 vLLM。
- 通过 `./scripts/dev-start.sh --skip-install --with-mineru-service` 验证迁移与种子初始化：既有项目自动新增 1 个 `MinerU（本地部署服务 / MLX）` profile；在前端设置页目检确认默认 MLX 路径和未保存切换后的 GPU/vLLM 服务地址输入均正常展示。

#### 已知问题与下一步

- 尚未用用户真实复杂 PDF 在完整前端交互中执行 `上传 -> 选择本地部署服务 -> ParseJob 完成 -> 进入清洗` 的质量验收；本轮已完成解析器真实服务调用与配置入口目检。
- Linux + NVIDIA GPU 部署后仍需用真实复杂 PDF 对 `vlm-http-client + vLLM` 完成吞吐、显存和输出质量验收。

---

## MinerU 本地解析服务化验证（2026-05-26）

### 第一阶段：独立服务与本地权重无 Token 冒烟

| 模块 | 内容 | 状态 |
|------|------|------|
| `scripts/mineru_local_service.py` | 新增独立 MinerU 服务配置、隔离环境安装、`mineru-api` 启动、健康检查和 PDF 转 Markdown 冒烟入口；可自动生成单页测试 PDF | 已实现并实测 |
| `docs/runbooks/mineru-local-service.md` | 记录本地服务验证步骤、Mac/未来 GPU 的后端边界、以及第二阶段平台接入方式 | 已完成 |
| `.gitignore` | 忽略独立服务虚拟环境 `.venv-mineru-service/`，避免重模型服务依赖进入平台工作树 | 已实现 |

#### 设计边界

- 本阶段只验证独立本地解析服务，不改动平台 `ParserProfile`、ParseJob 或前端解析入口；现有 `mineru_local + transformers` 继续可用。
- 服务使用官方 `mineru-api` 接收 PDF、组织解析结果，避免平台重新维护一套 PDF 编排服务；模型配置以 `MINERU_MODEL_SOURCE=local` 和本地 `models-dir.vlm` 为准。
- 当前 Mac 与未来 GPU 共用服务协议，但不强行共用推理引擎：Mac 验证本地服务可行性，Linux + NVIDIA GPU 阶段验收 vLLM。

#### 实测结果

- 使用独立原生 arm64 Python 3.12 环境安装官方 `mineru 3.1.15` 成功；平台原有 Python 3.11 `.venv` 未变更。
- 生成的 `logs/mineru-local-service/mineru.json` 将 `models-dir.vlm` 指向仓库现有 `models/MinerU2.5-Pro-2604-1.2B` 权重，未包含官方 API Token。
- 启动 `mineru-api` 后，`GET /health` 返回 `status=healthy`、`version=3.1.15`。
- 通过 `POST /file_parse` 以 `backend=vlm-auto-engine` 提交自动生成的一页 PDF，成功返回 Markdown `MinerU local service PDF smoke test`。
- 服务日志明确显示当前 macOS 自动使用 `mlx-engine`；服务环境中 `mlx_vlm` 可用而 `vllm` 未安装。因此已完成“本地服务 + 本地权重 + 无 Token”验证，尚未宣称完成 vLLM 推理验证。
- `python -m py_compile scripts/mineru_local_service.py` 与 `ruff check scripts/mineru_local_service.py` 通过。

#### 后续延续

- `mineru_local_service` ParserProfile 和平台 parser 适配器已于 2026-05-27 接入，通过本地 `mineru-api` 返回 ParseJob 结果，并与旧 `mineru_local` 并存。
- 未来迁移到 Linux + NVIDIA GPU 时，启动加载同一类自有权重的 vLLM/OpenAI-compatible 服务，令 `mineru-api` 使用 `backend=vlm-http-client` 完成吞吐与质量验收。

---

## 解析入口、任务展示与清洗来源隔离（2026-05-26）

### 解析来源作为清洗上下文的前后端调整

| 模块 | 内容 | 状态 |
|------|------|------|
| `apps/web/.../documents/[did]/page.tsx` | 解析记录移除不可核验的百分比进度；主入口始终为“选择解析结果进行清洗”；每条完成的解析记录始终保留“进入此解析结果”，并复用或创建其独立清洗上下文 | 已实现 |
| `apps/web/.../documents/[did]/clean/page.tsx` | 以 URL 中的 `cleaning_job_id` 固定当前解析来源；来源选择器展示解析器及时间；章节、版本、分派、合并和终审操作均携带该上下文，切换时防止旧异步响应回填 | 已实现 |
| `apps/web/.../tasks/page.tsx` | 文档解析任务不再显示估算百分比；任务状态刷新改为响应 WebSocket 上下文的最新事件，避免错误事件名导致页面不更新 | 已实现 |
| `apps/web/src/components/task-floating-panel.tsx` / dashboard layout | 浮窗改用项目级任务接口和 `queued` / `processing` 状态；解析任务展示状态文字；修正其他任务百分比被重复放大的显示错误 | 已实现 |
| `apps/api/app/routers/documents.py` / `schemas/document.py` | 新增清洗上下文列表和类型化启动响应；同一 ParseJob 的正常入口复用已有 CleaningJob；清洗写操作和版本读取要求显式指定 `cleaning_job_id` | 已实现 |
| `apps/api/app/services/section_service.py` / `clean_version_service.py` | 章节分派按 CleaningJob 校验；合并版本、终审和版本列表绑定指定解析来源，不再默认为整个文档混合读取 | 已实现 |
| `apps/api/app/workers/clean_worker.py` / `chunk_worker.py` | worker 填充入口创建的 CleaningJob；无明确终审版本时，如发现多个已通过清洗来源则阻止切分，避免静默混合 | 已实现 |

#### 设计边界

- `CleaningJob` 作为解析结果进入清洗阶段后的隔离键；同一 ParseJob 通过正常界面重复进入时回到已有工作台，不再因文档总体状态改变入口含义。
- 已有历史 CleaningJob 不自动删除或重写；如数据库中已经存在同一来源的历史重复上下文，工作台选择器以清洗创建时间区分并允许进入。
- 本轮未自动迁移此前已生成的混合版本或分块结果；新的清洗加载、版本合并和切分入口已有隔离与防混合保护。

#### 验证状态

- `apps/web` 执行 `tsc --noEmit` 通过。
- 本次涉及的文档详情、清洗工作台、任务页、任务浮窗与 dashboard layout 文件执行定向 `eslint` 通过。
- `apps/api` 修改文件执行 `ruff check`、`python -m py_compile` 通过，OpenAPI 契约检查确认分派、合并、终审和版本接口均要求 `cleaning_job_id`。
- `pytest tests -q` 通过，共 5 项测试通过。
- 全量 `npm run lint` 仍失败，剩余报错位于未纳入本轮范围的页面和认证上下文，主要为既有 `react-hooks/set-state-in-effect` 规则问题。
- `next build` 的默认 Turbopack 路径失败于 `next/font` 的 Google Font 内部模块解析；Webpack 复核路径持续阻塞于 `fonts.gstatic.com` 网络连接，未获得可用于判断本轮代码的构建结果。
- 尝试启动本地应用进行浏览器目检时，启动脚本检测到 Docker Desktop 未运行，因此未进行交互式页面验收。

#### 已知问题与下一步

- 在两个客户端极短时间内同时为同一 ParseJob 首次创建清洗任务时，目前依靠入口交互防重，数据库层尚未增加唯一约束。
- Docker Desktop 可用后，应分别用 MinerU 与 PaddleOCR 的同一文档结果进入清洗工作台，目检来源切换、章节隔离、合并版本和切分拦截表现。

---

## MinerU / PaddleOCR 远程 API 解析接入（2026-05-26）

### 真实服务协议适配与凭据安全加固

| 模块 | 内容 | 状态 |
|------|------|------|
| `libs/parsing/parsing/mineru_parser.py` | 按 MinerU 精准解析官方流程实现本地 PDF 的签名上传、批次轮询、ZIP 归档下载与 Markdown/JSON 提取；修复 OSS 签名上传的 Content-Type 要求 | 已实现并真实联通 |
| `libs/parsing/parsing/paddleocr_parser.py` | 按 PaddleOCR `layout-parsing` API 实现 `Authorization: token` 鉴权、camelCase 请求参数与页级 Markdown 聚合 | 已实现并真实联通 |
| `apps/api/app/config.py` / `workers/parse_worker.py` | 新增 `MINERU_API_TOKEN`、`PADDLEOCR_API_TOKEN` 服务端配置，由 worker 执行时注入 parser，不将凭据写入 ParserProfile | 已实现 |
| `apps/api/app/schemas/config.py` | ParserProfile 新写入拒绝内嵌密钥；对历史 `api_key` / `token` 字段响应脱敏 | 已实现 |
| `apps/web/.../settings/page.tsx` | 解析器设置页仅保留远程 API 地址配置，提示密钥由后端环境变量维护 | 已实现 |
| `scripts/dev-start.{sh,ps1}` / `.gitignore` | 新环境模板预留远程 parser token 变量；保护本地 token 文档不被误提交 | 已实现 |
| `tests/test_remote_parsers.py` / `tests/test_parser_credentials.py` | 覆盖远程 API 请求/响应契约、签名上传要求、worker 凭据注入与配置响应脱敏 | 已完成 |

#### 设计说明

- `mineru` 使用远程 MinerU 精准解析 API：先申请批量文件上传 URL，再将 PDF 原始字节 PUT 到签名地址，轮询批次结果，最后读取结果 ZIP 中的 Markdown 和结构化 JSON。
- MinerU 产物只持久化解析所需摘要与内容，不持久化返回的一次性签名下载 URL；签名上传/下载失败时也不会把存储服务错误正文写入任务错误字段。
- `paddleocr` 使用已配置的完整 `/layout-parsing` 端点，按官方协议发送 PDF base64，并从 `result.layoutParsingResults[*].markdown.text` 组合原始 Markdown；响应内图像 base64 不进入结构化产物。
- 远程 parser token 已迁移到本机 `apps/api/.env`；`models/models_api.md` 不再保留明文 token。后续配置应继续仅通过服务端环境变量管理密钥。

#### 验证状态

- 使用真实 MinerU API 对一页最小 PDF 完成 `签名上传 -> 异步解析 -> ZIP 下载 -> Markdown 提取` 冒烟测试。
- 使用真实 PaddleOCR API 对一页最小 PDF 完成 `/layout-parsing -> Markdown 聚合` 冒烟测试。
- `pytest tests -q` 通过，共 5 项远程 parser 与凭据安全测试通过。
- `ruff check`、`python -m py_compile`、`bash -n scripts/dev-start.sh` 与前端 `tsc --noEmit` 均通过本次改动检查。

#### 下一步

- 在网页端分别选择 `MinerU（API）` 与 `PaddleOCR（API）` 发起实际文档 ParseJob，检查 MinIO 产物、清洗入口和长文档耗时表现。
- 对多页含表格/公式/扫描页样本比较 `pymupdf4llm`、`mineru_local`、`mineru` 与 `paddleocr` 输出质量。

---

## MinerU 本地解析接入（2026-05-26）

### 第二阶段：后端与配置中心接入

| 模块 | 内容 | 状态 |
|------|------|------|
| `libs/parsing/parsing/mineru_local_parser.py` | 新增 `mineru_local`，按页渲染 PDF，使用本地 MinerU2.5-Pro + `transformers` 生成 Markdown/结构结果，进程内缓存模型并串行推理 | 已实现 |
| `apps/api/app/workers/parse_worker.py` | ParseJob 输出按任务 ID 版本化存储；失败状态精确回写当前任务并保留结束时间 | 已实现 |
| `libs/parsing/pyproject.toml` | 将 `mineru-vl-utils[transformers]` 纳入解析库标准运行依赖，使普通 API 启动即可发起本地 MinerU 任务 | 已实现 |
| `uv.lock` | 锁定 `mineru-vl-utils[transformers]`、`transformers` 与 `torch` 等本地推理运行时版本 | 已实现 |
| `apps/web/.../settings/page.tsx` | 解析器设置增加 `MinerU2.5-Pro（本地模型）` 及模型目录、设备、DPI、图片分析配置 | 已实现 |
| `scripts/init_seed.py` | 新数据库创建本地 MinerU profile；既有项目启动时幂等补齐缺失 profile | 已实现 |
| `scripts/dev-start.{sh,ps1}` | 普通一键启动自动同步 MinerU 推理运行库，模型仍按任务懒加载；保留旧参数兼容；修复 macOS Bash 3.2 空数组展开错误 | 已实现 |
| `docs/runbooks/mineru-local-parser.md` | 正式启用、选择解析器、结果存储及排错说明 | 已完成 |

#### 设计说明

- `mineru` 继续表示远程 API 解析器；新增 `mineru_local` 表示读取本机模型权重，不破坏原有配置。
- 默认 `pymupdf4llm` 仍保持快速解析路径；MinerU 推理运行库随正常安装准备好，模型权重只有发起 `mineru_local` 任务时才载入内存。
- 模型在 API 进程第一次执行 `mineru_local` 任务时加载并复用；本机推理通过锁限制为串行执行。
- 解析输出改为 `parsed/<parse_job_id>/raw.md` 与 `structured.json`，避免再次解析覆盖历史结果。

#### 验证状态

- 用户已确认第一阶段本地模型能够加载并完成解析任务。
- `ruff check` 与 `python -m py_compile` 已通过本地解析器、解析 worker、种子脚本和第一阶段测试脚本检查。
- 使用替身推理客户端完成 `PDF -> 页面渲染 -> MinerU 调用 -> Markdown/结构化结果` 的封装链路测试。
- `bash -n` 及启动脚本帮助输出已验证；在 macOS Bash 3.2 下模拟通过数据库调用路径；前端 `tsc --noEmit` 已通过。
- `uv lock --check --python 3.11` 已通过，确认本地 MinerU 标准运行依赖进入锁文件。
- 已执行普通 `uv sync --python 3.11 --extra dev`，项目 `.venv` 已安装 `mineru-vl-utils`、`transformers` 与 `torch`；普通 `uv run` 导入通过，且解析器模型缓存初始为 `0`，确认模型未在启动时预加载。
- `npm run lint` 仍存在项目原有 React hook / 未使用导入等规则报错，本阶段未扩大处理范围，保留为测试加固事项。
- 仍需在网页端按普通一键启动后执行一次 `上传/选 MinerU 本地解析/ParseJob 完成/进入清洗` 的集成验收。

---

## MinerU 本地解析验证（2026-05-25）

### 第一阶段：独立冒烟测试脚本

| 模块 | 内容 | 状态 |
|------|------|------|
| `scripts/test_mineru_local.py` | 从 PDF 按指定页码渲染页面图片，并按官方 `mineru-vl-utils[transformers]` 路径调用本地 `MinerU2.5-Pro-2604-1.2B`，保存页级 Markdown / JSON 和运行报告 | 已实现，用户已完成模型验证 |
| `docs/runbooks/mineru-local-smoke-test.md` | 说明渲染检查、首次推理、扩页测试和通过标准 | 已完成 |

#### 设计边界

- 脚本与现有 `ParserProfile`、ParseJob、MinIO 流程隔离，不改变当前默认 `pymupdf4llm` 解析行为。
- 默认仅测试第 1 页，支持 `--render-only`，先确认 PDF 页面输入无误后再加载重模型。
- 默认读取仓库内 `models/MinerU2.5-Pro-2604-1.2B`，并通过 `local_files_only=True` 避免运行时重新下载模型权重。
- 第一阶段验证时 MinerU 推理依赖采用临时实验调用；第二阶段正式接入后已纳入解析库标准运行依赖。

#### 验证

- `ruff check scripts/test_mineru_local.py` 通过。
- `python -m py_compile scripts/test_mineru_local.py` 通过。
- 使用临时单页 PDF 执行 `--render-only` 通过，成功生成页面 PNG 和 `run-report.json`。
- 页码超范围错误路径通过，失败信息与报告均可读。
- 未安装推理附加依赖时的提示路径通过，能够给出首次完整测试命令。

#### 待验证与后续

- 用户已反馈本地 MinerU 模型能够加载成功并完成解析任务。
- 后续工作已转入 `mineru_local` ParserProfile 接入、ParseJob 结果版本化存储和网页端集成验收。

---

## 工具链与本地运行改进（2026-05-21）

### 一键启动/停止脚本增强

| 模块 | 内容 | 状态 |
|------|------|------|
| `scripts/dev-start.sh` | 自动创建本地 env、启动 Docker 基础设施、安装缺失依赖、执行迁移与种子数据、后台启动 API/Web 并记录 PID | 已完成 |
| `scripts/dev-stop.sh` | 按 PID 停止 API/Web，按端口兜底清理，可选 `--all` 停止 Docker | 已完成 |
| `scripts/dev-start.ps1` | PowerShell 等价实现，使用 `uv` 替代固定 conda 环境假设 | 已完成 |
| `scripts/dev-stop.ps1` | PowerShell 等价停止脚本，支持 `-All` | 已完成 |
| `docs/runbooks/new-machine-runbook.md` | 新增一键启动/关闭说明，并保留手动排查步骤 | 已完成 |

#### 关键变化

- 首次运行若缺少 `infra/docker/.env`，会自动从模板复制。
- 首次运行若缺少 `apps/api/.env`，会生成本机连接 Docker 服务的配置。
- 首次运行若缺少 `apps/web/.env.local`，会写入默认 API / WebSocket 地址。
- 后端依赖优先使用 `uv sync --extra dev`，不再强依赖 `conda DatasetGen`。
- 前端依赖缺失时自动执行 `npm ci`。
- 数据库迁移和种子脚本纳入一键启动流程，均为幂等执行。
- 修复 uv 自动选择 Python 3.14 导致 `passlib/bcrypt` 初始化失败的问题：项目锁定 Python 3.11，并固定 `bcrypt>=4.0.1,<4.1`。
- 一键脚本现在会检测根目录 `.venv` 的 Python 版本；若发现不是 3.11，会提示删除 `.venv` 后重建，避免继续使用错误解释器。
- 一键脚本增加前端端口冲突处理：若 `localhost:3000` 被其他项目占用，会自动尝试 `3001-3005`，并打印真实访问地址；API CORS 默认允许这些本地开发端口。
- 停止脚本移除按端口兜底杀进程逻辑，只按启动脚本记录的 PID 关闭本项目 API/Web，避免误停用户其他 Docker 项目。

#### 验证

- `bash -n scripts/dev-start.sh` 通过。
- `bash -n scripts/dev-stop.sh` 通过。
- 当前环境未安装 PowerShell，`*.ps1` 未在本机执行语法验证。

#### 下一步

- 在真实 Windows PowerShell 环境中执行 `scripts/dev-start.ps1` / `scripts/dev-stop.ps1` 进行目检验证。
- 如后续需要团队部署，可增加 `--no-install`、端口冲突检测和更细粒度日志查看命令。

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
| 3 | 文档上传 → 解析 | ✅ 已通过 | #10~#21 (上传性能、解析器、状态展示、删除)；清洗来源选择入口稳定化 |
| 4 | 清洗工作台 | 🔄 新隔离待联调 | #22~#29, #31~#32 (PDF加载、三栏布局、字段对齐、滚动、编辑器主题)；新增按解析来源隔离 |
| 5 | 切分 | 🔄 防混合待联调 | #33 (分块列表字段不匹配)；新增多清洗来源保护 |
| 6 | LLM 生成 | ⏳ 待测试 | — |
| 7 | 审核 → 提升 | ⏳ 待测试 | — |
| 8 | 导出 | ⏳ 待测试 | — |
| 9 | 任务中心 | ✅ 部分通过 | WebSocket 实时推送已验证 |
| 10 | 前端联调 | 🔄 持续进行 | 每阶段均验证前后端交互 |

#### 主要改进（测试期间）

- **PDF 文件端点**：新增 `GET /documents/{did}/file`，支持 iframe 嵌入 (token query param 认证 + 浏览器缓存)
- **解析任务状态展示**：ParseJob 立即创建 + WebSocket 状态刷新；解析阶段不再展示不可核验的百分比进度
- **解析来源隔离**：清洗工作台以 CleaningJob 绑定 ParseJob；分派、版本合并与后续切分不再静默混用不同解析来源
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
