# 用户工作流全面 Review（2026-09-12）

评审基线：`03ebbdd`（评审开始时工作区干净）。本次仅新增评审文档和中文开发日志，不修改业务代码，不提交 Git。

## 结论与范围

确认 **21 项业务问题**，其中 **16 项 P1、5 项 P2**；另发现一项 Windows 质量检查脚本问题。主要风险集中在清洗内容丢失、后台任务无法恢复、导出内容或溯源错误。现有单元/集成测试通过，不能替代这些跨步骤、异步时序和多版本场景的验收。

- P1：优先修复。可能损坏结果、丢失编辑、破坏项目隔离，或阻塞已有的核心使用流程。
- P2：随后修复。特定入口、配置或角色组合不可用，存在其他操作路径。
- “运行复现”指执行当前真实函数、组件或 hook，使用内存数据库或 fake 数据库/HTTP 隔离外部 I/O；不是全部经浏览器和真实服务完成的端到端验证。
- “代码链路确认”指已核对前端入口、请求、后端约束及状态变化，但未完整执行该场景。

检查范围覆盖 README/启动与种子配置、认证和项目权限、上传/解析、清洗/审核/合并、分块版本、任务领取/取消/重试/回收、LLM 生成及模板试跑、候选提升、知识审批、数据集/评测集编组和导出、前端状态与 API 契约。

本次未调用真实付费 LLM/远程解析服务，未对开发库执行破坏性操作，未完整运行真实浏览器端到端流程。Docker 中本地模型的可用性、真实网关兼容性仍需要修复后的专门验收；未将其当作本次已复现问题。

## 验证记录

- 全部代码执行前激活 `conda activate DatasetGen`；Python 3.11.15、Node.js 22.18.0。
- 后端 Ruff：通过。
- 后端全量 pytest：**422 项通过**，用时 917.98 秒；覆盖率 **65.13%**，达到 60% 门槛，后端质量检查脚本完整通过。
- 前端 ESLint、TypeScript：通过。
- 前端 Vitest：**17 个文件、95 项测试通过**。
- Next.js production build：通过。
- `npm run api:check`：通过，已提交 OpenAPI 与生成的 TypeScript 一致，未发现禁止的旧合同模式。
- 定向复现包括：认证初始化竞态、租约心跳未启动、设置表单丢参数、清空内容被恢复、ORM 缓存导致版本复核失效、批量领取的等待时间消耗 lease、多 ParseJob 导出异常、导出 ChunkSet 来源错配、跨 Chunk 证据来源错配、默认导出空内容、模板试跑重复 `/v1`、默认解析配置被拒绝、项目非成员读取成功。

本次全量测试的模块覆盖率还显示：`clean_worker.py` 为 0%、`parse_worker.py` 为 37%、`prompt_template_service.py` 为 15%。上述定向内存复现不计入这次 pytest 覆盖率；这些数字也说明总覆盖率达标仍可能漏掉真实工作流程。

原始质量检查日志位于 `logs/review-backend-20260912.log`、`logs/review-frontend-20260912.log`、`logs/review-vitest-20260912.log`、`logs/review-build-20260912.log`。日志目录属于本地临时产物。

## 问题索引

