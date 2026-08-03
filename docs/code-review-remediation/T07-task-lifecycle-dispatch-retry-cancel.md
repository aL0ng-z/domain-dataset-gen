# T07：任务生命周期、派发、重试与取消

- 状态：代码及自动化验收完成，待真实 R1 联调
- 优先级：P1
- 建议规模：L
- 直接依赖：T01、T02、T04
- 可并行：可与 T05、T06 并行；T08、T11 依赖本卡

## 1. 目标

让 `Task` 成为可恢复的真实执行记录，而不是 HTTP 请求内 `BackgroundTasks` 的进度标签。创建任务后即使 API 进程重启也能派发；retry 必须产生真实执行；cancel 必须阻止尚未发生的发布；worker、API 与 WebSocket 不得把终态重新覆盖。系统明确采用“至少一次执行 + 幂等 handler”，不虚假承诺 exactly-once。

## 2. 范围

1. 用 PostgreSQL 持久任务队列和独立 runner 替换 FastAPI 进程内后台任务。
2. 持久化无密钥的版本化 handler payload、策略快照、尝试次数、执行 lease 和取消请求。
3. 建立数据库约束和 compare-and-set 状态机。
4. 实现自动重试、人工重试、协作式取消、超时回收和 worker 崩溃恢复。
5. 为 parse、clean、chunk、single/batch generate、dataset/benchmark export 注册 handler。
6. 统一父子任务聚合规则，供 T08 的 GenerationBatch 使用。
7. 更新任务中心及 WebSocket 的乱序、重连和操作状态。

## 3. 明确不做

- 不引入 Celery/Kubernetes，也不建设通用工作流编排平台。
- 不修改解析、切分、生成、审核、导出的业务产物合同；各业务卡负责其内部正确性。
- 不保证能撤销第三方已经接受的远程请求；取消保证不再发布本地产物，并尽力中止可取消的 I/O。
- 不把 API key、临时下载 URL、PDF 二进制、Prompt 全文等敏感/大对象写入 payload。
- 不用 Redis Pub/Sub 充当任务队列或任务状态真源。

## 4. 数据库合同

### 4.1 tasks

在现有表上新增或收紧：

- `handler VARCHAR(80)`、`payload JSONB`、`payload_version INTEGER`；新任务均非空，payload 只存资源 id 和非敏感选项。
- `idempotency_key VARCHAR(100)`，唯一约束 `(project_id, task_type, idempotency_key)`。
- `state_version BIGINT NOT NULL DEFAULT 0`，每次状态/进度变更递增，供 CAS 和事件去重。
- `attempt_count INTEGER NOT NULL DEFAULT 0`、`max_attempts INTEGER NOT NULL`、`timeout_seconds INTEGER NOT NULL`、`next_run_at TIMESTAMPTZ NOT NULL`。
- `lease_owner VARCHAR(150)`、`lease_expires_at TIMESTAMPTZ`、`heartbeat_at TIMESTAMPTZ`、`run_token UUID`。
- `cancel_requested_at TIMESTAMPTZ`、`cancel_requested_by UUID`、`error_code VARCHAR(80)`、`result_json JSONB`、`updated_at TIMESTAMPTZ`。
- `retry_of_task_id UUID REFERENCES tasks(id)`；同一源任务最多一个非终态人工重试后继。
- `is_legacy BOOLEAN NOT NULL DEFAULT false`，仅迁移前无法恢复 payload 的历史行可为 true。
- CHECK：progress 在 0..100、attempt/max/timeout 合法；processing/cancelling 必须有 run token 与 lease；终态必须有 completed_at。
- claim 索引覆盖 `(status, next_run_at, lease_expires_at)`，列表索引覆盖 `(project_id, created_at DESC)`。

`task_status` 增加 `cancelling`。合法转换仅为：

```text
queued -> processing | cancelled
processing -> queued | completed | failed | cancelling
cancelling -> cancelled
```

终态 `completed/failed/cancelled` 不再原地返回 queued；人工 retry 创建新 Task。所有转换必须使用 `WHERE id=? AND status=? AND state_version=?` 或等价锁定，受影响行数不是 1 即视为竞态失败。

### 4.2 task_attempts

新增审计表：`id, task_id, attempt_no, run_token, worker_id, status, started_at, heartbeat_at, finished_at, error_code, error_message, retriable, metrics_json`。

