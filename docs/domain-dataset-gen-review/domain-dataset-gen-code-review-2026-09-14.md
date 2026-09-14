# domain-dataset-gen 深度 Code Review 报告

**审查日期：2026-09-14**  
**仓库：aL0ng-z/domain-dataset-gen**  
**审查基线：master / `019f9ea70fb2325aad068e03cc7637372a9fe273`**  
**提交时间：2026-09-12 15:09:38 UTC**  
**性质：只读审查；未修改仓库、未创建提交或 Issue。**

> 结论：当前基线不宜直接作为可稳定交付、可验证来源的数据生产版本。优先修复构建门禁、文档删除一致性、会话身份边界、候选审核版本绑定、存量迁移和页码追溯，再评估扩大使用范围。这个结论是代码审查意见，不是对线上事故或已遭攻击的断言。

## 1. 执行摘要

本次整理 **22 项独立缺陷 / 交付阻断问题：P1 8 项，P2 14 项**。同一异步状态缺陷在多个页面的重复表现已合并；安全加固建议及证据不足的疑点不计入这 22 项。

证据分为：**A：既有真实 CI 记录验证 2 项；B：源码链路 + 本地机制级复现 15 项；C：源码及调用合同确认、尚未集成复现 5 项**。本次执行 16 个局部机制用例（Python 11 个、Node 5 个），均观察到了预期的缺陷现象；CR-005 有两个独立交错用例，因此 16 个用例对应 15 项发现。**这不是“整仓 16 个测试通过”，也不是测试覆盖率。**

审查并非只看代码风格。重点沿“上传 → 解析 → 清洗版本 → 切分 → 生成 → 候选审核 → 精修审批 → 编排 / 导出”追踪数据、权限、状态和对象存储边界，同时检查前端认证恢复、异步响应归属、实时刷新以及 CI / 迁移可用性。

最需要先处理的是三类问题：其一，失败请求可能已经破坏外部文件；其二，审核 / 请求重放没有绑定正确的数据版本或用户会话；其三，来源页码和生成内容即使被封存并正确计算 hash，也可能在封存前就已失真。已有 immutable snapshot 机制值得保留，但不能替代输入语义和上游一致性验证。

### 1.1 已核实的 CI 状态

[该提交的 CI run][CI-R] 在 **2026-09-13** 结束为 failure。前端在 npm ci 阶段失败，后端在服务容器初始化阶段失败；两边主要质量门禁都没有进入执行。上传覆盖率 artifact 的步骤显示 success，不意味着生成了覆盖率报告：日志明确提示没有找到报告文件。[CI-F][CI-B]

| 作业 | 实际失败点 | 后续未执行内容 | 本报告编号 |
|---|---|---|---|
| frontend | npm ci：lockfile 缺项 | lint、tsc、API 合同检查、Vitest、build | CR-021 |
| backend | MinIO 镜像拉取失败 | checkout 后的安装、lint、迁移、pytest | CR-022 |

## 2. 方法、覆盖范围与限制

所有仓库源码链接固定到上述 commit。通过 GitHub 连接器读取目录、源码、相关设计合同、迁移和真实 CI 作业日志，复核疑点；没有根据文件名或旧整改文档直接认定现行代码仍有缺陷。对会变化的框架协议还查阅了官方资料，例如 WebSocket 握手拒绝、SQLAlchemy Row 接口和 GitHub Actions 语法。

| 范围 | 本次实际审查重点 | 边界 |
|---|---|---|
| 前端基础设施 | auth / api / ws、认证与 WS context | 静态审查 + Node 机制复现；非真实浏览器 E2E |
| 前端业务 | 清洗 hook、生成跟踪 hook、候选审核、文档详情、导出历史 | 关键请求 / 状态路径；未逐页验收所有 UI 与组件 |
| 后端 HTTP / 权限 | JWT、当前用户、项目资源解析、上传 / 删除、候选路由 | 核心路径；不是全路由渗透测试 |
| 异步任务 | runner、queue、ExecutionContext、生成 / 切分 / 导出 worker | 取消、租约、发布与事件联动；未搭建真实多 worker 集群 |
| 数据功能 | 模板快照、生成内容、证据、精修版本、导出映射与 composition policy | 关键链路；未完整遍历 dataset / benchmark 所有 CRUD |
| PDF / 清洗 / 切分 | PyMuPDF 适配器、清洗合并、heading 切分与页码链 | 未执行真实 PDF 或外部解析服务；其他解析器适配器不是逐行覆盖 |
| 存储 / 迁移 / CI | MinIO 客户端、关键模型和 T09 回填、Compose、CI 实际日志 | 未运行真实 MinIO、Alembic 全链、镜像构建或依赖漏洞扫描 |

**环境限制与影响：** 运行环境无法直接联网克隆仓库，因此未在原仓库 checkout 中执行 `pytest`、`npm ci`、`npm test`、`npm run build` 或完整 Alembic 迁移。GitHub 源码和既有 CI 日志仍可读取。本地复现使用人工提取 / 规范化的相关逻辑、隔离的 SQLAlchemy / SQLite / 临时文件，以及本地 HTTP / WebSocket 网络服务，不访问线上数据，也不消耗真实模型额度。

本地 Python 是 **3.13.5**，SQLAlchemy **2.0.50**，FastAPI **0.128.2**，Starlette **0.50.0**，Uvicorn **0.48.0**，websockets **16.0**；Node 为 **22.16.0**。这与仓库目标 Python 3.11 / CI Node 22.23.2 的完整锁定环境不等同。故 B 级证据用于验证错误机制，不能替代项目依赖环境的集成回归。

### 2.1 优先级与证据等级

P1 表示应在发布 / 扩大使用前优先解决的数据一致性、身份归属、关键来源可信性或交付阻断；P2 表示明确的功能、并发、鲁棒性和配置缺陷。这里是修复优先级，不是 CVSS 分数；未确认可直接远程接管等 P0 级事件。

A 为真实既有 CI 日志；B 为源码 + 本地机制验证；C 为源码 / 合同 / 调用路径确认。C 项仍应按给出的回归场景在项目真实环境中验证，不应被表述为已经发生过的生产事故。

## 3. 问题总表