| 编号 | 优先级 | 用户看到的问题 | 证据方式 |
|---|---|---|---|
| R01 | P1 | 登录后刷新/打开深链接，又被跳回登录 | 真实 React 组件运行 |
| R02 | P1 | 非成员可以读取其他项目的详情和成员 | ASGI 路由 + fake DB |
| R03 | P1 | 新安装生成的远程/服务型解析配置直接被拒绝 | 真实冻结函数运行 |
| R04 | P1 | 本地 MinerU 无法通过现有配置校验 | 真实冻结函数运行 |
| R05 | P1 | 清洗编辑超过 5 分钟后失去保存权限 | 真实 hook 运行 |
| R06 | P1 | “保存并继续”保存失败后仍跳转，丢草稿 | 代码链路确认 |
| R07 | P1 | 编辑后立即提交审核，提交的是旧正文 | 代码链路确认 |
| R08 | P1 | 已清空的章节在合并时恢复原文 | 真实合并函数运行 |
| R09 | P1 | 取消清洗后无法重新开始，失败解析仍显示等待 | 代码链路确认 |
| R10 | P1 | 排在后面的任务未开始执行就 lease 过期 | 真实 runner + fake 时钟 |
| R11 | P1 | runner 崩溃回收后切分状态卡住 | 回收分支核对 + 分类函数运行 |
| R12 | P1 | 接受新清洗版本后，旧来源分块仍能发布 | 真实 ORM 双 Session 运行 |
| R13 | P2 | 批量生成的“已选 chunks”列表总是空 | 前后端参数约束核对 |
| R14 | P2 | 批量生成结束后无法从原弹窗新建批次 | 代码链路确认 |
| R15 | P2 | 正常模型配置在模板试跑时请求错误 URL | 真实试跑函数 + fake HTTP |
| R16 | P1 | 候选提升后跨 Chunk 证据的页码/文档错配 | 真实服务函数运行 |
| R17 | P1 | 同一文档解析两次后，引用它的导出失败 | 真实 manifest 函数运行 |
| R18 | P1 | 切换活动分块后，旧条目导出来源变成新版本 | 真实 manifest 函数运行 |
| R19 | P1 | 默认知识抽取结果成功导出为空训练样本 | 真实格式化函数运行 |
| R20 | P2 | 有项目权限的用户看不到操作按钮 | 前后端角色规则核对 |
| R21 | P2 | 设置保存成功但参数被忽略/重置，任务策略不生效 | 真实 RTL 表单 + 后端调用链核对 |

## 修复状态（2026-09-12）

R01–R21 与 Windows 前端门禁问题均已完成修复并通过整合验收。新增 E2E 发现的页码数组响应合同问题也已修复：解析器写入的 `source_pages` 现在可作为数组或兼容旧对象序列化，避免清洗章节列表返回 500。

最终验证：后端 507 项测试通过、覆盖率 67.20%；前端 142 项测试、ESLint、TypeScript、API 契约检查和生产构建通过；迁移往返通过。隔离浏览器工作流完成两份 PDF、两次解析、清洗、版本化分块、生成、跨文档证据、审批、编组和导出深度验证，manifest 保留固定来源。

## 详细发现与修复方案

### R01 · P1 · 刷新已登录页面被误判为未登录

位置：[`auth-context.tsx`](../../apps/web/src/contexts/auth-context.tsx) 第 108 行；[`Dashboard layout`](../../apps/web/src/app/(dashboard)/layout.tsx) 第 26 行。

**触发：** 已有有效 token，刷新项目详情或打开文档深链接，`/auth/me` 尚未响应。

**原因与影响：** 初始化 microtask 提前将状态设为 `authenticated`，使 `loading=false`，而 `user` 仍为 null。Dashboard 因此执行 `router.push('/login')`。随后用户信息恢复，也不会自动返回原工作页面。

**运行证据：** 真实 AuthProvider 在 `/me` resolve 前产生 `status=authenticated, loading=false, user=null, redirect=true`。

**修复：** `/me` 成功前保持 bootstrapping；成功时同时设置 user、token、status。守卫仅在明确 anonymous 时跳转，并保留回跳地址。

**验收：** `/me` 延迟 500ms 时刷新任意深链接，不发生登录跳转；有效 token 刷新失败、用户停用等场景应正确转 anonymous。

### R02 · P1 · 项目详情和成员列表缺少项目成员鉴权

位置：[`routers/projects.py`](../../apps/api/app/routers/projects.py) 的 `get_project`（第 45 行附近）与 `list_members`（第 94 行附近）。

**触发：** 已登录但不属于项目 B 的用户，持有 B 的项目 ID，访问 `GET /api/projects/{B}` 或 `/members`。

**原因与影响：** 两个入口仅依赖 `get_current_user`，未调用 `require_project_member`。服务层也不限制调用者，因此可读项目名称、描述、创建者以及成员 ID/角色。其他资源的集中授权不能补足这两个遗漏入口。

**运行证据：** 以非成员 viewer 调用真实 ASGI 路由，fake DB 仅包含目标项目和另一位成员；两个请求均返回 **200** 及目标对象。

