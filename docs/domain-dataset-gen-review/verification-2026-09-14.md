# 审查结果复核（2026-09-14）

结论：原报告的 **22 项正式发现均有实质依据，未发现需要删除的明显误报**。其中 20 项是代码问题（CR-019 是被 CR-018 遮蔽的后续缺陷），2 项是已核实的历史 CI 阻塞。问题存在、触发范围与优先级需要分别判断，不能把这些结论理解为所有部署已经发生数据丢失、越权或不可用。

当前 HEAD 为 `019f9ea70fb2325aad068e03cc7637372a9fe273`，与原报告一致。本次对照当前工作区源码，并直接调用项目实现验证关键问题。业务代码未修改；本文件及配套 JSON 是复核产物。

## 验证方式与边界

- Python 代码在 `conda activate DatasetGen` 后运行：Python 3.11.15、SQLAlchemy 2.0.49；WebSocket 验证环境为 FastAPI 0.135.3、Starlette 1.0.0、Uvicorn 0.43.0、websockets 16.0。
- 使用独立、临时的 PostgreSQL 16 容器及项目 ORM 模型建表，直接执行 CandidateService、ConfigService、DocumentService、AuthService、生成 worker 和迁移回填函数。审核和默认配置竞争使用两个真实数据库会话及受控暂停点。未执行完整 Alembic 升级链。
- 文档删除验证使用真实 PostgreSQL 外键，存储操作替换为记录删除结果的内存对象集合；生成验证仅替换 LLM 调用和任务控制 I/O。没有访问业务存储或调用真实模型。
- Node 22.18.0 直接加载当前 `auth.ts`、`api.ts` 的运行逻辑，仅擦除 TypeScript 类型及类型导出。认证竞态使用受控网络替身；响应体超时/取消使用真实本机 HTTP 服务。没有运行浏览器 E2E。
- WebSocket 握手通过当前项目 endpoint 与真实本机 Uvicorn 服务验证，不使用 TestClient 对关闭码的模拟结果。
- 重新读取 GitHub 原始 CI 作业和失败日志，没有重新触发 CI，也没有把历史 CI 失败当作本机安装失败。
- 临时 PostgreSQL 容器已停止并自动删除。没有调用会清空共享测试库的仓库测试 fixture。

正式发现中的 15 项通过原实现的针对性执行观察到问题；CR-019 在显式适配查询结果为映射后验证后续算法；4 项通过源码调用链确认；2 项通过原始 CI 记录确认。原报告附带的人工提取复现脚本没有被当作项目回归测试。

## 22 项逐项结论

下表优先级沿用原报告。源码位置均相对于仓库根目录。

