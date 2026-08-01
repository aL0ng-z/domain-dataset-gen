# 项目资源授权权限矩阵（T02）

> 对象级授权的唯一事实源是 `apps/api/app/authz.py`（`check_project_member` +
> `ProjectResourceResolver`）。本矩阵描述每个资源动作的最低角色与跨项目语义。
>
> - 全局 `admin` 可绕过成员与最低项目角色检查，**但不能**绕过「资源真实属于
>   URL 中 pid」的绑定（用错误 pid 访问真实资源一律 404）。
> - 资源不存在，或资源不属于 URL 中 pid -> **404**（不泄露哪一个 ID 存在）。
> - 项目存在但用户非成员 / 角色不足 -> **403**。
> - 未登录 / 无效 / refresh token -> **401**。
> - 同项目父子 ID 组合不成立 -> **404**。

## 角色层级

viewer < editor < reviewer < admin（`ROLE_HIERARCHY`）

## 嵌套路由（`/api/projects/{pid}/...`）

| 资源 | 读 (viewer) | 写/动作 (最低角色) | 归属链 |
|---|---|---|---|
| Document | GET `/{did}`、`/file`、`/parse-jobs`、`/cleaning-jobs`、`/sections`、`/chunks`、`/cleaning/versions` | DELETE `/{did}`、POST `/parse`、`/cleaning/start`、`/chunk`、`/generate-batch` (editor)；`/cleaning/assign`、`/cleaning/merge`、`/cleaning/final-review` (reviewer) | `Document.project_id` |
| ParseJob | GET `/parse-jobs/{jid}` (viewer) | DELETE `/parse-jobs/{jid}` (editor) | ParseJob → Document.project_id |
| CleaningJob | 列表 (viewer) | start/assign/merge/final-review 见上 | CleaningJob → Document.project_id |
| Task | GET `/{tid}` (viewer) | POST `/cancel`、`/retry` (editor) | `Task.project_id` |
| 配置 (ModelConfig/ParserProfile/ChunkProfile/ExportProfile/TaskPolicy) | GET (viewer) | POST/PATCH/DELETE/set-default/test (editor) | 资源.project_id |
| PromptTemplate | GET (viewer) | POST/PATCH/duplicate/test-run (editor) | 资源.project_id |
| CuratedItem | GET (viewer) | PATCH、add-to-dataset/add-to-benchmark (editor) | `CuratedItem.project_id` |
| Dataset/Benchmark | GET (viewer) | POST/PATCH/DELETE/items/cases/export (editor) | `Dataset.project_id` / `Benchmark.project_id` |
| Export | GET (viewer) | —（导出由动作派发） | `Export.project_id` |
| Monitoring | GET (viewer) | — | LlmUsageLog.project_id |

## 平铺路由（无 URL pid，经归属链反查项目）

| 资源 | 读 (viewer) | 写/动作 (最低角色) | 归属链 |
|---|---|---|---|
| Section `/api/sections/{sid}` | GET、comments、revisions (viewer) | PATCH、submit、lease、complete (editor)；review、assign、return (reviewer) | Section → Document.project_id |
| Chunk `/api/chunks/{cid}` | GET (viewer) | PATCH、generate (editor) | Chunk → Document.project_id |
| Candidate `/api/candidates/{cid}` | GET、comments (viewer) | PATCH、comments (editor)；review、promote-to-curated (reviewer) | Candidate → Chunk → Document.project_id |
| CleanedDocumentVersion `/api/cleaned-versions/{vid}` | GET (viewer) | — | Version → Document.project_id |

## 请求体引用校验

创建/动作端点的外键引用（parser/chunk/export profile、prompt template、model
config、curated item、dataset、benchmark、chunk）必须属于同一项目；跨项目引用
统一 **404**，且数据库无部分写入、后台任务不派发。

## WebSocket `/ws/projects/{pid}/tasks`

- accept 与 Redis subscribe 前完成：access token（T01）→ 启用用户 → 项目
  viewer 成员关系。
- 关闭码：认证失败 / refresh token / 停用用户 -> **4401**；非成员 / 角色不足 /
  项目不存在 -> **4403**。
- 广播前逐一复核成员与启用状态；撤权/停用的连接先关闭（4403）再发送。
- query token 仅作浏览器 WebSocket 兼容；代理、应用 access log、错误与指标
  标签中对 query 参数脱敏。

## PDF `/api/projects/{pid}/documents/{did}/file`

- 仅接受 `Authorization: Bearer access-token`；query token 一律拒绝（401）。
- did 必须属于 pid，调用者至少 viewer；校验完成前不访问 MinIO。
- 响应 `Cache-Control: private, no-store`；日志不得打印 Authorization 或文件内容。

## 后台任务

parse/clean/chunk/generate/export worker 在执行外部 IO 或写数据前，以 API 授权
的 `task.project_id`（或传入 project_id）为锚点复核目标资源与配置引用同项目；
不一致抛 `ProjectChainError`，任务标记失败，不产生部分写入。

## 并发线性化

成员撤销与写请求竞争时以事务提交顺序线性化；撤销提交后的新请求全部 403，
不产生跨项目或无成员写入。WS 已连接后成员被移除，再发布事件：客户端收到
关闭（4403）而非事件。