**修复：** 读取入口统一使用项目 viewer 成员校验；保留明确的全局 admin 规则，统一不存在/无权限的响应语义。

**验收：** 项目 A 成员读取 B 的详情和成员应被拒绝；B 成员正常访问。将这两个入口纳入跨项目授权矩阵。

### R03 · P1 · 种子解析配置仍使用已被禁止的旧字段

位置：[`scripts/init_seed.py`](../../scripts/init_seed.py) 第 177、189、195、201 行及 `MINERU_LOCAL_SERVICE_OPTIONS`、`PADDLEOCR_LOCAL_SERVICE_OPTIONS`；[`parse_freeze_service.py`](../../apps/api/app/services/parse_freeze_service.py) 第 56–61 行。

**触发：** 按 README 初始化新环境，选择预置 MinerU API、PaddleOCR API 或对应本地部署服务，发起解析。

**原因与影响：** seed 绕过新 ParserProfileService，直接写入 `base_url/vlm_base_url` 等旧 options。运行时白名单已禁止这些字段，触发即拒绝；只按 README 填 API Token 无法解决。示例 registry 默认也为空。

**运行证据：** 用 seed 的实际字段构造 ParserProfile 调用 `freeze_profile_policy`，MinerU API 与本地服务均报 `ParseFreezeError: unsafe_parser_option`；PyMuPDF 对照通过。

**修复：** seed 与正常 CRUD 共用同一校验，按服务端已注册端点生成 `endpoint_ref` 配置；端点缺失时明确提示“尚未配置”，不制造貌似可用的 profile。同步 README 和旧 profile 迁移步骤，重复 seed 不应重新引入旧字段。

**验收：** 空库迁移→seed→枚举预置配置→冻结解析快照，所有显示可用的配置均通过；缺凭证/端点时显示准确、可操作的提示。

### R04 · P1 · 本地 MinerU 的 endpoint 规则自相矛盾

位置：[`parse_freeze_service.py`](../../apps/api/app/services/parse_freeze_service.py) 第 64、82–84 行；[`config_service.py`](../../apps/api/app/services/config_service.py) 的 `_resolve_endpoint_ref`；[`parsing/snapshot.py`](../../libs/parsing/parsing/snapshot.py) 的 `mineru_local` 白名单。

**触发：** 本机已有 README 所要求的模型文件，使用“MinerU2.5-Pro（本地模型）”。

**原因与影响：** `mineru_local` 未被列入免 endpoint 的本地解析器；冻结及 CRUD 要求它提供 `endpoint_ref`，但它的 options 白名单又不允许该字段。因此普通配置和手工补 endpoint 均不能通过这套流程。

**运行证据：** 使用 seed 的 model_path/device_map/render_dpi 等参数，真实冻结函数报 `invalid_parser_endpoint`；不是模型文件缺失引发的错误。

**修复：** 将无网络的本地模型模式与本地 HTTP 服务模式明确区分；在 CRUD、冻结、worker 校验中一致地允许 `mineru_local` 无 endpoint 执行，继续限制其功能参数。

**验收：** 有效本地模型配置可创建并触发解析；不得接受多余网络/凭证字段；本地 HTTP 服务仍须受 registry 管理。

### R05 · P1 · 清洗租约获取成功后没有启动续租

位置：[`use-cleaning-workbench.ts`](../../apps/web/src/hooks/use-cleaning-workbench.ts) 第 265–271 行；后端 TTL 见 [`section_service.py`](../../apps/api/app/services/section_service.py) 第 12 行。

**触发：** 正常进入章节，异步获取编辑租约后连续编辑超过 300 秒。

**原因与影响：** 心跳 effect 首次执行时没有 lease 而返回；依赖仅包含章节 ID 和稳定 heartbeat，lease 异步返回不会触发 effect。因此没有定时续租，后续保存会因租约失效被拒绝。

**运行证据：** 真实 hook 延迟 acquire 后已有 `lease='lease-a'`，但创建心跳 `setInterval` 的次数为 **0**。

**修复：** 以 lease ID 和章节 ID 驱动定时器生命周期；取得后启动，切换、失去租约或卸载后清理。恢复可见性时复核租约，提供重新获取入口。