| 编号 | 优先级 | 结论及触发条件 | 本次依据 / 关键位置 |
|---|---|---|---|
| CR-001 | P1 | **成立。** 有 EvidenceLink 引用的文档删除会先删除外部对象，然后数据库拒绝删除。数据库回滚无法撤销已经完成的存储操作。 | 直接执行 `DocumentService.delete_document`，PostgreSQL 抛出 `evidence_links_document_id_fkey` 违反错误，文档行仍在，存储替身中的 PDF/Markdown/JSON 已删除。`apps/api/app/services/document_service.py:116`；`apps/api/app/models/curated.py:111`。 |
| CR-002 | P1 | **成立。** 缺少审核者所见内容版本及编辑/审核/提升之间的并发保护。普通编辑已经清空旧审核字段，问题在过期审核及并发交错。 | 两个真实会话中，审核暂停后编辑提交新内容，随后审核继续；最终新内容为 `approved/supported`。`apps/api/app/services/candidate_service.py:46`、`:131`、`:220`；`apps/api/app/schemas/candidate.py:34`。 |
| CR-003 | P2 | **成立。** 同项目、候选来源批次选择集合之外的 Chunk 可以成为证据；不构成已证实的跨项目越权。 | 实际 `_resolve_span` 接受另一文档/批次的 Chunk，随后真实审核和提升成功。校验只比较项目，没有检查 `source_generation_batch_id/selected_chunk_ids`。`apps/api/app/services/candidate_service.py:73`。 |
| CR-004 | P2 | **成立。** 生成端只解析 JSON，不执行冻结的输出 schema。 | 实际生成 handler 收到 `[]` 后，将数组写入 PostgreSQL Candidate，并把 run 标记为 `completed`；不是仅证明 `json.loads` 能解析数组。`apps/api/app/workers/generate_worker.py:121`；响应的 `dict` 要求见 `apps/api/app/schemas/candidate.py:72`。 |
| CR-005 | P1 | **成立。** 旧请求遇到 401 并等待刷新时切换登录，会清空新会话或使用新身份重发旧操作。 | 加载实际前端模块，失败分支最终 access token 为 `null`；成功分支同一 PATCH body 第二次发送时变成 `Bearer NEW_USER_ACCESS`。`apps/web/src/lib/auth.ts:111`、`:155`；`apps/web/src/lib/api.ts:186`。 |
| CR-006 | P1 | **成立，但可用性严重程度依赖部署。** 超限文件先全量读成 bytes，再判断 200 MiB。 | 路由第 123 行 `await file.read()`，第 124 行才检查大小。接口要求项目 editor 权限；仓库中未找到更早的入口请求体限制。未做大文件/OOM 压测。`apps/api/app/routers/documents.py:117`。 |
| CR-007 | P2 | **成立。** 常规 worker 迁移缺少事件发布，文档详情页没有活动任务轮询。任务完成后页面可能继续显示旧状态。 | 搜索实际调用点，`publish_transition` 只有定义，`_publish_event` 由取消/人工重试调用；文档详情仅在首次加载、动作后及 WS 消息时刷新。`apps/api/app/services/task_service.py:374`；`apps/web/src/app/(dashboard)/projects/[id]/documents/[did]/page.tsx:107`。生成跟踪 hook 的轮询不能替代文档解析/切分路径。 |
| CR-008 | P2 | **成立。** accept 前 close 无法向客户端发送已建立连接上的 4401/4403 关闭帧。 | 实际 endpoint 的本机网络握手返回 HTTP 403，WebSocket 未建立；前端却按私有关闭码区分认证与权限失败。`apps/api/app/ws/task_ws.py:199`；`apps/web/src/lib/ws.ts:76`。未把 TestClient 返回的逻辑关闭码当作浏览器证据。 |
| CR-009 | P2 | **成立。** 广播等待权限校验期间加入的新连接会被旧快照覆盖。 | 调用实际 `ConnectionManager._broadcast/connect` 并暂停权限查询；新连接 accept 成功，但广播结束后不再存在于连接集合。`apps/api/app/ws/task_ws.py:108`，特别是第 120 行。 |
| CR-010 | P2 | **成立。** 超时定时器和组合信号监听器在响应头返回时被清除，未覆盖后续 body 读取。 | 实际 `api.get` 设置 200 ms 超时，仍约 617 ms 成功读完；另一次在 200 ms 主动 abort，仍约 610 ms 成功读完。`apps/web/src/lib/api.ts:70`、`:212`、`:257`。取消传播失效的具体条件是 timeout 与外部 signal 被组合；仅直接传递外部 signal 的路径需区别看待。 |
| CR-011 | P2 | **成立。** single-flight 刷新没有自己的超时/取消边界。 | 实际 `refreshToken()` 的并发调用共享同一 Promise；传给 fetch 的 signal 为空，刷新函数没有注册定时器；网络替身释放前一直 pending。`apps/web/src/lib/auth.ts:111`、`:132`。问题不意味着浏览器底层网络永远没有超时。 |
| CR-012 | P2 | **成立。** 候选 A 的迟到 Chunk 响应可以覆盖当前展开 B 的证据文本和 Chunk ID，列表查询也没有隔离旧响应。 | `handleExpand` 的 then 无条件写入共享状态；`fetchCandidates/fetchExports` cleanup 仅清掉调度计时器。证据添加使用共享 `sourceChunk`，审核 POST 使用当前候选 ID，因此错配有提交路径。`apps/web/src/app/(dashboard)/projects/[id]/candidates/page.tsx:100`、`:246`；未运行 React/浏览器复现。 |
| CR-013 | P2 | **成立，限于存在非空 input 的内容。** messages/ShareGPT 格式丢弃 input 上下文。 | 实际 `render_payload_from_manifest` 包括真实内容校验仍接受含 `UNIQUE_CONTEXT` 的 input，但两种输出均不包含该标记。校验器没有把 input 合入问题。`apps/api/app/workers/export_worker.py:96`、`:123`；`libs/domain/domain/export_content.py:52`。 |
| CR-014 | P2 | **成立。** async 导出 handler 直接执行同步 MinIO I/O，会阻塞同一事件循环的心跳和超时处理。 | 两次 `put_object_versioned` 没有 await/to_thread，内部执行同步网络调用；runner 的心跳 task 与 wait_for 依赖事件循环运行。`apps/api/app/workers/export_worker.py:397`、`:404`；`libs/storage/storage/minio_client.py:59`；`apps/api/app/workers/runner.py:214`。没有据此宣称必然重复发布或突破后续 CAS。 |
| CR-015 | P1 | **成立。** Chunk 页码不是物理 PDF 页码映射，而是正文正则提取结果。 | 实际切分器把正文交叉引用 `Page 888` 变成 `source_pages=[888]`；普通正文得到 `[]`。chunk worker 直接保存结果，没有把 parser 的 page_mapping 接入切分。`libs/splitters/splitters/hybrid_heading.py:187`；`apps/api/app/workers/chunk_worker.py:120`、`:156`。 |
| CR-016 | P2 | **成立。** 提交用户 A 的用户名和用户 B 的邮箱，会让 OR 查询命中两行。 | 直接调用真实 AuthService 和 PostgreSQL 得到 `MultipleResultsFound`；路由只捕获 ValueError。该接口仅管理员可创建用户，不是公开注册攻击面。`apps/api/app/services/auth_service.py:26`；`apps/api/app/routers/auth.py:18`。 |
| CR-017 | P2 | **成立，取决于凭据字符。** 数据库 URL 手工拼接无法正确表示未转义的特殊字符。 | 实际 Settings 用密码 `p@ss` 生成 URL，SQLAlchemy 将密码解析为 `p`，主机解析为 `ss@db.example`。`apps/api/app/config.py:69`。 |
| CR-018 | P1 | **成立，但不是所有非空数据库都失败。** 有待回填的 CuratedRevision/CuratedItem 等相关行时，SQLAlchemy 2.x Row 的字符串下标失败。 | 在真实 PostgreSQL 结果上调用原 `_backfill_curated_revisions`，第 106 行抛出 `TypeError: tuple indices must be integers or slices, not str`。仅有用户/文档、相关回填表为空时不会在这个循环触发。`apps/api/migrations/versions/t09_curated_evidence_approval.py:93`、`:128`、`:181`、`:230`。 |
| CR-019 | P2 | **成立，是被 CR-018 遮蔽的后续缺陷。** NFC 后的位置不能直接作为未规范化 Chunk 原文的字符位置。 | 保持原迁移算法不变，仅把查询结果适配为映射后执行：原文 `e\u0301 X`、quote `X`，回填 `[2,3)`，原文切片却为空格；正确起点应为 3。T06 迁移没有将旧 Chunk 内容统一改成 NFC。`apps/api/migrations/versions/t09_curated_evidence_approval.py:206`。当前正常迁移首先因 CR-018 失败，不能说它已成功写入这些错位坐标。 |
| CR-020 | P2 | **成立。** 非 TaskPolicy 的默认项切换没有统一锁和唯一约束。 | 两个真实 PostgreSQL 会话同时调用实际 `ConfigService.set_default`，最终模型 A、B 都为 `is_default=true`。TaskPolicy 已有专门锁和部分唯一索引，不能把它的保护推及其他配置。`apps/api/app/services/config_service.py:88`；`apps/api/app/models/config.py:11`、`:82`。 |
| CR-021 | P1 | **成立，证据是该提交的真实 CI。** 前端 npm ci 失败，后续检查跳过。 | 重新读取原作业：`EUSAGE`，缺 `@emnapi/core@2.0.0-alpha.3`、`@emnapi/runtime@2.0.0-alpha.3`。本机 Node/npm 与原 Linux CI 不完全一致，本次没有声称本机也已重跑干净安装失败。见下方 CI 来源。 |
| CR-022 | P1 | **成立，限于已观察到的 CI 拉取失败。** 默认 `minio/minio` 镜像三次拉取失败，后端测试没有开始。 | 重新读取原作业：`pull access denied`，容器初始化失败；checkout、迁移、pytest 等 skipped。不能推导为镜像全球永久不可用。本机恰有缓存镜像，也不能用缓存否定冷启动 CI 问题。`.github/workflows/ci.yml:37`。 |

