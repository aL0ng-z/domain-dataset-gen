# T02：项目资源对象级授权

- 状态：待实施
- 优先级：P0-Security
- 建议规模：L
- 直接依赖：T00、T01
- 后续依赖本卡：T05、T07、T09、T10
- 可并行：完成设计后可按路由族拆分实现，但必须一次性启用安全门禁

## 1. 目标与背景

当前 require_project_member 只证明调用者属于 URL 中的 pid，随后多数服务仍按 did、cid、bid、tid 等裸
UUID 查询。攻击者可组合“自己的 pid + 其他项目的资源 ID”读取或修改对象。sections、chunks、
candidates、cleaned-versions 等平铺路由只要求登录；PDF 下载和任务 WebSocket 也没有项目成员校验。

本卡建立统一的对象级授权：每次读取、修改、删除、派发任务和订阅事件，都必须同时证明用户可访问目标
项目、资源真实属于该项目且角色满足操作要求。任何客户端提供的父子 ID 都不是授权事实。

## 2. 范围

1. 建立集中式项目成员校验与 project-scoped resource resolver，替换路由内裸 UUID 查询。
2. 覆盖所有带 pid 的嵌套路由，强制校验 URL 项目与目标对象归属一致。
3. 覆盖 sections、chunks、candidates、cleaned-versions 等平铺路由，通过数据库关系反查项目。
4. 覆盖 Document、ParseJob、CleaningJob、Section、CleanedDocumentVersion、ChunkSet、Chunk、
   GenerationRun、Candidate、评论、CuratedItem、Dataset、Benchmark、Export、Task 与配置对象。
5. 校验请求体内引用的 profile、template、model、dataset、benchmark、curated item 等也属于同一项目。
6. PDF 下载必须通过统一 access-token 和项目 viewer 授权，并移除 query token。
7. WebSocket 在 accept 和 Redis subscribe 之前完成 access-token、启用用户及项目 viewer 校验。
8. WebSocket 在成员被移除或用户被停用后，不得继续收到新的项目事件。
9. 后台任务在执行外部 IO 或写数据前复核 task、目标资源及配置的项目链一致。
10. 用参数化双项目、四角色测试覆盖所有资源路由与动作。

## 3. 明确不做

- 不改变 admin、reviewer、editor、viewer 的既定层级或各业务动作的最低角色。
- 不修复 Candidate/CuratedItem 的审批与证据门禁；这些属于 T09。
- 不修复 Dataset/Benchmark 分页和编组业务规则；这些属于 T04、T10。
- 不引入 PostgreSQL Row Level Security、ABAC 策略引擎或跨项目共享资源。
- 不把所有平铺路由改成新 URL；本卡保持现有外部路径兼容。
- 不实现 WebSocket 一次性 ticket、Cookie 认证或完整会话撤销。
- 不以隐藏 UI 按钮代替服务端授权，也不依赖前端传回 project_id 证明归属。
- 不顺带修复任务状态机、清洗 lease 或导出不可变性。

## 4. 数据库合同

- 预计不新增业务表或列；现有外键是资源归属事实源。
- 归属链固定如下，resolver 必须用 SQL join 或等价 EXISTS 校验，不得先查对象再信任 pid：

| 资源 | 项目归属路径 |
|---|---|
| Document | Document.project_id |
| ParseJob、CleaningJob、Section、CleanedDocumentVersion、ChunkSet、Chunk | 资源 → Document.project_id |
| GenerationRun、Candidate、CandidateComment | 资源 → Chunk → Document.project_id |
| Parser/Chunk/Model/Export Profile、TaskPolicy、PromptTemplate | 资源.project_id |
| PromptTemplateVersion | Version → PromptTemplate.project_id |
| CuratedItem、Dataset、Benchmark、Export、Task、LlmUsageLog | 资源.project_id |
| CuratedRevision、EvidenceLink | 资源 → CuratedItem.project_id |
| DatasetItem、BenchmarkCase | 资源 → Dataset/Benchmark.project_id |
| SnapshotManifest | Manifest → Export.project_id；禁止按 manifest 裸 ID 暴露 |

- 冗余外键同时存在时必须全部一致，例如 Chunk.document_id 与其 Section.document_id；不一致数据应拒绝
  访问并记录安全告警，不得任选一个归属。
- 所有写操作在同一事务内使用带项目谓词的 SELECT/UPDATE/DELETE；不得在授权后用裸 ID 再写一次。
- 如 EXPLAIN 证明缺少索引，可仅新增项目归属 join 所需索引，并提交可逆 Alembic 迁移。
- 迁移前运行孤儿与跨项目引用审计；发现不一致数据时停止自动修复并输出 ID 清单。

## 5. API 合同

### 5.1 状态码