**验收：** 延迟 acquire 后推进 30 秒必须发心跳；超过 300 秒仍能保存；切换章节不会对旧 lease 继续续租。

### R06 · P1 · “保存并继续”忽略保存失败，跳转覆盖草稿

位置：[`clean/page.tsx`](../../apps/web/src/app/(dashboard)/projects/[id]/documents/[did]/clean/page.tsx) 第 603–606 行；[`use-cleaning-workbench.ts`](../../apps/web/src/hooks/use-cleaning-workbench.ts) 第 186–228 行。

**触发：** 编辑章节 A，切换 B 时选“保存并继续”；保存请求发生 409、500、断网，或租约已丢失。

**原因与影响：** `save()` 捕获错误后正常 resolve，无 lease 也直接返回。上层无条件执行 `pending()`，加载 B 覆盖 A 的本地草稿，失败提示无法保住编辑。

**修复：** 保存返回明确结果，只有成功保存到具体 revision 才继续；失败时保留原章节、文本、弹窗和冲突信息。

**验收：** 分别模拟成功、409、500、无 lease；只有成功能跳转，其他情况草稿逐字保留。

### R07 · P1 · 快速提交审核会提交旧正文

位置：[`clean/page.tsx`](../../apps/web/src/app/(dashboard)/projects/[id]/documents/[did]/clean/page.tsx) 第 304–309、834 行；[`use-cleaning-workbench.ts`](../../apps/web/src/hooks/use-cleaning-workbench.ts) 第 232–245 行。

**触发：** 修改 Markdown 后，在 1200ms 自动保存防抖结束前立即点击“提交审核”。

**原因与影响：** 提交按钮未受 dirty/saving 约束，submit 直接发送旧 `savedRevision`。服务器提交旧内容；前端随后清除 lease，使尚未发生的自动保存失去条件，新文本可能只停留在只读编辑器中。

**修复：** 提交前串行等待保存完成，使用返回的新 revision 提交；或在 dirty/saving 时明确禁用提交。人工和自动保存应共用单一协调流程。

**验收：** 编辑后立即提交，审核正文包含最后一次修改；保存失败不得发送 submit；自动保存与提交交错时也不能提交旧版本。

### R08 · P1 · 清空章节后合并恢复已删除原文

位置：[`clean_version_service.py`](../../apps/api/app/services/clean_version_service.py) 第 129 行；同类修订记录问题在 [`section_service.py`](../../apps/api/app/services/section_service.py) 第 263 行。

**触发：** 将噪声章节清空，保存 `cleaned_markdown=''`，随后合并清洗版本。

**原因与影响：** `cleaned_markdown if cleaned_markdown else raw_markdown` 将合法空字符串当成未编辑，恢复原文。用户明确删除的目录、页眉或噪声重新流入分块和生成。

**运行证据：** 真实合并函数输入 raw=`TEXT USER DELETED`、cleaned=`''`，输出为 `'TEXT USER DELETED\n'`。

**修复：** 仅对 `None` 回退原文，空字符串表示明确删除。同步检查修订、预览、下载和分块读取是否存在同类 truthiness 回退。

**验收：** 保留一节、清空另一节后合并，被删除文本不得出现；下一次编辑的历史修订也应保留空字符串。

### R09 · P1 · 解析/清洗失败或取消没有同步业务状态

位置：[`task_service.py`](../../apps/api/app/services/task_service.py) 第 265–269 行；[`documents.py`](../../apps/api/app/routers/documents.py) 第 419–435 行；parse/clean worker 的失败路径。

**触发：** 发起清洗，在 Task queued 时取消，再次进入同一解析结果的清洗工作台；或解析/清洗 handler 永久失败。

**原因与影响：** Task 状态更新未同步 ParseJob/CleaningJob。失败时 handler 对 processing 的事务写入回滚，Job 留 queued。清洗重入复用 queued/processing/completed Job 并返回 `task_id=None`，而 cancelled Task 不能人工 retry，形成空工作台无法重新开始的断点。解析列表也可能仍显示等待且禁用删除。

