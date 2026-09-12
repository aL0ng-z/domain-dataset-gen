# T07 任务生命周期：运维与故障 Runbook

> 覆盖持久任务队列的部署、监控、故障排查与迁移/回滚操作。
> 真源：PostgreSQL `tasks` / `task_attempts` 表；Redis 仅用于 WebSocket 事件提示，
> 不是状态真源。REST API 永远返回数据库当前状态。

## 1. 架构与状态机

```
queued ──claim──▶ processing ──handler──▶ completed
  │                  │  │
  │ cancel           │  ├──可重试失败+退避──▶ queued（重试）
  │                  │  │
  ▼                  │  └──永久失败/超限──▶ failed
cancelled ◀──cancel──┴──cancelling──cancel──▶ cancelled
```

- 合法转换仅：`queued->processing|cancelled`、`processing->queued|completed|failed|cancelling`、
  `cancelling->cancelled`。
- 终态 `completed/failed/cancelled` 不可原地回退；人工 retry 创建新 Task
  （`retry_of_task_id` 指向源任务）。
- 至少一次执行 + 幂等 handler：不虚假承诺 exactly-once。

## 2. 部署

### 服务拓扑

| 服务 | 职责 | 说明 |
|------|------|------|
| API（uvicorn） | 触发业务 Task 创建、REST 查询 | 重启不丢任务（队列在 PostgreSQL） |
| worker（runner） | 轮询领取到期 queued Task 并执行 handler | `python -m app.workers.runner`，可多副本 |
| PostgreSQL | 队列真源 + CAS 状态机 | `tasks` / `task_attempts` |
| Redis | WebSocket 事件发布 | 不可用时 REST 仍是真源，功能不受损 |

### 启动

```bash
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env --profile worker up -d
# 容器 runner 需要显式启用；扩容仍使用相同 compose 文件和环境：
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env --profile worker up -d --scale worker=3
```

本地开发启动 runner：
```bash
conda run -n DatasetGen python -m app.workers.runner   # 需 POSTGRES_* 环境变量指向目标库
```

## 3. 监控

### 关键指标（可从数据库查询）

```sql
-- 队列积压
SELECT status, count(*) FROM tasks GROUP BY status;

-- 卡死的 processing（lease 过期未回收）
SELECT id, status, lease_expires_at FROM tasks
 WHERE status='processing' AND lease_expires_at < now() - interval '5 minutes';

-- 重试/失败分布
SELECT error_code, count(*) FROM tasks WHERE status='failed' GROUP BY error_code;
```

### 需要告警的状态

- 任何 `tasks.status='processing'` 且 `lease_expires_at` 长期过期（reaper 未工作）。
- `failed` 任务激增（handler 永久失败或被外部限流）。
- worker 进程数持续为 0（无派发）。

## 4. 故障排查

### 4.1 任务一直 queued，不被领取

1. worker 是否运行？`docker compose ps` 查看 worker 容器。
2. 是否有到期任务？`SELECT id, next_run_at FROM tasks WHERE status='queued' AND next_run_at <= now();`
3. worker 日志是否有异常（handler 未注册 / 数据库连接失败）？

### 4.2 任务 stuck processing

- reaper 每 30s 回收 `lease_expires_at` 过期的 processing：
  - 有取消请求 -> cancelled；
  - 可重试错误且有额度 -> 回 queued 退避；
  - 否则 failed。
- 若长期 stuck，手动查看 `lease_expires_at` 与 worker 是否存活。

### 4.3 取消未生效

- queued 取消是原子的（立即 cancelled）。
- processing 取消 -> cancelling，handler 在下一次 checkpoint 检测到取消请求后中止。
- 若 handler 没有 checkpoint（外部长调用未设置心跳），取消可能延迟到 lease 回收。
- 已发布到第三方的远程请求无法保证撤销；UI/文档如实说明「取消保证不再发布本地产物」。

### 4.4 retry 未执行

- retry 只接受 `failed` 任务（completed/cancelled 返回 409）。
- retry 需要 `Idempotency-Key`；同 key 并发只生成一个后继（部分唯一索引保护）。
- 新 Task 是独立行，`retry_of_task_id` 指向源任务；原任务保持 failed 不复活。

## 5. 迁移与回滚

### 升级

```bash
cd apps/api
python -m alembic upgrade head
# t07_task_lifecycle 是双父 mergepoint；upgrade head 已收敛单一 head。
```

### 回滚预检

downgrade 前必须停止 runner，且不存在以下任务：
- `status='cancelling'`；
- 新格式非终态任务（`status IN ('queued','processing') AND (payload IS NOT NULL OR handler IS NOT NULL)`）。

```bash
python -m alembic downgrade 58918ea257fd   # 回到公共祖先（mergepoint 的 -1 歧义）
```

### 数据保留

- `task_attempts` 审计表不可覆盖/删除（历史只读）。
- 回滚删除 `task_attempts` 前须显式导出（生产环境数据保留决定）。

## 6. 常见错误码

| error_code | 含义 | 是否重试 |
|------------|------|----------|
| `NETWORK_ERROR` / `RATE_LIMITED` / `TEMPORARY_INFRA_ERROR` | 临时失败 | 是（退避） |
| `RESOURCE_NOT_FOUND` / `VALIDATION_FAILED` / `CONTRACT_VIOLATION` | 永久失败 | 否 |
| `UNSUPPORTED_TASK_PAYLOAD` | 未知 handler/version | 否（只一次） |
| `LEGACY_TASK_NOT_RESUMABLE` | 迁移前无法重建 payload 的旧任务 | 否 |
| `TASK_CANCELLED` | 已取消 | - |