| 条件 | REST/PDF | WebSocket |
|---|---|---|
| 无 token、无效 token、refresh token | 401 | accept 前关闭 4401 |
| 项目存在且用户是成员，但角色不足 | 403 | accept 前关闭 4403 |
| 资源不存在，或资源不属于 URL 中 pid | 404 | 不适用 |
| 非成员访问项目入口 | 403；保持现有项目成员语义 | accept 前关闭 4403 |
| 同项目父子 ID 组合不成立 | 404；不得泄露哪一个 ID 存在 | 不适用 |

- 全局 admin 可绕过成员与最低项目角色检查，但不能绕过 pid 与资源真实归属绑定。
- 列表查询必须在 SQL 层按已授权项目过滤，禁止取回后在 Python 中过滤。
- 创建与动作端点中的所有外键引用都要 scoped load；跨项目引用统一表现为 404。
- 同项目但业务状态不允许的组合继续使用 409 或 422，不得与授权错误混淆。
- 服务层方法优先接收已 scoped-load 的实体，或显式接收 pid 并把 pid 写入查询条件。
- OpenAPI 为 401、403、404 给出统一响应说明，不暴露跨项目对象存在性。

### 5.2 PDF

- GET /api/projects/{pid}/documents/{did}/file 仅接受 Authorization: Bearer access-token。
- did 必须属于 pid，调用者至少为 viewer；校验完成前不得访问 MinIO。
- 移除 token query 参数，响应保持 application/pdf 和原文件名语义。
- 响应使用 Cache-Control: private, no-store；任何日志不得打印 Authorization 或文件内容。

### 5.3 WebSocket

- /ws/projects/{pid}/tasks 可暂时保留 query 中的 access token，以兼容浏览器 WebSocket API。
- token 必须符合 T01；随后查启用用户和 ProjectMember，至少为 viewer，最后才 accept/subscribe。
- 连接记录 user_id 与 project_id；每次广播前复核用户启用状态和成员关系，撤权后先关闭再发送。
- 非法连接不得创建 Redis pubsub 订阅；断开后必须回收连接和最后一个订阅任务。
- query 参数必须从代理、应用 access log、错误与指标标签中脱敏。

## 6. 前端合同

- 页面路由中的 projectId 仅用于构造 URL，不得被视为资源归属证明。
- 统一处理 401、403、404：401 进入 T01 刷新流程；403 展示权限不足；404 展示资源不存在或不可访问。
- PDF 通过 apiFetch 携带 Authorization 下载 Blob，再创建 object URL 给 iframe；切换文档和卸载时 revoke。
- PDF 请求失败时不得回退为带 token 的 query URL。
- WebSocket 只使用 T01 提供的当前 access token；4401 走认证恢复，4403 停止重连并提示无项目权限。
- viewer/editor/reviewer 的按钮可按能力隐藏或禁用，但服务端仍是唯一授权门禁。
- 不为跨项目 404 展示对象名称、原项目或“你无权访问项目 X”等旁路信息。

## 7. 预期修改面

- apps/api/app/dependencies.py 及新建的项目资源授权/resolver 模块
- apps/api/app/routers/ 下所有项目资源路由
- apps/api/app/services/ 中按裸 ID 读取或修改资源的方法
- apps/api/app/ws/task_ws.py
- apps/api/app/workers/ 中消费多个资源 ID 的入口校验
- apps/web/src/lib/api.ts、apps/web/src/lib/ws.ts
- 文档清洗页的 PDF Blob 加载与释放逻辑
- tests/integration/authorization/、WebSocket/PDF 集成测试及资源工厂
- 实施完成时更新 OpenAPI、权限矩阵和 docs/logs/dev-log.md

## 8. 依赖

- T00 提供双项目、四角色、完整资源链、MinIO fake、Redis pubsub 与 ASGI/WS 测试底座。
- T01 提供严格 access-token 校验及前端 401 状态机。
- T04 后续可生成前端错误类型，但不阻塞本卡服务端安全修复。
- T09、T10 可收紧业务门禁，但不得削弱本卡的项目归属约束。

## 9. 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| 遗漏冷门或平铺路由 | 自动枚举 OpenAPI 路由，并维护资源动作参数化测试表 |
| admin bypass 同时绕过资源归属 | 将“成员/角色”和“对象属于 pid”拆成两个不可跳过的判断 |
| 授权检查与写入存在 TOCTOU | 同事务、带项目谓词的写查询；提交后再派发后台任务 |
| 多级 join 引发性能退化 | 使用 EXISTS/索引并记录 EXPLAIN；不以放松校验换性能 |
| PDF Blob 未释放造成内存泄漏 | 切换、失败、卸载时统一 revokeObjectURL |
| 已连接 WS 在撤权后继续收消息 | 每次广播前复核，测试撤权与发布并发顺序 |
| 404 语义破坏旧前端提示 | 前端统一不可见/不存在提示，并增加合同测试 |