**修复：** 建立 parse/clean 业务终态收敛，覆盖 queued 取消、handler 失败和 reaper；重入只复用确有活跃 Task 或已完成结果的 Job；根据其他有效产物更新 Document 状态。

**验收：** queued/processing 各取消一次后均能重新开始；永久失败的 Job 显示错误并允许正常恢复；Task 与业务 Job 的终态跨会话一致。

### R10 · P1 · 批量领取后串行执行，等待消耗执行租约

位置：[`runner.py`](../../apps/api/app/workers/runner.py) 第 133–139 行；[`queue.py`](../../apps/api/app/workers/queue.py) 第 134–149 行。

**触发：** 同一 runner 领取一个执行 180 秒的解析任务和一个默认 120 秒 lease 的生成任务。

**原因与影响：** 一次 claim 5 个任务，立即全部变 processing 并开始 lease，随后却逐个 await 执行。后排任务的 lease 在实际开始前已经过期。重新计算 deadline 不会重置数据库 lease，checkpoint 仍会拒绝。

**运行证据：** 调用真实 `_claim_and_execute` 并用 fake 时钟模拟前排耗时，第二个任务实际启动时 `lease_expired=True`。

进一步调用真实 `ExecutionContext.checkpoint()`，未来 deadline 配合已经过期的 lease 确实抛出 TaskProtocolError（[`execution.py`](../../apps/api/app/workers/execution.py) 第 123–124 行）；生成 handler 的首次 checkpoint 前没有续租。

**修复：** 单执行槽先将 claim batch 调整为 1；需要并发时仅按空闲槽领取，并立即开始执行与维护租约。

**验收：** 前排耗时超过后排 timeout 时，后排仍获得从实际开始计算的完整预算；混合任务和多 runner 均覆盖。

### R11 · P1 · runner 崩溃后切分业务状态无法收敛

位置：[`queue.py`](../../apps/api/app/workers/queue.py) 第 485–516、542–550 行；[`documents.py`](../../apps/api/app/routers/documents.py) 第 564–581 行。

**触发：** 切分任务被领取后 runner 退出，重启后过期 lease 被 reaper 回收。

**原因与影响：** 首次中断通常没有 error_code，`is_retriable(None)==False`，即使尚有额度也直接 failed。reaper 不经过 handler 的终态 hook，仅更新 Task/Attempt，ChunkSet 留 pending。文档页认为仍在切分，再次触发返回 `409 CHUNK_RUN_IN_PROGRESS`。

任务页人工 retry 仍可能恢复；本项指自动恢复及业务终态收敛失效、文档页新建入口被阻断，不表示数据彻底无法恢复。

**证据边界：** 错误分类函数已运行确认；回收路径与阻断条件经代码核对，未实际杀死运行中的生产 worker。

**修复：** 尚未记录业务错误的 lease 失效按基础设施中断，在额度内重排；最终失败/取消统一使用可在重启后重建的业务收敛注册表，避免依赖原进程闭包。

**验收：** 模拟崩溃并推进 lease，有额度时自动恢复；耗尽后 Task/ChunkSet 同为 failed，文档页可发起新切分。

### R12 · P1 · 切分发布校验读取旧 ORM 缓存

位置：[`chunk_worker.py`](../../apps/api/app/workers/chunk_worker.py) 首次读取第 95 行、发布复核第 171–177 行。

**触发：** worker 读取 active clean v1 后开始切分；另一个 Session 接受 v2；worker 随后加锁复核并发布。

**原因与影响：** 再次 `select(Document).with_for_update()` 不自动刷新 identity map 中已加载字段。缺少 `populate_existing=True`，旧 v1 指针使检查通过，可能将 v1 分块发布为 active，而文档 active 清洗版本已是 v2。

**运行证据：** 使用真实 Document ORM 和内存 SQLite 双 Session，得到 `same_identity=True, check_passes_old=True, actual_db_is_new=True`。该实验验证 ORM 取值行为，不等同于完整 PostgreSQL 并发压力测试。

**修复：** 发布时加锁读取新鲜标量，或使用 populate_existing 刷新，并通过带版本条件的 UPDATE/CAS 发布。