## 需要保留的区别

1. **问题成立不等于修复方案必须全部照搬。** 例如事件缺失不自动要求新增 outbox 框架；先接通必要事件或轮询。上传问题先解决有界读取，不必在此次任务中扩展完整配额系统。是否加入额外机制应遵循项目 AGENTS.md 的最小修复要求。
2. **CR-002 普通顺序操作已有保护。** `update_content` 会将状态改为 human_edited 并清除审核信息；本次确认的缺陷是过期审核和并发窗口。精修 revision 的审批保护也不能替代候选层内容版本绑定。
3. **CR-006 的 P1 需要结合部署评估。** 大文件先读入再拒绝是确定事实，实际资源耗尽程度则受入口限制、内存、并发和可访问用户影响。
4. **CR-018/019 应一起处理。** 先修 Row 读取，随后仍需修原文坐标；空库迁移成功不能验证存量回填正确。
5. **CR-021/022 是交付阻塞，不是应用测试断言失败。** 原 CI 根本没有执行后续测试，覆盖率上传步骤成功也不等于生成了覆盖率报告。

## 原报告的额外疑点

这些条目仍与原来的 22 项正式发现分开记录。

| 原报告位置 | 本次结论 |
|---|---|
| §5.1 API key 与字段名 | `ConfigService` 确实只是把 `api_key` 改名为 `api_key_encrypted`，worker 直接作为 API key 使用。应用层加解密缺失有代码依据，但数据库/磁盘加密及真实部署暴露情况仍不能据此判断。 |
| §5.1 默认凭据、端口 | 设置中的开发默认值及 Compose 未指定 loopback 的端口映射确实存在。报告没有据此认定公网暴露，这个边界正确。 |
| §5.2 mc 初始化命令 | **已补充实机确认。** 本机缓存 `minio/mc:latest` 的 entrypoint 为 `["mc"]`；执行与 CI 相同的 `minio/mc sh -c ...` 结构，直接报 `sh is not a recognized command`、退出码 1。使用 `--network none`，没有访问任何存储。该问题会影响使用此入口的初始化步骤，但不是原 CI 镜像拉取失败的原因。开发 Compose 的 minio-init 已设置 `/bin/sh` entrypoint，不能一并判错。 |
| §5.3 queued 取消后的终态回调 | 源码链路支持该疑点：`cancelTask` 直接写入 cancelled；effect 对终态提前返回；获取 batch 与 `onTerminal` 只在 poll 内。存在直接取消后不加载批次汇总、不调用该回调的路径。消费者 `batch-generate-dialog.tsx:81` 用回调通知父页刷新。未做 React/E2E 复现，不扩大为“所有取消均失败”。 |
| §5.3 切分幂等键未重置 | 源码链路支持该疑点：详情页 ref 在整个挂载期间复用；相同请求摘要返回旧 ChunkSet，换 profile 则可触发 409。页面省略 cleaned_version_id，使摘要中的该字段一直为 null；另一个用户更新 active 清洗版本、当前页保持挂载并再次切分时，也有复用旧任务的路径。见详情页 `:163`、documents 路由 `:572`、chunk_set_service `:164`。未做浏览器复现。 |

## 证据文件与 CI 来源

本次结构化观测结果见 [verification-results-2026-09-14.json](verification-results-2026-09-14.json)。结果中的 synthetic token、域名、内容和存储集合均为测试值。CR-019 的 `row_adapter_used=true` 用于明确区分后续算法验证与未修改代码的正常迁移执行。

原 [CI run 34741941555](https://github.com/aL0ng-z/domain-dataset-gen/actions/runs/34741941555) 对应同一 HEAD；[前端作业](https://github.com/aL0ng-z/domain-dataset-gen/actions/runs/34741941555/job/103682995323) 与 [后端作业](https://github.com/aL0ng-z/domain-dataset-gen/actions/runs/34741941555/job/103682995231) 的状态、失败点和日志已通过 `gh run view --json`、`gh run view --log-failed` 重新核对，失败发生于 2026-09-13 UTC。

本次目标是确认审查结论，不是验收修复。因此没有运行与上述问题无关的全套测试、构建或压力测试，也没有修改原报告的编号与优先级。