## 10. 实施步骤

1. 生成“路由—资源—归属链—最低角色—动作”清单并锁定为测试参数。
2. 实现项目成员依赖与 typed scoped resolver，统一 401/403/404。
3. 先改所有嵌套路由，确保 pid 进入每一个服务查询和父子关系查询。
4. 改造平铺路由，通过资源链解析 project_id 并校验成员与角色。
5. 校验请求体引用和后台任务入口的同项目约束。
6. 改造 PDF 为统一依赖加 scoped document 查询，前端改用 Blob。
7. 改造 WS 握手、订阅前校验、广播前复核和清理。
8. 执行孤儿/归属不一致审计，必要时只增加性能索引。
9. 完成双项目负向矩阵、并发撤权测试、OpenAPI 与中文开发日志。

## 11. 自动化验收标准

在仓库根目录执行：

~~~powershell
conda activate DatasetGen
python -m pytest -q tests/integration/authorization tests/integration/test_pdf_authorization.py tests/integration/test_task_websocket_authorization.py
python -m ruff check apps/api/app/dependencies.py apps/api/app/routers apps/api/app/services apps/api/app/ws tests/integration/authorization
~~~

在前端目录执行：

~~~powershell
cd apps/web
npm test -- --run src/lib/ws.test.ts src/app/pdf-authorization.test.tsx
npm run lint
npm exec tsc -- --noEmit
~~~

必须满足：

1. 对每种资源，项目 A 成员用 pid=A 与项目 B 的对象 ID 执行 GET/PATCH/DELETE/action 均为 404。
2. 平铺 sections/chunks/candidates/cleaned-versions 路由对项目 B 非成员返回 403 或 404 的既定语义，
   且响应、耗时断言与日志均不泄露对象内容和所属项目。
3. viewer 对读操作成功、对写操作 403；editor/reviewer/admin 按既定矩阵通过，未登录一律 401。
4. admin 使用错误 pid 访问真实资源仍为 404；不能因全局角色绕过路径绑定。
5. 跨项目 parser profile、prompt/model config、curated item、dataset/benchmark 引用在写入前被拒绝，
   数据库无部分写入且后台任务未派发。
6. PDF 在授权前不调用 storage fake；query token 被拒绝，正确 Bearer + 同项目 viewer 才返回 PDF。
7. WS 非成员、refresh token 和停用用户均不能 accept 或订阅 Redis；合法 viewer 只能收到本项目事件。
8. 并发场景：WS 已连接后提交成员移除，再发布事件，客户端不得收到该事件并被关闭。
9. 并发场景：成员撤销与写请求竞争时，以事务提交顺序线性化；撤销提交后的新请求全部 403，
   不产生跨项目或无成员写入。
10. OpenAPI 中每个项目资源操作都关联统一认证依赖；静态检查不存在路由内裸 ID 写操作白名单外实例。

## 12. 停止条件

出现以下任一情况时停止并请求设计或数据治理决策：

- 某类资源无法通过现有外键唯一推导 project_id，或同一对象合法属于多个项目；
- 归属审计发现存量跨项目引用，且无法判定正确项目或删除会造成数据损失；
- 产品要求新增跨项目共享、复制、公开链接或匿名 PDF/WS 访问；
- 既定角色矩阵与现有路由行为冲突，且会改变 reviewer/editor 的产品职责；
- 必须改变公开 URL 或引入 RLS 才能可靠关闭漏洞；
- WS 基础设施无法在广播前复核权限，且业务不接受撤权后连接继续存活的窗口。

## 13. 审查重点

- 每个资源查询是否同时包含资源 ID 与可信项目归属条件。
- admin 是否只绕过成员/角色，而没有绕过对象与 URL 项目的绑定。
- 列表、子资源、删除、导出、重试等非典型动作是否也在矩阵中。
- 请求体引用和后台 worker 是否执行同项目校验，而不只检查顶层路径。
- PDF 是否在 MinIO 调用前授权，前端是否彻底删除 query token。
- WS 是否在 accept/subscribe 前授权，并在撤权后阻止下一条消息。
- 负向测试是否使用两套真实完整资源链，而非随机不存在 UUID。

## 14. 完成定义

- 所有自动化验收命令返回 0，双项目、四角色、REST/PDF/WS 矩阵全部通过。
- 任意“自己的 pid + 他项目资源 ID”组合均不能读取、修改、删除、派发任务或订阅事件。
- 所有平铺路由均能从数据库关系得到唯一项目并执行授权。
- PDF 不再接受 query token，WebSocket 非法连接不会创建 Redis 订阅。
- 存量数据归属审计已通过或已按停止条件形成单独治理决策。
- 权限矩阵、OpenAPI 和 docs/logs/dev-log.md 已同步更新。