**验收：** 在首次读取与发布之间，用独立 Session 接受 v2，旧切分必须拒绝发布。现有“handler 启动前就切换版本”的用例不能覆盖该时序。

### R13 · P2 · “已选 chunks”使用非法分页参数，列表静默变空

位置：[`batch-generate-dialog.tsx`](../../apps/web/src/components/batch-generate-dialog.tsx) 第 95–99 行；[`documents.py`](../../apps/api/app/routers/documents.py) 第 644 行。

**触发：** 有 ready 分块时打开批量生成，选择“已选 chunks”。

**原因与影响：** 前端固定 `page_size=200`，后端上限 100，返回 422。请求自身 catch 将错误转换为空数组，页面误报没有可选分块。“全部 ready”模式仍可能提交，不能把此项描述成整个批量功能完全不可用。

**修复：** 使用合法分页并支持后续页；失败应展示错误及重试，不能冒充空数据。

**验收：** 基于真实 API 参数校验，有 ready 数据时列表正常；超过 100 条仍可选择后续项；422/500 必须可见。

### R14 · P2 · 批量生成结束后原弹窗不能新建任务

位置：[`batch-generate-dialog.tsx`](../../apps/web/src/components/batch-generate-dialog.tsx) 第 73、205、283 行；[`use-generation-tracking.ts`](../../apps/web/src/hooks/use-generation-tracking.ts) 第 32–58 行。

**触发：** 一次批量任务终结后关闭弹窗，再打开批量生成。

**原因与影响：** track 从未清除，弹窗一直渲染旧任务详情。刷新又从 URL 恢复旧 track，缺少“开始新操作”的入口。此项只针对 UI 无法退出旧跟踪状态；新批次仍应遵循后端 ready 状态及幂等规则。

**修复：** 终态提供“新建生成”，清除 track 与 URL 跟踪参数并重载可用分块；保留历史查看入口。执行中重开应继续跟踪原任务。

**验收：** 完成一次选定部分分块的任务后，能为仍 ready 的其他分块创建新批次；执行中关闭重开不会重复提交。

### R15 · P2 · 模板试跑重复拼接 `/v1`

位置：[`prompt_template_service.py`](../../apps/api/app/services/prompt_template_service.py) 第 153–165 行，关键为第 155 行。

**触发：** 按 README 或预设将模型地址配置为 `https://api.deepseek.com/v1`，点击模板试跑。

**原因与影响：** 试跑另行拼 `/v1/chat/completions`，形成 `/v1/v1/chat/completions`。正式生成使用另一套 LLMClient 地址处理，导致相同配置在两个入口表现不一致。试跑还无条件传未设置的 temperature/max_tokens 为 null。

**运行证据：** fake HTTP 捕获真实试跑函数请求 URL 为 `https://api.deepseek.com/v1/v1/chat/completions`；未向真实供应商发请求。

**修复：** 试跑复用正式 LLMClient 和参数构造；省略未设置的可选参数，统一尾斜杠处理。

**验收：** 所有预设及尾部 `/` 地址产生正确路径；正式生成与试跑使用一致参数；未填写参数不会发送 null。

### R16 · P1 · 候选提升复制了错误 Chunk 的证据来源

位置：[`candidate_service.py`](../../apps/api/app/services/candidate_service.py) 第 283–294 行。

**触发：** 候选来自 Chunk A，审核时添加同项目 Chunk B 的合法 span，再提升为 CuratedItem。

**原因与影响：** span 校验允许 B，但 EvidenceLink 的 document_id、source_pages、heading_path 始终复制候选来源 A。即使同文档，只要跨章节/页码就会错；跨文档还会跳到错误 PDF，错误会进一步进入审批快照和导出。

**运行证据：** B 的 span 校验成功，提升结果 `chunk_id_correct=True, document_id_correct=False`；B 第 10 页被写成 A 第 1 页。

**修复：** 逐个或批量加载 span 的实际 Chunk，从对应 Chunk 派生全部来源字段；提升时复核 span，并考虑补充归属约束。

**验收：** 同文档跨章节及同项目跨文档多个 span，证据文档、页码、章节、文本逐一正确；非法跨项目引用继续拒绝。

