# T06 版本化切分：运维与故障 Runbook

> 覆盖 ChunkSet 版本化的部署、迁移/回滚、故障排查与数据一致性检查。
> 真源：PostgreSQL `chunk_sets` / `chunks` / `documents.active_chunk_set_id`；
> 切分 Task 由 T07 持久队列执行（`tasks` / `task_attempts`）。

## 1. 架构与数据流

```
CleanedDocumentVersion (accepted + active)
        │ 冻结（config_json / source_sha256 / splitter_version）
        ▼
   ChunkSet (version 唯一；is_legacy=false 必填 task_id)
        │ 切分（chunk_document:v2，T07 runner 执行）
        ▼
      Chunk (chunk_set_id NOT NULL；唯一 chunk_set_id+ordinal)
        │ 原子发布（runner 完成事务）
        ▼
   Document.active_chunk_set_id 切换（旧集合保留可追溯）
```

- 每次显式切分产生新 ChunkSet version；历史集合不可覆盖。
- 切分只经 T07 dispatcher 执行（handler `chunk_document:v2`，payload 携带
  `chunk_set_id`）；API 进程重启不丢任务。
- 幂等：`Idempotency-Key`（`client_key:request_digest` 复合键）同 key 同请求
  重放返回同一 ChunkSet；同 key 不同请求 409；不同 key 活跃切分 409。

## 2. 部署

### 前置依赖

- 必须已部署 T07 持久任务队列（独立 runner 进程）。
- 必须已部署 T05 终审语义（active CleanedDocumentVersion 单调 accepted）。

### 迁移步骤

```bash
# 1. 执行迁移（合并 T07/T05 双 head）
cd apps/api
python -m alembic upgrade head

# 2. 核对回填审计输出
#   [T06] 版本回填：N 个 set 分配 version；legacy/可追溯标记：M
#   [T06] 孤儿 Chunk 回填：创建 X 个隔离 legacy set，绑定 Y 个 Chunk
#   [T06] 清空非法 active_chunk_set_id：Z 个
```

迁移语义：
- 迁移前 ChunkSet 按 `created_at,id` 分配稳定 version。
- 无法绑定 T07 持久 Task 或缺失必填字段（cleaned version/profile/config）的
  旧 set 置 `is_legacy=true` + `summary_json.provenance=legacy_unverified`，
  不伪造溯源。
- `chunk_set_id IS NULL` 的 Chunk 按 Document 创建隔离 legacy set 并稳定重排
  ordinal，Chunk id 不变，下游 FK 不受影响。
- `documents.active_chunk_set_id` 指向异文档/不存在的指针清空（不猜测填充）。

### 启动 runner

```bash
docker compose up -d --scale worker=3
# 或本地：
conda run -n DatasetGen python -m app.workers.runner
```

## 3. 监控

### 关键查询

```sql
-- 活跃切分（每文档最多一个 pending/processing）
SELECT document_id, status, version, task_id, created_at
  FROM chunk_sets
 WHERE status IN ('pending', 'processing');

-- 失败的切分（需人工 retry）
SELECT id, document_id, version, error_message, task_id
  FROM chunk_sets
 WHERE status = 'failed' ORDER BY created_at DESC;

-- 默认列表只应展示 active set
SELECT d.id, d.active_chunk_set_id, cs.version
  FROM documents d
  LEFT JOIN chunk_sets cs ON cs.id = d.active_chunk_set_id;

-- 孤儿 Chunk（不应存在；若有说明迁移失败）
SELECT count(*) FROM chunks WHERE chunk_set_id IS NULL;
```

### 指标

- 队列积压：`SELECT status, count(*) FROM tasks WHERE task_type='chunk' GROUP BY status;`
- 切分失败率：`status='failed'` 的 ChunkSet 数量。

## 4. 故障排查

| 症状 | 排查 |
|------|------|
| 切分卡在 processing | 检查 T07 runner 是否存活；`tasks.status`/`lease_expires_at`；reaper 是否回收 |
| 切分失败 CLEAN_VERSION_STALE | 发布前 T05 终审推进了新 active 版本；本次切分作废，用新 key 重新发起 |
| ChunkSet failed 但想重跑 | 调用 `POST /api/projects/{pid}/tasks/{tid}/retry`（带新 Idempotency-Key），
  复用同一冻结 ChunkSet，成功后只有一组 Chunk 与一个 active pointer |
| 前端看不到新 Chunk | 确认 `documents.active_chunk_set_id` 指向最新 completed set；默认列表只展示 active |
| 幂等 409 | 同 Idempotency-Key 但不同请求（不同 profile/cleaned_version）被拒绝；
  更换 key 或使用与首次一致的请求 |

## 5. 迁移回滚

### 预检

```bash
python -m alembic downgrade <T06 前一修订>
```

回滚前必须确认：
- 不存在 `failed/cancelled` 状态的 ChunkSet（旧 enum 无法表示）——命中即停止。
- 回滚只移除新增约束/列，保留历史 Chunk 与其 legacy 绑定，不删除历史数据。

### 步骤

```bash
cd apps/api
python -m alembic downgrade 58918ea257fd   # 回滚全部至基线
python -m alembic upgrade head             # 重新升级
```

- 若存在 `failed/cancelled` 集合，回滚抛出 RuntimeError 并停止（任务卡 §4.3.6）。
- 回滚后 `chunks.chunk_set_id` 恢复 nullable，legacy 绑定保留。

## 6. 数据一致性检查

```bash
# 审计：回填后 chunk_set_id 无 NULL、下游 FK 有效
python scripts/check_chunk_integrity.py   # 或等价 SQL

# 1. 无孤儿 Chunk
SELECT count(*) FROM chunks WHERE chunk_set_id IS NULL;  -- 应为 0

# 2. 每文档最多一个活跃集合（部分唯一索引兜底）
SELECT document_id, count(*) FROM chunk_sets
 WHERE status IN ('pending','processing') GROUP BY document_id HAVING count(*) > 1;  -- 应为空

# 3. active pointer 合法
SELECT d.id, d.active_chunk_set_id, cs.document_id
  FROM documents d LEFT JOIN chunk_sets cs ON cs.id = d.active_chunk_set_id
 WHERE cs.id IS NULL OR cs.document_id <> d.id;  -- 应为空
```