- 唯一约束 `(task_id, attempt_no)` 和 `run_token`。
- attempt 状态为 `processing/completed/failed/timed_out/cancelled/abandoned`。
- Task 错误展示可更新，Attempt 历史不可覆盖或删除。
- `max_attempts = TaskPolicy.max_retries + 1`，Task 创建时冻结策略；后续修改 Policy 不改变在途任务。

### 4.3 claim、超时与迁移

- runner 用 `SELECT ... FOR UPDATE SKIP LOCKED` 领取到期 queued Task，递增 attempt，生成 run token/lease，并在一个事务内创建 Attempt。
- worker 心跳和所有状态提交必须携带 run token；过期 worker 的提交影响 0 行，不能覆盖新 attempt 或终态。
- reaper 对过期 processing：有 cancel request 则转 cancelled；可重试错误且有额度则回 queued 并设置指数退避；否则 failed。
- 迁移时 terminal 历史任务保留；缺少可重建 payload 的 queued/processing 旧任务标记 `failed + LEGACY_TASK_NOT_RESUMABLE` 并记录数量，不猜测执行参数。
- downgrade 前必须停止 runner；若存在 `cancelling` 或新格式非终态任务则停止回滚。Attempt 审计表的删除需显式数据保留/导出决定。

## 5. Handler 与失败合同

- handler registry 以稳定名和 payload version 分派，例如 `parse_document:v1`、`clean_document:v1`、`chunk_document:v1`、`generate_single:v1`、`generate_batch:v1`、`export_dataset:v1`、`export_benchmark:v1`。
- 未知 handler/version 为永久失败 `UNSUPPORTED_TASK_PAYLOAD`，不得无限重试。
- 每个 handler 接收 `ExecutionContext`，在外部调用前后、批次循环和发布事务前调用 `checkpoint()`；它校验 run token、lease、timeout 和 cancel request。
- 可重试错误仅包括明确的网络/限流/临时基础设施失败；校验失败、资源不存在、合同错误为永久失败。
- handler 的业务写入使用稳定幂等键或唯一约束；对象存储使用 task/attempt 唯一临时 key，发布前校验 fencing token，失败时可安全清理孤儿对象。
- runner 捕获异常并落库后再确认该 attempt；禁止 worker 吞掉异常后由父任务标 completed。
- 父任务仅在全部要求的子任务 completed 时 completed；任一不可恢复失败则 failed；父任务 cancel 向所有非终态子任务传播，聚合更新也使用 CAS。

## 6. API 合同

- 所有业务触发 API 只在事务中创建业务 job + Task，提交后由 runner 领取，不再调用 `background_tasks.add_task`。
- 触发 API 支持 `Idempotency-Key`；同 key/同请求返回同 Task，不同请求摘要返回 `409 IDEMPOTENCY_KEY_REUSED`。
- `GET /projects/{pid}/tasks/{tid}` 响应新增：`state_version, attempt_count, max_attempts, next_run_at, cancel_requested_at, retry_of_task_id, can_cancel, can_retry, error_code`。
- `GET /projects/{pid}/tasks/{tid}/attempts` 返回按 attempt_no 排序的审计列表，不返回 payload 中可能敏感的内部字段。
- `POST .../{tid}/cancel`：queued 原子转 cancelled；processing 转 cancelling 并返回 `202`；重复请求幂等返回当前状态；终态返回当前对象且不改写完成时间。
- `POST .../{tid}/retry` 只接受 failed Task，要求新的 `Idempotency-Key`，创建 `retry_of_task_id=tid` 的新 queued Task并返回 `201`；不得复活原行。
- 对 completed/cancelled retry 返回 `409 TASK_NOT_RETRYABLE`；不同项目统一按 T02 返回 404。
- WebSocket 事件含 `task_id/status/progress/state_version/attempt_count/event_at`；事件只用于提示刷新，REST 始终是真源。

## 7. 前端合同

- 任务页展示 `cancelling`、当前 attempt/max、下次重试时间、稳定 error code/消息和 retry 后继链接。
- Cancel 按钮仅由 `can_cancel` 控制；点击后立即显示“取消中”，不谎报“已取消”。
- Retry 使用一次点击一个 idempotency key；成功后定位到返回的新 Task，而不是等待旧 Task 变 queued。
- 按 `state_version` 丢弃乱序 WebSocket 消息；断线重连或收到任一事件后重新 GET，不能仅靠本地事件拼状态。
- processing/cancelling 任务保留轮询兜底；Redis 不可用时页面仍能看到最终状态。
- 重复点击、请求超时和页面刷新不得产生多个 retry 后继或重复业务任务。