### R17 · P1 · 文档多次解析后导出查询抛异常

位置：[`export_manifest.py`](../../apps/api/app/services/export_manifest.py) 第 87–93 行。

**触发：** 同一 PDF 保留两个 ParseJob（正常解析比较功能），将其知识条目加入集合后导出。

**原因与影响：** 按文档查询全部 ParseJob，仅排序却调用 `scalar_one_or_none()`，两条及以上即抛 MultipleResultsFound，导致导出失败。

**运行证据：** 真实函数配合返回多行的查询结果，抛 `Multiple rows were found when one or none was required`；查询无 LIMIT。

**修复：** 沿证据的 `Chunk → Section/固定清洗版本 → CleaningJob → ParseJob` 精确定位。不能只加 LIMIT 1，最新解析未必是证据来源。

**验收：** 同文档两个解析结果分别产生条目，均能导出，并记录各自真实 ParseJob。

### R18 · P1 · 导出把证据的旧分块版本换成当前活动版本

位置：[`export_manifest.py`](../../apps/api/app/services/export_manifest.py) 第 110–124 行。

**触发：** v1 分块生成并批准条目，之后接受 v2 分块，再导出包含旧条目的数据集。

**原因与影响：** manifest 使用 `Document.active_chunk_set_id`，而实际证据 Chunk 属于 v1。导出可能成功，但将 v1 文本与 v2 参数/hash 拼接，破坏可追溯性。

**运行证据：** 实际证据 `chunk_set_id=v1`，真实函数输出 `provenance.chunk_set.version=2`。

**修复：** 先定位证据 Chunk，再使用它的不可变 chunk_set_id 及固定清洗/解析来源；逐层校验归属。与 R17 共用同一来源解析器，避免再次出现相似查询。

**验收：** 切换 active set 前后，旧条目的来源节点和版本不变；新条目使用 v2；整个 manifest 的证据链内部一致。

### R19 · P1 · 默认导出格式会静默生成空训练内容

位置：[`export_worker.py`](../../apps/api/app/workers/export_worker.py) 第 67–79 行；默认模板与格式见 [`init_seed.py`](../../scripts/init_seed.py)。

**触发：** 使用默认知识点抽取模板得到 title/content/key_concepts，正常审批并加入 Dataset，使用默认 sft_jsonl 导出。

**原因与影响：** 格式化器只取 instruction/question、output/answer，缺失时填空。没有按内容类型检查兼容性，因此用户得到成功提示和正常条数，却下载了空样本。benchmark_case 的 reference_answer 也未映射为答案。

**运行证据：** 默认知识结构输出 `{"instruction":"","input":"","output":""}`；默认评测结构保留 question，但 output 为空。

**可达性核对：** 静态追踪确认知识审批、Dataset 添加和冻结门禁均未要求 question/answer，也未限制该 item_type。具有完整生成来源和有效证据的默认知识条目可以到达这个格式化器。

**修复：** 为 item_type 与 export_format 建立明确映射；不兼容内容在创建导出前阻断并列出条目，不能默认空串。知识对象可提供原结构导出，或明确要求先转换为 QA。

**验收：** 三种默认模板 × 每种支持格式的内容级契约测试；必需字段非空且语义一致；不能只断言文件存在、hash 正确或样本数量。

### R20 · P2 · 前端角色判定与后端不一致

位置：数据集、评测集详情页第 80–90 行；[`curated detail`](../../apps/web/src/app/(dashboard)/projects/[id]/curated/[cid]/page.tsx) 第 71–81 行；清洗页第 441 行。后端规则见 [`authz.py`](../../apps/api/app/authz.py) 第 136–169 行。

**触发：** 全局 viewer/editor 被赋予某项目 reviewer/editor，或反向配置。

**原因与影响：** UI 依据全局 `user.role` 隐藏/展示操作，服务端依据 `ProjectMember.role`。用户可能有权限却看不到添加、冻结、批准、分派按钮；或看到点击必然 403 的按钮。

**修复：** 返回当前用户在项目内的有效角色/能力，统一项目 capability hook；全局 admin 的明确绕过规则保持一致。