| 编号 | 优先级 | 证据 | 模块 | 问题 |
|---|---|---|---|---|
| [CR-001](#cr-001) | P1 | B | 后端 / 数据安全 | 文档删除先破坏对象存储，再因外键失败回滚数据库 |
| [CR-002](#cr-002) | P1 | C | 后端 / 审核一致性 | 候选编辑、审核和提升未绑定同一个内容版本 |
| [CR-003](#cr-003) | P2 | C | 后端 / 证据追溯 | 证据校验只检查项目，没有检查候选来源批次 |
| [CR-004](#cr-004) | P2 | B | 功能 / LLM 生成 | 生成成功门禁只验证 JSON 语法，未验证冻结输出结构 |
| [CR-005](#cr-005) | P1 | B | 前端 / 身份边界 | 旧刷新请求可清空新会话，或把旧修改请求用新用户身份重发 |
| [CR-006](#cr-006) | P1 | C | 后端 / 可用性 | 上传大小限制在完整读入内存之后才执行 |
| [CR-007](#cr-007) | P2 | C | 全链路 / 任务可观测性 | worker 状态迁移未接入事件发布，文档页可能一直显示旧状态 |
| [CR-008](#cr-008) | P2 | B | 前后端 / 认证协议 | WebSocket 握手前关闭无法向浏览器传递预期的 4401 / 4403 |
| [CR-009](#cr-009) | P2 | B | 后端 / 实时事件 | WebSocket 广播覆盖连接快照，丢失并发新连接 |
| [CR-010](#cr-010) | P2 | B | 前端 / 请求生命周期 | HTTP 超时及组合取消信号在响应头到达时被过早清理 |
| [CR-011](#cr-011) | P2 | B | 前端 / 认证恢复 | 刷新令牌没有独立截止时间，会阻塞整个 single-flight 队列 |
| [CR-012](#cr-012) | P2 | B | 前端 / 审核工作台 | 候选切换和列表查询缺少旧响应隔离，可能显示并提交错误来源 |
| [CR-013](#cr-013) | P2 | B | 功能 / 数据集导出 | messages / ShareGPT 导出静默丢弃 input 上下文 |
| [CR-014](#cr-014) | P2 | B | 后端 / 任务可靠性 | 导出 worker 直接执行同步 MinIO 调用，阻塞心跳与超时处理 |
| [CR-015](#cr-015) | P1 | B | 功能 / 来源追溯 | Chunk 来源页码由正文正则猜测，不能作为可信 PDF 证据 |
| [CR-016](#cr-016) | P2 | B | 后端 / 账户功能 | 注册冲突查询可能命中两名用户并抛出 500 级异常 |
| [CR-017](#cr-017) | P2 | B | 部署 / 运行配置 | 手工拼接数据库 URL 未转义凭据中的特殊字符 |
| [CR-018](#cr-018) | P1 | B | 数据库 / 迁移 | T09 存量回填使用不兼容的 Row 下标，升级有数据的数据库会失败 |
| [CR-019](#cr-019) | P2 | B | 数据库 / Unicode 证据 | T09 证据回填保存 NFC 文本坐标，却继续引用未规范化原文 |
| [CR-020](#cr-020) | P2 | C | 后端 / 配置功能 | 非 TaskPolicy 配置的默认项切换存在并发竞争 |
| [CR-021](#cr-021) | P1 | A | 前端 / 构建与 CI | 前端锁文件不能通过当前 CI 的干净安装，质量门禁全部跳过 |
| [CR-022](#cr-022) | P1 | A | 部署 / 后端 CI | 默认 MinIO 镜像在已观察 CI 环境无法拉取，后端测试根本未启动 |

## 4. 缺陷详情

<a id="cr-001"></a>
### CR-001 · 文档删除先破坏对象存储，再因外键失败回滚数据库

**P1 ｜ 证据 B ｜ 后端 / 数据安全**

**代码定位：** [S01] `DocumentService.delete_document`；[S02] 文档 DELETE；[S03] `EvidenceLink` 外键；[S39] `get_db`。

**问题与影响：** 删除流程先删除解析产物和原始 PDF，再执行数据库删除、flush。精修证据对 `documents` / `chunks` 的引用并不随这一删除任意解除；数据库拒绝删除时，请求事务回滚，但已经完成的 MinIO 删除不会回滚。结果是数据库仍显示文档和证据，原始对象却已不可正常读取。这不是普通的“删除接口报错”，而是跨存储数据一致性破坏。

**触发 / 复现：** 为一个文档生成候选并提升为带 EvidenceLink 的精修条目，再删除文档。局部复现按同样顺序删除临时对象，再触发限制性外键：观察到 `FOREIGN KEY constraint failed`、文档行仍在、对象已不在。完整 PostgreSQL / MinIO 调用尚未执行；有备份或版本历史时可能恢复，因此不宣称任何部署都永久不可恢复。

**修复建议：** 优先禁止删除仍被证据、快照或活动任务引用的文档，返回稳定的 409 业务码；采用软删除 / tombstone。将物理删除转成数据库事务提交后驱动的可重试垃圾回收任务，包含引用复核、审计和精确对象版本处理。不要仅把数据库 DELETE 提前，就认为跨存储事务已解决。

**回归验收：** 有精修引用、活动任务及无引用三类场景；注入 flush / commit / 对象删除失败。所有拒绝删除的请求必须保证原始 PDF 仍可读取；重试清理不得误删仍被快照引用的对象。

**验证状态：** `delete_object_before_fk`：已观察到数据库回滚与外部对象删除不一致；SQLite + 临时文件是机制验证，不是生产存储测试。

<a id="cr-002"></a>
### CR-002 · 候选编辑、审核和提升未绑定同一个内容版本

**P1 ｜ 证据 C ｜ 后端 / 审核一致性**

**代码定位：** [S04] `update_content`、审核、提升路径；[S05] 对应路由；[S06] `CandidateReview` / `CandidateUpdate`；[S39] 会话设置。

**问题与影响：** 候选的读、检查“尚未提升”、修改审核字段和创建精修快照之间没有共享的行锁 / compare-and-swap 版本合同。请求也不携带审核者看到的内容版本。唯一 `candidate_id` 约束只能防止重复精修记录，不能证明批准的是当前内容。精修层已有 revision 和审批绑定，并不补上候选层这个窗口。

**触发 / 复现：** 审核请求读取旧候选后，在证据查询等 await 点暂停；编辑请求先提交新内容并清空旧审核信息；审核请求随后写入 approved 和证据。按默认事务设置，可出现新内容带着对旧内容作出的批准。提升与编辑并发也存在“冻结检查先通过，随后另一个事务提升”的窗口。

**修复建议：** 给 Candidate 引入 revision / 内容哈希，审核和编辑提交 expected_revision；审核记录保存被审核内容的哈希。编辑、审核、提升均对同一 Candidate 行使用统一锁序或 CAS；取得锁后刷新 ORM 状态，再复核状态和提升记录。

**回归验收：** 用两个真实 PostgreSQL 会话和同步栅栏控制交错，而不是只连续调用接口。应保证过期审核返回 409；提升只能消费被批准的确切版本；审核后编辑必须明确失效原批准。

**验证状态：** 源码与默认会话配置确认；没有执行该仓库的双会话 PostgreSQL 竞争测试。

<a id="cr-003"></a>
### CR-003 · 证据校验只检查项目，没有检查候选来源批次

**P2 ｜ 证据 C ｜ 后端 / 证据追溯**

**代码定位：** [S04] `_resolve_span`；[S36] T09 来源批次合同。

**问题与影响：** 代码校验 Chunk 所属项目、字符边界和 quote 一致性，却没有检查该 Chunk 是否属于候选来源批次冻结的选择集合。服务注释和 T09 合同要求拒绝跨批次证据。于是，同项目另一文档或另一历史切分集合中的片段，也可能被挂到当前候选上。此问题不是已证实的跨项目越权，而是同项目内来源边界失效。

**触发 / 复现：** 准备同一项目两个生成批次 A/B，使用只属于 B 的 Chunk 的合法坐标和原文 quote 审核 A 的候选。当前项目检查与 quote 检查均可以通过；预期应在来源集合校验处拒绝。CR-012 的前端竞态会使这个问题更容易被无意触发。

**修复建议：** 以 `source_generation_batch_id` 对应的冻结选择 / provenance 为准核验证据 Chunk，不依据当前 active 指针猜测历史来源。遗留无来源记录使用明确的不可验证策略，不静默放宽到整个项目。

**回归验收：** 同项目不同批次、不同文档、旧 ChunkSet、已被切换 active 的合法历史批次均应分别测试；跨项目仍保持拒绝。

**验证状态：** 源码及仓库合同交叉确认；未对真实 API 发起该审核请求。

<a id="cr-004"></a>
### CR-004 · 生成成功门禁只验证 JSON 语法，未验证冻结输出结构

**P2 ｜ 证据 B ｜ 功能 / LLM 生成**

**代码定位：** [S07] 单条生成完成与 Candidate 创建路径；[S08] 模板快照；[S06] `CandidateResponse.content: dict`；[S21] 导出校验。

**问题与影响：** worker 在 `json.loads` 成功后即可记录成功并创建候选，没有执行冻结的 `output_schema`。`{}`、错误字段对象、数组甚至 JSON null 都不是合法业务结果，却能通过这一语法门禁。错误对象会把问题推迟到人工审核或导出；数组 / null 还与候选响应中的 dict 合同冲突。JSON mode 不等于结构校验，不能替代服务端验证。[E04]

**触发 / 复现：** 让模型返回 `{}`、`[]`、`null` 或 `{"unexpected": 1}`。局部试验确认四种输入均可被 JSON 解析接受，但都不是包含有效 question / answer 的 QA 对象。是否最终出现响应序列化错误还取决于后续数据库与响应路径，未做完整端到端断言。

**修复建议：** 在成功状态、计数和 Candidate 写入之前，使用该批次冻结的输出 schema 和 task_type 验证类型、必填字段、空值与长度。模型端 structured output 仅作为辅助；将错误归类为稳定的格式错误，并受 Task 重试预算约束。

**回归验收：** 有效 JSON 但类型错误、缺字段、空白答案、拒绝输出、截断输出都不能增加成功计数；schema 合法结果应保持原有 provenance 和计费记录。

**验证状态：** `malformed_json_shape` 已执行；没有调用真实 LLM，也没有声称某个模型一定会生成这些错误结果。

<a id="cr-005"></a>
### CR-005 · 旧刷新请求可清空新会话，或把旧修改请求用新用户身份重发

**P1 ｜ 证据 B ｜ 前端 / 身份边界**

**代码定位：** [S09] `requestRefresh`、`TokenStore.setTokens`、`handleAuthFailure`；[S10] `fetchApi` / `fetchBlob` 的 401 重试。

**问题与影响：** 令牌代数只保护旧刷新结果“不覆盖新令牌”，没有保护旧 HTTP 请求的后续动作。过期刷新失败后，旧请求仍会无条件触发当前会话清理；旧刷新成功但因代数变化被丢弃时，函数仍返回 true，HTTP 层会重新读取当前令牌并重发原请求。新会话若属于另一用户，旧操作可能被归属给新用户；后端权限检查仍存在，这不是绕过后端权限，而是前端操作与身份错配。

**触发 / 复现：** 旧用户的 PATCH 得到 401 → 挂起刷新 → 退出并登录新用户 → 释放旧刷新响应。失败分支把新令牌清成 null；成功分支重发旧 body，Authorization 却变成 `Bearer NEW_USER_ACCESS`。两种交错均已在受控 Promise 中观察到。

**修复建议：** 为每个请求捕获 session_epoch，并在刷新前后、重试前和认证失败清理前检查一致性。区分“同会话令牌轮换”和“会话切换”；会话切换后的旧请求应取消 / 丢弃，绝不能自动改用新身份重放。刷新返回值应表达 applied / stale / failed，而非仅布尔值。

**回归验收：** 覆盖新登录、跨标签页登录、退出、旧刷新成功 / 失败、并发多个 401 以及写请求。必须断言旧操作没有以新身份发出，且旧失败没有清除新会话。

**验证状态：** `old_refresh_failure_clears_new_session`、`old_mutation_replayed_as_new_user` 均已观察缺陷；使用内存 TokenStore，未执行浏览器 E2E。

<a id="cr-006"></a>
### CR-006 · 上传大小限制在完整读入内存之后才执行

**P1 ｜ 证据 C ｜ 后端 / 可用性**

**代码定位：** [S02] 文档上传：`content = await file.read()` 之后检查 `len(content)` 与 200 MB 上限。

**问题与影响：** 超过限制的上传在被 413 拒绝前已经物化为完整 bytes。即使 multipart 接收阶段使用临时文件，后续无界 read 仍会把内容重新载入内存。多个大文件并发时，文件级限额无法提供进程级内存保护。影响成立需要请求到达该接口且没有更严格的入口限制；本次没有对线上服务做压力请求。

**触发 / 复现：** 在隔离环境向有上传权限的接口提交超过 200 MB 的文件，或并发提交接近上限的文件，监测 API 进程峰值 RSS；应在构造全量 bytes 之前拒绝。源码中的读入与检查顺序足以确认保护位置错误。

**修复建议：** 分块读取并维护累计大小，在 MAX+1 字节处停止；配合代理 / ASGI 接收限额、用户与项目配额及上传并发控制。对需要全量 PDF 的后续处理使用受控临时文件或限额 worker，不在 API 请求中重复复制全文件。

**回归验收：** 有 / 无 Content-Length、分块传输、超限、并发和客户端中断场景；超限请求不得创建数据库记录或对象存储残留，API RSS 应受合理上界约束。

**验证状态：** 源码确认；为避免资源消耗，没有执行大文件或拒绝服务压测。

<a id="cr-007"></a>
### CR-007 · worker 状态迁移未接入事件发布，文档页可能一直显示旧状态

**P2 ｜ 证据 C ｜ 全链路 / 任务可观测性**

**代码定位：** [S11] `_publish_event` / `publish_transition`；[S12][S13][S14] 持久任务执行路径；[S15] 文档页 `fetchData` 与 `lastMessage` effect。

**问题与影响：** TaskService 已定义版本化 Redis 事件，取消 / 手动重试有调用，但已读 worker 领取、完成及失败路径未接入该发布器；仓库搜索 `publish_transition` 仅找到定义。文档详情页在初次加载、用户动作后和收到 WS 消息时刷新，没有相应的活动任务轮询兜底。数据库可以正确完成任务，但 UI 继续停留在 queued / processing。

**触发 / 复现：** 保持文档详情页打开，触发解析或切分，等待 worker 在数据库中完成，不进行其他操作。预期终态自动显示；当前链路缺少驱动该刷新所需的完成事件。取消和重试能发事件，不等于普通任务生命周期已经覆盖。

**修复建议：** 在事务提交成功后发布所有关键迁移，优先采用事务 outbox 避免“先发事件、再提交失败”；统一处理成功、失败、取消与恢复。前端对活动任务加入低频 REST 轮询，断线重连后重新同步，WS 只作提示而非唯一真源。

**回归验收：** 实测 worker 提交 → outbox / Redis → WS → 页面更新；Redis 暂停、事件丢失和乱序时，REST 兜底仍应最终收敛。

**验证状态：** 源码和调用点搜索确认；未启动 Redis 或浏览器验证最终 UI。

<a id="cr-008"></a>
### CR-008 · WebSocket 握手前关闭无法向浏览器传递预期的 4401 / 4403

**P2 ｜ 证据 B ｜ 前后端 / 认证协议**

**代码定位：** [S16] `task_websocket_endpoint` 授权失败分支；[S17] onclose 重连 / 认证分支。

**问题与影响：** 后端在 accept 前调用 `close(code=4401/4403)`，Starlette 此时发送的是 HTTP 403 拒绝握手，不是已建立 WebSocket 上的关闭帧。[E03] 前端却依赖这两个私有码触发退出或禁止重连；真实浏览器握手失败通常表现为异常关闭，进入通用重连分支。[E07] 即使访问令牌仅过期、刷新令牌仍有效，也没有通过该路径完成刷新。

**触发 / 复现：** 局部真实 TCP 试验启动 FastAPI / Uvicorn，在 websocket endpoint 直接 `await ws.close(code=4401)`，WebSocket 客户端收到 `InvalidStatus` / HTTP 403，未收到 4401 关闭帧。未把 TestClient 对 ASGI 消息的表现当作真实浏览器协议证据。

**修复建议：** 将身份恢复设计成可观测协议：例如先用可刷新的 HTTP 请求获取短时 WS 票据，再建立连接；握手失败时做有界身份复核并区分无权限、令牌过期和网络故障。避免无上限重试旧令牌，也不要把所有异常关闭都当成强制退出。

**回归验收：** 真实浏览器覆盖无效 token、有效 refresh + 过期 access、无项目权限、已连接后撤权、网络断开与恢复；无权限不能无限重连，过期 access 不应直接丢失有效会话。

**验证状态：** `websocket_preaccept_close`：真实本地 ASGI 网络握手已观察 HTTP 403；未执行浏览器自动化。

<a id="cr-009"></a>
### CR-009 · WebSocket 广播覆盖连接快照，丢失并发新连接

**P2 ｜ 证据 B ｜ 后端 / 实时事件**

**代码定位：** [S16] `ConnectionManager._broadcast`、`connect`、`disconnect`。

**问题与影响：** 广播先把连接字典复制成列表，在逐个 await 权限查询后构造 live，最后整体替换 `active_connections[project_id]`。等待数据库期间新连接写入的是旧字典，之后会被这次替换丢弃。并发 disconnect 也可能被旧快照重新带回。连接本身仍可保持打开，却不再正常接收项目事件。

**触发 / 复现：** 广播读取 socket-A 后，在权限查询处暂停；socket-B 连接并被加入项目字典；继续广播。局部试验最终字典只剩 socket-A，socket-B 的登记丢失。

**修复建议：** 不要在 await 之后用过期快照整体覆盖共享状态。保留同一连接容器，对经复核的单个连接执行带身份检查的移除；必要时使用锁 / generation 管理短小的状态变更，网络和数据库等待不放在长临界区。

**回归验收：** 受控栅栏测试 connect-during-broadcast、disconnect-during-broadcast、最后一个连接离开后再连接，以及发送异常清理；每个存活授权连接应持续收到后续事件。

**验证状态：** `ws_connection_snapshot_race` 已执行，观察到新连接消失。

<a id="cr-010"></a>
### CR-010 · HTTP 超时及组合取消信号在响应头到达时被过早清理

**P2 ｜ 证据 B ｜ 前端 / 请求生命周期**

**代码定位：** [S10] `fetchWithTimeout` 第 26–81 行，以及随后 `fetchApi` 的 `res.json()` / `fetchBlob` 的 body 消费。

**问题与影响：** fetch Promise 在收到响应头时即可完成，helper 的 finally 随即清除 timer 和转发 abort 的监听器，响应体却在外部继续读取。因此后续 JSON / Blob 下载不再受这段超时控制；同时使用 timeout 和外部 signal 时，响应头之后的外部取消也不能再传到组合信号。单独直接传入的外部 signal 不应被误称为完全失效。

**触发 / 复现：** 本地 HTTP 服务器立即发送 200 响应头，600 ms 后才结束 JSON body。客户端设 200 ms 超时，并在响应头后发出 abort，最终 body 仍在约 616 ms 后成功读完。

**修复建议：** 把 deadline 和 signal 生命周期包住完整 fetch + body 消费，或返回带显式 dispose 的响应包装并由读取方统一清理。覆盖错误响应体、204、JSON、Blob 和重试过程，区分单次请求与总操作预算。

**回归验收：** 慢响应头、慢响应体、永不结束 body、响应头后取消、401 后重试，均应有确定终态并释放资源。

**验证状态：** `response_body_ignores_deadline_and_abort` 已在本地 TCP HTTP + Node fetch 上观察；不是基于定时器模拟猜测。

<a id="cr-011"></a>
### CR-011 · 刷新令牌没有独立截止时间，会阻塞整个 single-flight 队列

**P2 ｜ 证据 B ｜ 前端 / 认证恢复**

**代码定位：** [S09] `requestRefresh` / `refreshToken`；[S10] 401 后等待 `refreshToken()`。

**问题与影响：** 刷新使用裸 fetch，没有 AbortSignal 或自己的超时，所有遇到 401 的调用又共享 pendingRefresh。原业务请求的 fetchWithTimeout 已结束，不能限制这个刷新等待。一条不结束的刷新请求会使所有等待者长时间悬挂；某个调用者取消也没有在等待阶段获得及时响应。

**触发 / 复现：** 使用一个保持 pending 的刷新 transport，连续调用 refreshToken，两次返回同一 Promise，且调用链里没有自主 deadline。局部测试只做有界观察，不把几十毫秒的等待宣称成对真实网络“永久悬挂”的实测。

**修复建议：** 为共享刷新设置独立超时和最终清理；每个等待者可独立与自己的取消信号竞争，而不是取消别人仍需要的共享刷新。会话 epoch 校验应同时解决 CR-005，避免超时后清理另一个会话。

**回归验收：** 刷新服务不响应、响应体不结束、调用者中途取消、一个等待者退出而另一个仍活动等场景；重试不得形成刷新风暴。

**验证状态：** `refresh_singleflight_has_no_own_deadline` 已执行有界挂起观察；缺少截止时间由源码直接确认。

<a id="cr-012"></a>
### CR-012 · 候选切换和列表查询缺少旧响应隔离，可能显示并提交错误来源

**P2 ｜ 证据 B ｜ 前端 / 审核工作台**

**代码定位：** [S18] `handleExpand`、源 Chunk 请求与 `fetchCandidates`；[S19] `fetchExports`；[S15] `fetchData`。

**问题与影响：** 展开候选时直接将异步 Chunk 响应写入共享的 chunkContent / sourceChunk，没有校验当前候选 ID，也没有取消旧请求。快速从 A 切换到 B 后，A 的晚到响应能覆盖 B 的证据区。相关列表也只有取消尚未执行的 setTimeout，没有取消已发出的请求，页码或项目切换后可显示旧数据。这里将相同的异步状态归属问题合并计数，而非每个页面重复报一个 bug。

**触发 / 复现：** 让 A 的 Chunk 请求较慢，切换到 B 并先完成 B 请求，再释放 A。局部结果为 `expanded_candidate=candidate-B`，但 `displayed_source_chunk=chunk-A`。列表同理可构造 page=2 却显示 page=1 返回值。

**修复建议：** 请求携带 project / entity / page 的 key 或 generation；响应落地前再次比较。切换时清空旧证据、禁用依赖来源的操作，取消旧请求；保存和审核必须绑定当前表单所对应的候选 ID 与 revision。可以用具有请求 key 隔离的缓存层，但仍需处理 mutation 的归属。

**回归验收：** React 测试控制 Promise 逆序完成；快速切换候选、项目和页码后，旧响应不得改变当前表单。结合 CR-003 验证后端也拒绝错误批次的证据。

**验证状态：** `stale_candidate_evidence_response` 已观察状态错配；尚未运行仓库 React 组件测试。

<a id="cr-013"></a>
### CR-013 · messages / ShareGPT 导出静默丢弃 input 上下文

**P2 ｜ 证据 B ｜ 功能 / 数据集导出**

**代码定位：** [S20] `_format_messages` / `_format_sharegpt`；[S21] 内容规范化。

**问题与影响：** 聊天格式只把 instruction / question 放进用户轮次，把 output / answer 放进回答，没有携带非空 input。SFT / Alpaca 风格数据合法使用 input 提供材料，因此同一条已审核样本转换成聊天格式后会丢失回答依赖的上下文。文件仍可解析、hash 仍可正确，完整性校验无法发现这种语义丢字段。

**触发 / 复现：** 输入 `instruction="根据输入回答"`、`input="压力为 12 Pa"`、`output="12 Pa"`；导出 messages 或 ShareGPT。结果保留问题和答案，却没有“压力为 12 Pa”材料。局部字段投影试验确认唯一上下文标记消失。

**修复建议：** 明确每种格式的语义映射，将 instruction 与非空 input 按确定规则组合到用户内容，或提供明确的独立消息结构。变更 formatter 版本，旧导出快照继续引用旧版本；不能只在校验层偷偷修改历史封存结果。

**回归验收：** 空 input、非空 input、多行上下文、question/answer 与 instruction/output 两类输入，以及不同格式间的语义等价检查。

**验证状态：** `export_input_loss` 已执行字段投影复现；没有把它当作完整 MinIO 导出集成测试。

<a id="cr-014"></a>
### CR-014 · 导出 worker 直接执行同步 MinIO 调用，阻塞心跳与超时处理

**P2 ｜ 证据 B ｜ 后端 / 任务可靠性**

**代码定位：** [S20] 导出公共流程中的 `put_object_versioned`；[S22] 同步 MinIO 实现；[S12][S14] worker 控制任务。

**问题与影响：** 异步 handler 直接调用同步的对象 HEAD、下载校验和 PUT。网络等待期间事件循环不能调度同进程的心跳、取消检查及超时回调。租约 / run-token 机制本身虽已存在，却不能让被同步调用阻塞的事件循环及时执行；慢上传可能引起租约过期、重复工作或迟迟不能取消。

**触发 / 复现：** 用阻塞的存储替身模拟慢 PUT，和心跳 ticker 放在同一事件循环。100 ms 阻塞窗口内观察到零次心跳。真实 MinIO 的延迟、SDK 超时和 lease 参数尚未联调，因此不声称每次上传都会失败。

**修复建议：** 把同步存储 I/O 转移到有界线程池 / `asyncio.to_thread`，并设置传输层超时。注意取消 await 不会自动终止底层线程；仍要保留 run-token 发布门禁、唯一对象 key 与重试幂等。CPU 密集的长切分也应按相同原则评估。

**回归验收：** 上传耗时超过心跳间隔、上传期间取消、租约被接管和线程返回晚于任务终止等情况；旧 attempt 不得发布结果，其他任务的心跳不被拖住。

**验证状态：** `blocking_storage_heartbeat` 已执行事件循环阻塞机制验证；未启动真实 MinIO。

<a id="cr-015"></a>
### CR-015 · Chunk 来源页码由正文正则猜测，不能作为可信 PDF 证据

**P1 ｜ 证据 B ｜ 功能 / 来源追溯**

**代码定位：** [S23] `_apply_overlap` 页码正则；[S24] 真实 page_mapping；[S25] `source_pages` 写入；[S26] 清洗合并文本。

**问题与影响：** 切分器使用正文中的 `page 123` 字样生成 source_pages，没有沿用解析器的物理页码映射。一般正文可能没有任何 page 标记，而文内“见 Page 888”的交叉引用会被误当成实际来源页。该字段会进入后续证据链，因此这里将其列为高优先级数据可信性问题，而不是普通显示瑕疵。

**触发 / 复现：** 为物理来源为第 2 页的测试文本加入“See Page 888”，正则输出 `[888]`；没有页码字样则可能得到空数组。局部试验直接运行该正则，未创建或解析真实 PDF，也未伪称验证了整个页码映射流水线。

**修复建议：** 保留解析阶段的页 / 字符区间映射，并在清洗、合并、切分与 overlap 时维护可追溯的区间转换。无法确定的来源应显式标为 unknown，不从自由文本猜测物理页号；同时提供来源版本和可信度。

**回归验收：** 无 page 字样、正文引用其他页、跨页段落、空白页、清洗删改、overlap 跨页及多解析器输入；每个来源页码应能反向定位到原 PDF。

**验证状态：** `raw_page_regex` 已重现错误页码推断；完整多解析器映射验证待补。

<a id="cr-016"></a>
### CR-016 · 注册冲突查询可能命中两名用户并抛出 500 级异常

**P2 ｜ 证据 B ｜ 后端 / 账户功能**

**代码定位：** [S27] `AuthService.register`；[S49] 注册异常处理。

**问题与影响：** 用 username 相同 OR email 相同查询，再调用 `scalar_one_or_none()`。用户名由用户 A 占用、邮箱由用户 B 占用时，结果是两行，不是一个“已存在用户”；SQLAlchemy 抛出 MultipleResultsFound，无法走普通重复注册业务错误分支。

**触发 / 复现：** 创建 alice/a@example.test、bob/b@example.test，再注册 alice/b@example.test。局部使用同样 OR 条件和 scalar_one_or_none，得到 `MultipleResultsFound`。该错误发生在查询消费阶段，不依赖真正创建新用户。

**修复建议：** 用 EXISTS、limit(1) 或显式分别查询来判断冲突，并把数据库唯一约束冲突统一映射为稳定业务码。仍需保留数据库唯一约束处理并发注册，不要仅依赖查询。

**回归验收：** 仅用户名冲突、仅邮箱冲突、两者属于同一用户、两者分别属于不同用户以及并发相同注册，均返回可预期的 4xx，而非未处理异常。

**验证状态：** `register_two_unique_matches` 已在 SQLAlchemy 2.0 上执行查询语义复现。

<a id="cr-017"></a>
### CR-017 · 手工拼接数据库 URL 未转义凭据中的特殊字符

**P2 ｜ 证据 B ｜ 部署 / 运行配置**

**代码定位：** [S28] `Settings.database_url`；[S39] `create_async_engine(settings.database_url)`。

**问题与影响：** 直接把数据库用户名 / 密码放进 URL 字符串，合法密码中的 @ 等保留字符会改变 URL 的解析结果。部署使用随机强密码时，可能被错误解释为主机名的一部分，表现为认证失败或 DNS 失败，而非配置校验错误。[E02]

**触发 / 复现：** 将密码设为测试值 `review@pass`，手工 URL 被解析出 `password=review`、`host=pass@localhost`。局部 make_url 已重现；没有使用或披露真实数据库凭据。

**修复建议：** 优先使用 SQLAlchemy `URL.create` 构造 URL 对象；需要字符串时统一正确编码用户名、密码等部分，并保持 async / migration 连接构造一致。日志输出时继续隐藏密码。

**回归验收：** 使用包含 @、:、/、% 和空格的凭据进行解析单测及真实数据库连接测试；正常简单密码不应回归。

**验证状态：** `database_url_password` 已执行，URL.create 对照保留了正确主机和密码。

<a id="cr-018"></a>
### CR-018 · T09 存量回填使用不兼容的 Row 下标，升级有数据的数据库会失败

**P1 ｜ 证据 B ｜ 数据库 / 迁移**

**代码定位：** [S29] `_backfill_curated_revisions`、`_backfill_curated_items`、`_backfill_evidence_offsets`、`_demote_approved_items`；`upgrade()` 调用顺序。

**问题与影响：** 迁移中的 execute(...).fetchall() 返回 SQLAlchemy Row，代码随后使用 `row["..."]`。SQLAlchemy 2.x 的 Row 不是这种字典接口，需要 result.mappings() 或 row._mapping。[E01] 只要相应历史查询非空，就会进入不兼容访问；空库中循环不执行，因此空库 smoke test 不能证明可升级存量库。

**触发 / 复现：** 在旧版本数据库中准备一条 CuratedRevision 后升级 T09；首个回填循环访问 `row["curated_item_id"]` 即可触发错误。局部执行真实 SQLAlchemy Row 访问得到 `TypeError: tuple indices must be integers or slices, not str`。

**修复建议：** 将所有需要按列名访问的结果改为 `.mappings().all()` 或明确使用 `_mapping`，逐个审查迁移中同类访问。以脱敏旧库快照运行 upgrade，并验证回填计数、hash 与降级规则；已经发布的迁移修改需结合实际部署历史管理。

**回归验收：** 空库与有历史 revision、无 revision 的 item、存在 evidence、历史 approved item 的四类存量 fixture 都必须执行升级；还要测试事务回滚及可重试性。

**验证状态：** `migration_row_access` 已观察 SQLAlchemy 2.0.50 的真实异常；没有运行完整 Alembic / PostgreSQL 迁移。

<a id="cr-019"></a>
### CR-019 · T09 证据回填保存 NFC 文本坐标，却继续引用未规范化原文

**P2 ｜ 证据 B ｜ 数据库 / Unicode 证据**

**代码定位：** [S29] `_backfill_evidence_offsets`；[S04] 运行时对原始 Chunk 字符范围的校验。

**问题与影响：** 回填在 NFC 规范化后的 content_nfc 中找 quote，并将该字符串的索引直接保存为 start_char / end_char，但没有把 Chunk 原文转换成相同坐标空间。组合字符会改变 code point 数量，规范化后的索引不等于原始字符串索引。修复 CR-018 后，这个后续逻辑问题才会在相应数据上显现。

**触发 / 复现：** 原文为 `e\u0301 x`（e + combining acute accent + 空格 + x），quote 为 x。NFC 文本中 x 的索引是 2，原文中是 3；回填保存 [2,3)，实际切片却是空格。局部试验已观察此错位。

**修复建议：** 始终以不可变原文 code point 为坐标基准。先做原文精确匹配；必须支持规范化等价匹配时，构建规范化位置到原文区间的映射。无法无歧义恢复的证据应标记待复核，而不是保存看似精确的错误坐标。

**回归验收：** NFC / NFD、多个 combining marks、emoji、重复 quote、规范化发生在 quote 之前及内部等场景；保存后立即验证原文切片与 quote 的既定等价合同。

**验证状态：** `unicode_backfill_offsets` 已执行；与前端已经正确的 UTF-16 → code point 换算不是同一个问题。

<a id="cr-020"></a>
### CR-020 · 非 TaskPolicy 配置的默认项切换存在并发竞争

**P2 ｜ 证据 C ｜ 后端 / 配置功能**

**代码定位：** [S30] `ConfigService.set_default`；[S31] ModelConfig / ParserProfile / ChunkProfile / ExportProfile 与 TaskPolicy 约束差异。

**问题与影响：** 只有 TaskPolicy 的默认切换加了项目锁 / advisory lock 和部分唯一索引；其他配置仍使用“查找当前默认 → 全部置 false → 目标置 true”的非串行化流程。两个请求读到同一个旧状态后可各自将不同目标设为默认，使同项目同类型出现多个 is_default。前端取第一个默认配置时，实际默认模型或切分配置可能不确定。

**触发 / 复现：** 同项目存在两个非默认 ModelConfig，同时把 A、B 设为默认，控制两个请求先完成读取再各自提交。在没有唯一索引与统一锁的这些模型上，两条 true 可以同时保留。仓库合同要求每个项目每类配置仅一个默认；不能因为 TaskPolicy 已修复就认为通用服务也已修复。

**修复建议：** 为每类配置建立 `project_id WHERE is_default=true` 的唯一约束，并在统一的项目 / 配置类型锁下完成切换；添加约束前显式处理历史重复，不随意挑一条。唯一冲突应转换为明确业务冲突而不是泛化 500。

**回归验收：** 四类非 TaskPolicy 配置分别进行双会话并发切换；0 / 1 个旧默认、跨项目、删除默认项与并发切换均需覆盖。

**验证状态：** 模型、服务及仓库默认项合同交叉确认；未执行 PostgreSQL 双会话复现。

<a id="cr-021"></a>
### CR-021 · 前端锁文件不能通过当前 CI 的干净安装，质量门禁全部跳过

**P1 ｜ 证据 A ｜ 前端 / 构建与 CI**

**代码定位：** [S32] frontend Install dependencies；[S33][S34] 依赖声明与锁文件；[CI-F] 已读取的真实作业日志。

**问题与影响：** 固定提交的前端 CI 在 `npm ci` 直接以 EUSAGE 退出，报告锁文件缺少 `@emnapi/core@2.0.0-alpha.3` 与 `@emnapi/runtime@2.0.0-alpha.3`。后续 lint、TypeScript、Vitest 和 Next.js build 全部 skipped。因此目前没有该次 CI 证明前端可从干净环境构建。不能把缓存 node_modules 中能运行视为等价验收。

**触发 / 复现：** 实际记录：run `34741941555`、job `103682995323`，2026-09-13 06:06:51 UTC，Node 22.23.2 / npm 10.9.8。这里是读取既有运行记录，不是本次重新执行 npm ci。根因可能包含生成锁文件时的 npm / 平台差异；尚未完整求解依赖图。

**修复建议：** 在与 CI 一致的 Node / npm 版本下重新生成并审查 lockfile，特别检查可选及平台相关依赖；提交完整锁文件后在全新 Linux checkout 执行 npm ci。不要改用 npm install 绕过 CI 的可复现性检查，也不要以 --force 掩盖依赖问题。

**回归验收：** 干净 npm ci 成功后，原 lint、tsc、api:check、Vitest、build 门禁必须真实运行且记录结果；额外覆盖团队使用的平台与 CI 平台的锁文件一致性。

**验证状态：** 真实 CI 日志与作业步骤已核实；本次未重新安装前端依赖。

<a id="cr-022"></a>
### CR-022 · 默认 MinIO 镜像在已观察 CI 环境无法拉取，后端测试根本未启动

**P1 ｜ 证据 A ｜ 部署 / 后端 CI**

**代码定位：** [S32] backend.services.minio.image；[S35] 相同默认镜像引用；[CI-B] 真实容器初始化日志。

**问题与影响：** 该次 CI 三次拉取未带版本的 `minio/minio` 均返回 pull access denied，Initialize containers 失败，checkout、依赖安装、迁移和 pytest 随后全部 skipped。说明默认测试环境的外部镜像依赖当前不可用。证据只证明这次 CI 环境的拉取失败，不能据此断言镜像在全球永久下架或所有部署都无法访问。

**触发 / 复现：** 实际记录：run `34741941555`、job `103682995231`；2026-09-13 06:06:51 至 06:07:07 UTC 重试后失败。日志显示 PostgreSQL 和 Redis 能拉取，失败点明确位于 MinIO 镜像，而不是本仓库 Python 测试。

**修复建议：** 确定团队有权访问、来源可信的 MinIO 兼容镜像 / 内部镜像并锁定 tag + digest；确需认证时配置最小权限的拉取凭据。增加镜像可用性预检并同步开发、测试、CI 的镜像配置。不要只在某台机器依赖已有缓存。

**回归验收：** 无本地 Docker 缓存环境中拉取、启动、健康检查、建 bucket、启用版本控制和读写测试均通过后，才能运行全套后端测试。CI 存储恢复后还应复核下节列出的 mc 初始化命令风险。

**验证状态：** 真实 CI 日志已核实；没有在本地执行 Docker，也没有验证候选替代镜像。

## 5. 额外风险与待验证项（不计入缺陷总数）

### 5.1 密钥保护与部署边界

`ConfigService._map_fields` 只是把 api_key 改名为 api_key_encrypted，已读写入 / 使用链路没有应用层加解密。[S30][S31][S44] 应明确该名称是否承诺加密，以及数据库、备份、日志、管理员可见范围和密钥轮换策略。不能仅凭列名断言数据已经加密；同样，没有看到应用层加密也不能直接宣称磁盘或数据库完全没有加密。

运行设置含开发用默认凭据 / JWT 配置；Compose 发布 PostgreSQL、Redis、MinIO 端口，并未把所有端口限定在 loopback。[S28][S35] 这在受控开发机可能合理，但生产启动应拒绝弱默认项并限制网络暴露。本次未访问部署网络，**没有认定存在实际公网暴露或凭据泄露**。

### 5.2 MinIO 初始化命令的下一层风险

CI 的 bucket 初始化使用 `docker run ... minio/mc sh -c '...'`，没有覆盖 entrypoint；上游 Dockerfile 声明 ENTRYPOINT 为 mc。[S32][E06] 这意味着 shell 参数可能被当成 mc 子命令。已观察的 CI 在镜像拉取前置条件就失败了，因此这一步实际是 skipped，**不能把它说成该次 CI 的失败原因**。固定镜像后应检查实际镜像入口与 shell 是否存在，必要时逐条调用 mc 或使用明确入口的初始化容器。未使用本地 Docker 实机复现，因此列为待验证风险。

### 5.3 其他未提升为正式缺陷的疑点

生成跟踪 hook 在 queued 取消直接返回 cancelled 时，可能绕过后续轮询终态回调；文档详情页的 chunk idempotency ref 成功后未显式重置，也值得与后端请求摘要规则联合检查。[S48][S15] 本次没有完整确认所有消费方与服务端幂等规则，故不把这两点计入正式问题。解析器 egress、DNS / 重定向防护、所有 dataset / benchmark 页面、依赖 CVE 与所有数据库触发器，也需要专项验收，不能由本报告推导为安全通过。

## 6. 已存在且应保留的防护；排除的误报

JWT 校验区分 access / refresh，解析用户 ID 并在数据库中检查启用状态；项目资源解析也有统一权限入口。[S40][S41][S45] 本次没有确认可以直接跨项目读取任意资源的 IDOR，因此不将泛化“缺少权限检查”列为发现。

任务队列已有租约、run token、原子状态迁移和并发领取机制。[S12][S13][S14] 问题是部分同步 I/O、事件通知及业务层版本绑定没有与这些机制完整衔接，不是建议抛弃持久任务架构、退回不可靠的内存后台任务。

清洗工作台已有保存串行化、租约心跳和冲突草稿保留。[S38] 候选页已经显式把 UTF-16 selection offset 转成 Unicode code point。[S18] 本报告的 Unicode 缺陷在历史回填的 NFC 坐标映射，不是前端缺少 surrogate pair 处理。

精修 revision / 审批绑定、导出 manifest 和对象版本封存已经有较完整实现。[S03][S29][S37][S42][S22] 正确 hash 只能说明某份内容没有变，不能证明它在生成时满足 schema、带齐上下文或拥有正确页码。

另一个已排除项是“GitHub Actions services.command 必然非法”：读取时的官方工作流语法已支持该字段，不能套用旧经验报错。[E05] 真实后端 CI 日志明确失败于 MinIO 镜像拉取，而非 YAML 语法解析。

## 7. 实际验证记录

### 7.1 本次执行的 16 个机制用例

这些脚本的断言是“观察到了缺陷”，所以退出码 0 只说明复现脚本按预期运行，**不表示被审查仓库健康**。每个用例都在本地隔离环境执行；没有复制完整仓库、没有连接生产服务。

| 用例 | 对应发现 | 关键观察 |
|---|---|---|
| migration_row_access | CR-018 | Row 字符串下标 TypeError；_mapping 对照正确 |
| register_two_unique_matches | CR-016 | 两个唯一条件分别命中两行，MultipleResultsFound |
| delete_object_before_fk | CR-001 | 数据库文档仍在，临时对象已删除 |
| unicode_backfill_offsets | CR-019 | NFC 索引 2，原文正确索引 3，切片是空格 |
| raw_page_regex | CR-015 | 声明物理来源第 2 页的文本被推断为 888 |
| export_input_loss | CR-013 | messages / ShareGPT 丢失唯一 input 标记 |
| malformed_json_shape | CR-004 | 四种错误形状仍均通过 JSON 解析 |
| database_url_password | CR-017 | @ 使主机变为 pass@localhost |
| ws_connection_snapshot_race | CR-009 | 广播覆盖字典后 socket-B 消失 |
| blocking_storage_heartbeat | CR-014 | 同步阻塞窗口内心跳次数为 0 |
| websocket_preaccept_close | CR-008 | 真实握手 HTTP 403，未发送 4401 关闭帧 |
| old_refresh_failure_clears_new_session | CR-005 | 新用户 access token 被清成 null |
| old_mutation_replayed_as_new_user | CR-005 | 旧 PATCH body 携带新用户 Authorization |
| response_body_ignores_deadline_and_abort | CR-010 | 200 ms 限时 + abort 后仍约 616 ms 成功读完 |
| refresh_singleflight_has_no_own_deadline | CR-011 | 共享挂起 Promise，源码无自有截止时间 |
| stale_candidate_evidence_response | CR-012 | 展开 B，显示的却是 A 的源 Chunk |

归档内 `backend-results.json`、`frontend-results.json` 保存结构化结果；`reproduce_backend.py`、`reproduce_frontend.mjs` 保存可复查逻辑；`ci-evidence.md` 保存关键 CI 摘录及原始作业链接。脚本说明了替身与真实网络部分，避免将简化模型误认成项目原始测试。

### 7.2 项目环境必须补做的回归

| 回归组 | 最关键验收 | 防止仅“看起来通过” |
|---|---|---|
| 干净构建 | 全新 Linux checkout，npm ci 与后端环境初始化 | 后续测试必须 executed，不能 skipped |
| 存量迁移 | t08 历史数据 → t09 → head | 不只用空库；核对证据坐标及回填计数 |
| 审核竞争 | 编辑 / 审核 / 提升双会话交错 | 验证实际行版本与审批内容哈希 |
| 对象一致性 | 删除 / 上传的 flush、commit、I/O 故障注入 | 数据库和对象是否同时符合保留 / 删除策略 |
| 会话竞态 | 登录切换、401、刷新、写请求与跨标签页 | 断言最终发出的身份和 body，不只看 toast |
| 任务与 WS | 真 worker、Redis、浏览器及断线 | REST 最终收敛，终态通知不丢，撤权立即生效 |
| 生成到导出 | schema 错误、非空 input、格式映射 | 文件能解析与 hash 正确之外，还要验证语义 |
| 页码追溯 | 真实多页 PDF + 清洗修改 + overlap | 每条 source_pages 可反向定位到原文 |

建议对前后端 E2E 至少覆盖一次完整的小样本工作流：上传 → 解析 → 清洗审核 → 接受合并版本 → 切分 → 生成 → 人工证据审核 → 精修批准 → 编排 → 导出 → 下载及完整性校验，并在关键步骤加入并发与失败注入。

## 8. 建议的修复批次

| 批次 | 内容 | 对应编号 | 完成门槛 |
|---|---|---|---|
| 0：恢复验证能力 | 修正干净安装、固定可用存储镜像、验证初始化 | CR-021、022；5.2 风险 | CI 真正进入 lint / test / build |
| 1：保护已有数据与身份 | 删除策略、候选版本合同、请求会话隔离、存量迁移 | CR-001、002、005、018、019 | 故障与竞争用例稳定复现后转绿；存量备份可恢复 |
| 2：保证数据语义 | schema、来源批次、页码、导出 input | CR-003、004、013、015 | 证据、输入与导出内容可端到端对应 |
| 3：修复异步可靠性 | 上传限额、超时、WS、事件、旧响应、同步 I/O | CR-006–012、014 | 慢服务、断网、重连和取消最终收敛 |
| 4：收紧配置和账户边界 | 注册冲突、URL、默认项唯一性 | CR-016、017、020 | 特殊凭据及双会话配置回归通过 |

批次不是工时估计，也不表示 P2 必须等所有 P1 才能并行修复。涉及 API revision / schema 的改动应同一 PR 更新服务端、OpenAPI、前端生成类型和契约测试；涉及 immutable formatter / manifest 的改动应提升版本，保留旧产物解释能力。

## 9. 核心文件审查索引

为避免夸大覆盖率，下表按实际读取范围区分全文件 / 多段主路径与部分读取；列入“源码引用”不自动意味着逐行审查整个文件。

| 分组 | 实际读取的核心文件 | 范围说明 |
|---|---|---|
| 前端认证与请求 | auth.ts、api.ts、ws.ts、auth-context、ws-context | 全文件 |
| 前端业务 | use-cleaning-workbench、use-generation-tracking、exports page | 全文件 |
| 前端候选 / 文档 | candidates page、document detail page | 分别重点读取 1–300 / 1–285 行请求与状态逻辑，非完整视觉验收 |
| API 基础 | config、database、JWT、dependencies、auth service / router | 全文件；authz 为关键权限解析部分 |
| 文档与证据 | document service、candidate service / router / schema、document / chunk_set / curated models | 主路径 / 全文件；documents router 重点为 1–240 行 |
| 清洗与精修 | clean_version service、curated_item service | 分别重点读取 1–240 / 1–260 行，并交叉检查模型与调用方 |
| 配置 | config service、config models | 全文件；默认项相关合同与迁移搜索交叉核对 |
| worker / queue | runner、queue、execution、generate_worker、chunk_worker、task_service | 多段覆盖主实现 |
| 导出与编排 | export_worker、export_service、composition_policy、export_content | worker 格式化 / 上传主路径；service 重点为 1–270 行；policy 与内容校验全文件 |
| 共享库 | minio_client、hybrid_heading、pymupdf_parser、LLM client | 全文件 |
| 迁移与交付 | T09 migration、ci.yml、docker-compose.yml、package.json、真实 CI logs | T09 重点 1–540 行；锁文件缺项依据真实 npm ci 日志，未完整解析 lockfile 依赖图 |

未完整审阅区域包括所有 UI 展示组件、全部数据集 / 基准集 CRUD、所有本地 / 远程解析器网络实现、其余迁移全文及全部测试源码。这里的“全面”体现为跨层主链路和失效边界审查，不代表对每个文件逐行穷尽或保证没有其他 bug。

## 10. 来源

仓库源码链接全部固定到审查 SHA；证据定位以文件 + 函数 / 代码块为准。没有为未实际计数的代码片段捏造精确行号。外部文档仅用于核对框架行为，真实 CI 日志用于证明对应提交在特定运行环境中的结果。

- **S01**：[document_service.py][S01] — `apps/api/app/services/document_service.py`
- **S02**：[documents.py（上传、删除及触发路由）][S02] — `apps/api/app/routers/documents.py`
- **S03**：[curated.py（证据外键、精修模型）][S03] — `apps/api/app/models/curated.py`
- **S04**：[candidate_service.py][S04] — `apps/api/app/services/candidate_service.py`
- **S05**：[candidates.py（路由）][S05] — `apps/api/app/routers/candidates.py`
- **S06**：[candidate.py（请求、响应合同）][S06] — `apps/api/app/schemas/candidate.py`
- **S07**：[generate_worker.py][S07] — `apps/api/app/workers/generate_worker.py`
- **S08**：[generation_service.py][S08] — `apps/api/app/services/generation_service.py`
- **S09**：[auth.ts][S09] — `apps/web/src/lib/auth.ts`
- **S10**：[api.ts][S10] — `apps/web/src/lib/api.ts`
- **S11**：[task_service.py][S11] — `apps/api/app/services/task_service.py`
- **S12**：[runner.py][S12] — `apps/api/app/workers/runner.py`
- **S13**：[queue.py][S13] — `apps/api/app/workers/queue.py`
- **S14**：[execution.py][S14] — `apps/api/app/workers/execution.py`
- **S15**：[文档详情页][S15] — `apps/web/src/app/(dashboard)/projects/[id]/documents/[did]/page.tsx`
- **S16**：[task_ws.py][S16] — `apps/api/app/ws/task_ws.py`
- **S17**：[ws.ts][S17] — `apps/web/src/lib/ws.ts`
- **S18**：[候选审核页][S18] — `apps/web/src/app/(dashboard)/projects/[id]/candidates/page.tsx`
- **S19**：[导出历史页][S19] — `apps/web/src/app/(dashboard)/projects/[id]/exports/page.tsx`
- **S20**：[export_worker.py][S20] — `apps/api/app/workers/export_worker.py`
- **S21**：[export_content.py][S21] — `libs/domain/domain/export_content.py`
- **S22**：[minio_client.py][S22] — `libs/storage/storage/minio_client.py`
- **S23**：[hybrid_heading.py][S23] — `libs/splitters/splitters/hybrid_heading.py`
- **S24**：[pymupdf_parser.py][S24] — `libs/parsing/parsing/pymupdf_parser.py`
- **S25**：[chunk_worker.py][S25] — `apps/api/app/workers/chunk_worker.py`
- **S26**：[clean_version_service.py][S26] — `apps/api/app/services/clean_version_service.py`
- **S27**：[auth_service.py][S27] — `apps/api/app/services/auth_service.py`
- **S28**：[config.py（运行设置）][S28] — `apps/api/app/config.py`
- **S29**：[T09 迁移][S29] — `apps/api/migrations/versions/t09_curated_evidence_approval.py`
- **S30**：[config_service.py][S30] — `apps/api/app/services/config_service.py`
- **S31**：[config.py（配置模型）][S31] — `apps/api/app/models/config.py`
- **S32**：[ci.yml][S32] — `.github/workflows/ci.yml`
- **S33**：[package.json][S33] — `apps/web/package.json`
- **S34**：[package-lock.json（CI 指认的锁文件）][S34] — `apps/web/package-lock.json`
- **S35**：[docker-compose.yml][S35] — `infra/docker/docker-compose.yml`
- **S36**：[T09 证据与审批合同][S36] — `docs/code-review-remediation/T09-candidate-curated-evidence-and-approval.md`
- **S37**：[curated_item_service.py][S37] — `apps/api/app/services/curated_item_service.py`
- **S38**：[use-cleaning-workbench.ts][S38] — `apps/web/src/hooks/use-cleaning-workbench.ts`
- **S39**：[database.py][S39] — `apps/api/app/database.py`
- **S40**：[JWT 核心校验][S40] — `apps/api/app/core/jwt.py`
- **S41**：[HTTP 认证依赖][S41] — `apps/api/app/dependencies.py`
- **S42**：[export_service.py][S42] — `apps/api/app/services/export_service.py`
- **S43**：[composition_policy.py][S43] — `apps/api/app/services/composition_policy.py`
- **S44**：[LLM client.py][S44] — `libs/llm/llm/client.py`
- **S45**：[项目权限解析][S45] — `apps/api/app/authz.py`
- **S46**：[auth-context.tsx][S46] — `apps/web/src/contexts/auth-context.tsx`
- **S47**：[ws-context.tsx][S47] — `apps/web/src/contexts/ws-context.tsx`
- **S48**：[use-generation-tracking.ts][S48] — `apps/web/src/hooks/use-generation-tracking.ts`
- **S49**：[auth.py（路由）][S49] — `apps/api/app/routers/auth.py`
- **S50**：[document.py（模型）][S50] — `apps/api/app/models/document.py`
- **S51**：[chunk_set.py（模型）][S51] — `apps/api/app/models/chunk_set.py`

### 官方资料与运行记录

- **E01**：[SQLAlchemy 2.0：Row 与 mappings][E01]
- **E02**：[SQLAlchemy：连接 URL 特殊字符][E02]
- **E03**：[Starlette：WebSocket denial response][E03]
- **E04**：[JSON mode 与结构化输出][E04]
- **E05**：[GitHub Actions 工作流语法][E05]
- **E06**：[MinIO mc 上游 Dockerfile（读取时为 master，非项目锁定依赖）][E06]
- **E07**：[MDN WebSocket 关闭码][E07]
- **CI-R**：[固定提交的 CI run][CI-R]；**CI-F**：[前端作业][CI-F]；**CI-B**：[后端作业][CI-B]。

---

[S01]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/services/document_service.py
[S02]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/routers/documents.py
[S03]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/models/curated.py
[S04]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/services/candidate_service.py
[S05]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/routers/candidates.py
[S06]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/schemas/candidate.py
[S07]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/workers/generate_worker.py
[S08]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/services/generation_service.py
[S09]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/web/src/lib/auth.ts
[S10]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/web/src/lib/api.ts
[S11]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/services/task_service.py
[S12]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/workers/runner.py
[S13]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/workers/queue.py
[S14]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/workers/execution.py
[S15]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/web/src/app/%28dashboard%29/projects/%5Bid%5D/documents/%5Bdid%5D/page.tsx
[S16]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/ws/task_ws.py
[S17]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/web/src/lib/ws.ts
[S18]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/web/src/app/%28dashboard%29/projects/%5Bid%5D/candidates/page.tsx
[S19]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/web/src/app/%28dashboard%29/projects/%5Bid%5D/exports/page.tsx
[S20]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/workers/export_worker.py
[S21]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/libs/domain/domain/export_content.py
[S22]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/libs/storage/storage/minio_client.py
[S23]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/libs/splitters/splitters/hybrid_heading.py
[S24]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/libs/parsing/parsing/pymupdf_parser.py
[S25]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/workers/chunk_worker.py
[S26]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/services/clean_version_service.py
[S27]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/services/auth_service.py
[S28]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/config.py
[S29]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/migrations/versions/t09_curated_evidence_approval.py
[S30]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/services/config_service.py
[S31]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/models/config.py
[S32]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/.github/workflows/ci.yml
[S33]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/web/package.json
[S34]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/web/package-lock.json
[S35]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/infra/docker/docker-compose.yml
[S36]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/docs/code-review-remediation/T09-candidate-curated-evidence-and-approval.md
[S37]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/services/curated_item_service.py
[S38]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/web/src/hooks/use-cleaning-workbench.ts
[S39]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/database.py
[S40]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/core/jwt.py
[S41]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/dependencies.py
[S42]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/services/export_service.py
[S43]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/services/composition_policy.py
[S44]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/libs/llm/llm/client.py
[S45]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/authz.py
[S46]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/web/src/contexts/auth-context.tsx
[S47]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/web/src/contexts/ws-context.tsx
[S48]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/web/src/hooks/use-generation-tracking.ts
[S49]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/routers/auth.py
[S50]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/models/document.py
[S51]: https://github.com/aL0ng-z/domain-dataset-gen/blob/019f9ea70fb2325aad068e03cc7637372a9fe273/apps/api/app/models/chunk_set.py
[E01]: https://docs.sqlalchemy.org/en/20/changelog/migration_20.html#result-rows-act-like-named-tuples
[E02]: https://docs.sqlalchemy.org/en/20/core/engines.html#escaping-special-characters-such-as-signs-in-passwords
[E03]: https://starlette.dev/websockets/#send-denial-response
[E04]: https://developers.openai.com/api/docs/guides/structured-outputs
[E05]: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idservicesservice_idcommand
[E06]: https://github.com/minio/mc/blob/master/Dockerfile
[E07]: https://developer.mozilla.org/en-US/docs/Web/API/CloseEvent/code
[CI-R]: https://github.com/aL0ng-z/domain-dataset-gen/actions/runs/34741941555
[CI-F]: https://github.com/aL0ng-z/domain-dataset-gen/actions/runs/34741941555/job/103682995323
[CI-B]: https://github.com/aL0ng-z/domain-dataset-gen/actions/runs/34741941555/job/103682995231