## 8. 预期修改面

- `apps/api/app/models/task.py`、`models/config.py` 与新 `TaskAttempt` 模型
- `apps/api/app/schemas/task.py`、`services/task_service.py`、`routers/tasks.py`
- 所有创建 Task 的业务路由与 `apps/api/app/workers/*.py`
- 新 handler registry、runner、reaper、ExecutionContext 与启动入口
- `infra/docker/` 或当前部署清单中新增独立 worker 服务
- `apps/api/app/ws/task_ws.py`
- 任务页、浮动任务面板、生成 API 类型和前端测试
- Alembic 迁移、后端集成/故障测试、runbook、根目录 `DevLog.md`

## 9. 依赖和风险

- T01/T02 保证 access token 与对象授权；T04 保证状态/错误合同；T00 提供多连接数据库测试。
- PostgreSQL 队列会增加连接和轮询负载；用有界 batch、索引、退避和连接池隔离控制。
- 至少一次执行会暴露非幂等 handler；每个 handler 必须逐项审计发布点和唯一约束。
- 外部 API 可能无法真正撤销；取消后的远程结果只能丢弃，UI/文档必须如实说明。
- API 与 worker 版本滚动升级可能遇到新 payload；runner 必须拒绝未知版本并支持至少当前/上一版本的部署窗口。

## 10. 实施步骤

1. 固化状态图、错误分类、payload schema 和 handler 注册表。
2. 编写 Task/Attempt 迁移、legacy 非终态处置和回滚预检。
3. 实现原子 create、claim、heartbeat、CAS transition、reaper 与策略退避。
4. 建独立 runner，把现有 BackgroundTasks 入口逐一改成持久 handler。
5. 为各 handler 加 cancellation checkpoint、幂等写与 run-token 发布门禁。
6. 实现 cancel/retry/attempt API、父子聚合和版本化事件。
7. 更新任务 UI、部署清单、监控指标和故障 runbook。
8. 执行进程崩溃、Redis 故障、并发 claim/cancel/retry、迁移往返和全量门禁。

## 11. 自动化验收标准

1. 创建 Task 后立即终止 API 进程，独立 runner 启动后仍能领取并完成。
2. 两个 runner 并发 claim 同一 Task，只有一个有效 run token 和一个 processing Attempt。
3. queued cancel 后 handler 调用次数为 0；processing cancel 在发布 checkpoint 前终止且无正式业务产物。
4. 旧 run token 在 lease 过期重领后不能更新进度、终态或发布指针。
5. 可重试错误按 Policy 退避并在额度内成功；永久错误只执行一次；超限稳定 failed。
6. 人工 retry 返回新 Task 并被真实执行；同 idempotency key 并发点击只生成一个后继。
7. completed/failed/cancelled 终态在迟到 worker、重复事件和重复 API 操作下保持不变。
8. 一个子任务失败时父任务不得 completed；父取消传播到 queued/processing 子任务。
9. Redis 停止时派发、状态和 REST 查询仍正常；恢复后 UI 通过 REST 收敛。
10. legacy 非终态迁移结果为可审计 failed，Alembic 往返及全部 lint/test/build 返回 0。

## 12. 停止条件

- 部署环境无法运行独立长期 worker，且团队未批准其他持久队列方案。
- 某 handler 存在不可幂等、不可查询且不可隔离发布的不可逆外部副作用。
- 产品要求“取消”必须终止第三方远程作业，但供应商没有 cancel API 或 job id。
- payload 必须存明文凭证才能重放；应先设计凭证引用机制。
- 迁移时存在正在执行且不能安全停止/识别参数的生产 Task。

## 13. 审查重点

- 是否完全移除请求内 BackgroundTasks 作为业务任务执行器。
- claim/heartbeat/终态是否全部用 run token 和数据库 CAS 防迟到写。
- retry 是否真正派发且保留旧任务/attempt 审计，cancel 是否在发布前设门禁。
- retriable 分类是否保守，Policy 是创建时快照还是运行时漂移。
- payload 是否最小、版本化且不含密钥；父子状态是否可能假 completed。

## 14. 完成定义

- 所有现有异步业务均通过持久 runner 执行，API 重启不会丢任务。
- retry、cancel、超时、崩溃恢复与父子聚合符合状态图并有自动化证据。
- Task/Attempt、REST、WebSocket 和前端展示最终一致，Redis 不是真源。
- 迁移/回滚/runbook 完整，根目录 `DevLog.md` 已用中文记录实施和验证结果。