**验收：** 覆盖全局 viewer+项目 reviewer、全局 reviewer+项目 viewer，以及全局 admin；所有入口与服务器允许操作一致。

### R21 · P2 · 配置表单忽略输入，任务执行也未采用任务策略

位置：[`settings/page.tsx`](../../apps/web/src/app/(dashboard)/projects/[id]/settings/page.tsx) 第 518–521、544–546、580–584、705–711、822–827 行；[`task_service.py`](../../apps/api/app/services/task_service.py) 第 187–188 行。

**触发：** 设置最大并发、重试次数或 JSON 参数并保存；或仅对已有配置改名。

**原因与影响：** GenericConfigTab 输入使用 `max_concurrency/retry_limit`，提交读取 `concurrency_limit/max_retries`；JSON 只存入 config_json 而没有提交映射。隐藏字段又回退默认值，例如 overlap_tokens=50，修改名称也可能重置已有参数。后端 TaskService 取硬编码默认次数/超时，调度器未读取 TaskPolicy.concurrency_limit，因此仅修 UI 仍不能让策略生效。

**运行证据：** RTL 输入并发 1、重试 0、JSON `{"timeout_seconds":999}` 后保存，实际 body 为 `max_retries=3, timeout_seconds=300, concurrency_limit=5`。后端无 TaskPolicy 读取属于代码调用链确认。

**修复：** 按 DTO 拆分配置表单，正确加载已有值，PATCH 仅发修改字段，保留零值；明确 JSON 到 options/template_options 的映射。创建任务时按项目和任务类型解析有效策略并冻结到 Task；调度时真正执行并发限制，定义默认/显式参数的优先级。

**验收：** 保存后重新读取逐项一致；只改名不改其他字段；0 次重试保持 0；不同项目的超时/重试/并发配置对实际任务生效且相互隔离。

## 附加发现：Windows 前端质量检查脚本把警告当失败

位置：[`scripts/test-frontend.ps1`](../../scripts/test-frontend.ps1) 第 32 行。

本次以 `*> logs/review-frontend-20260912.log` 捕获脚本输出时，在 Vitest 的 Vite 配置警告处停止，错误为 NativeCommandError；原因是 PowerShell `ErrorActionPreference=Stop`，重定向后的原生命令 stderr 在检查 exit code 前触发异常。改用本次进程内的 Continue 直接执行同一 Vitest 命令后，95 项全部通过；构建也通过。此结论针对本次带日志重定向的调用，不断言直接裸运行脚本也必然失败。**不能据脚本中断声称测试失败，也不能声称该一键脚本本次完整通过。**

建议参照启动脚本的 Invoke-NativeChecked，在原生命令执行期间正确处理 stderr，以退出码判断成功，再恢复原 ErrorActionPreference；另按 Vite 提示修正配置的模块加载方式。验收同时覆盖“stderr 有警告、exit 0”和“真实非零退出”。

## 建议修复顺序与验收批次

1. **先保护数据正确性和编辑内容：** R06/R07/R08、R12、R16/R17/R18/R19。导出验收必须断言真实问题/答案及来源关系，不能只验证 hash 和对象存在。
2. **修复正常使用与恢复：** R01/R03/R04/R05/R09/R10/R11。重点覆盖异步 acquire、立即提交、取消重入、worker 崩溃、混合任务排队。
3. **补全项目隔离：** R02 优先独立修复；R20 同步统一前后端项目能力判断。
4. **补齐功能与配置：** R13/R14/R15/R21，以及 Windows 门禁脚本。所有默认模板/预设配置纳入契约测试。

建议按这四组拆分可独立评审的修复变更，每项以本文验收条件收口。最后在全新数据库上，以预置账号和配置完成一条真实流程：上传→两次解析→清洗含删除→审核合并→分块→生成→跨 Chunk 证据→审批→编组→导出，并增加断网保存、取消/崩溃恢复、项目角色变化三个场景。

现有用例的主要遗漏是：以即时 mock 替代延迟获取，以手动 heartbeat 替代实际定时器，以单版本 fixture 替代多解析/多分块，以接口返回/文件 hash 替代导出内容语义。这些应作为补测重点。
