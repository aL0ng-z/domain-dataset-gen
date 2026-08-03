# T03：解析器出站与凭证安全

- 状态：代码及自动化验收完成；开发库 profile 待配置 registry 后迁移，待真实 R1 联调
- 优先级：P0-Security
- 建议规模：L
- 直接依赖：T00
- 后续依赖本卡：解析链路生产部署验收、T11
- 可并行：可与 T01、T02、T04 并行；合并后需重跑 T02 跨项目回归

## 1. 目标与背景

ParserProfile 禁止直接保存 api_key，却允许 editor 设置 base_url、upload_url、results_url 等任意地址。
parse worker 随后把服务器全局 MinerU/PaddleOCR Token 注入这些选项，并可能向该地址发送 PDF。攻击者可
据此窃取全局凭证和文档，或通过重定向、私网地址、DNS 重绑定访问云元数据及内网服务。

本卡把“业务解析参数”和“网络/凭证配置”分离：项目用户只能引用服务端批准的端点；凭证绑定到明确
provider origin；每个初始、重定向和响应派生 URL 都由统一出站策略校验，且用户 URL 永远不能获得全局
Token 或 PDF。

## 2. 范围

1. 建立服务端 ParserEndpointRegistry，定义 endpoint_ref、parser_name、可信 origin、凭证引用、
   允许的 artifact origin、端口、重定向和网络区域策略。
2. ParserProfile 只保存 endpoint_ref 与 provider-specific 功能参数，不再保存任何网络 URL 或密钥。
3. 对 parser_options 使用按 parser_name 的字段白名单，拒绝未知字段和嵌套的秘密/URL 别名。
4. 在 Profile 创建、更新、服务启动和任务执行四个边界做 fail-closed 校验。
5. 创建 ParseJob 时原子冻结当时有效、无秘密的 ParserProfile 与 EndpointPolicy 快照、版本和 SHA-256；
   worker、重试与 T11 只读取该快照，后续 profile/registry 修改不回写或改变历史 ParseJob。
6. 全局 MinerU/PaddleOCR Token 仅在发送到其绑定的 credential origin 前即时注入。
7. 覆盖 MinerU 初始任务、轮询、signed upload、结果 ZIP，以及 PaddleOCR 请求的全部 URL。
8. 禁止自动重定向；手动处理每一跳并重新应用 scheme、origin、DNS、IP 和凭证转发策略。
9. 拒绝环回、私网、link-local、multicast、reserved、unspecified 和云元数据地址，包括 IPv6。
10. 防止 DNS rebinding：连接必须使用已验证并固定的解析结果，或验证实际 peer IP。
11. managed-local parser 只能引用管理员注册的精确服务地址，不携带远程 provider 全局 Token。
12. 对存量 ParserProfile 提供 dry-run、显式映射、事务迁移和执行期拒绝策略。
13. 日志、错误、指标、快照和任务记录对 Token、Authorization、PDF、signed URL query 做脱敏。

## 3. 明确不做

- 不允许项目 editor 自由配置第三方解析服务地址，即使该地址使用 HTTPS。
- 不把全局 API Token 复制成项目配置，也不在数据库中新增明文秘密。
- 不修复 ModelConfig、LLMClient 或其他非解析器的 SSRF；应另立同类出站安全卡。
- 不建设通用 Secret Manager、service mesh、网络防火墙或全平台 egress proxy。
- 不改变解析结果结构、清洗流程或 ParserProfile 的版本管理语义。
- 不在普通 CI 中调用真实 MinerU、PaddleOCR、云对象存储或内网服务。
- 不支持用户自定义认证头、代理、CA、上传地址或结果下载地址。
- 不用简单字符串 startswith、仅解析一次 DNS 或“禁止 localhost”代替完整网络策略。

## 4. 数据库合同

- 继续使用 ParserProfile.parser_options JSONB 并收紧其数据合同；同时为 ParseJob 增加可审计快照字段。
- parser_options 允许 endpoint_ref 及解析功能参数；禁止 base_url、upload_url、results_url、
  vlm_base_url、proxy、headers、api_key、access_token、token、secret、credential 等网络/秘密字段。
- 禁止字段检查必须递归、大小写不敏感并规范化连字符/下划线，避免在嵌套对象中绕过。
- endpoint_ref 是不透明稳定 ID，只能解析到服务端 registry；数据库不保存 registry 的真实 URL 或 Token。
- 服务端 registry 至少包含：

| 字段 | 合同 |
|---|---|
| endpoint_ref | 稳定唯一、不含秘密的标识 |
| parser_name | 可使用该端点的唯一解析器类型 |
| credential_ref | 指向环境/Secret Provider；managed-local 必须为空 |
| credential_origins | 可携带 Authorization 的精确 scheme/host/port 集合 |
| artifact_origins | 可上传 PDF 或下载结果的精确 origin/受控域后缀集合 |
| network_zone | public-remote 或 managed-local |
| redirect_policy | 禁止，或有界且每跳重验的显式策略 |

- ParseJob 至少新增 parser_profile_snapshot JSONB、parser_profile_sha256 CHAR(64)、
  endpoint_policy_snapshot JSONB、endpoint_policy_ref、endpoint_policy_version、
  endpoint_policy_sha256 CHAR(64) 和 snapshot_schema_version。
- 新建 ParseJob 的上述字段必须在创建事务内一次性写入且形成完整组合；不得先插入 job 再异步补快照。
- parser_profile_snapshot 保存 profile_id/version、parser_name、endpoint_ref 和已校验功能参数；
  endpoint_policy_snapshot 保存执行所需的规范 origin、用途 allowlist、网络区和重定向规则。
- 快照允许保存不透明 credential slot/reference 以便运行时取密钥，但严禁保存真实 secret、Token、
  Authorization、PDF 字节、provider 响应或任何预签名 URL；这些值也不得参与 hash。
- 两个 SHA-256 都基于带 snapshot_schema_version 的规范 JSON 字节计算；键排序、Unicode、数字和空值
  规范必须固定并由同一个 canonicalize 函数生成，hash 字段存小写十六进制。
- 数据库约束允许 snapshot_schema_version=0 的 legacy_unavailable 标记使用空 hash；版本大于等于 1
  时两个 snapshot、两个 hash、endpoint_policy_ref/version 必须全部非空。
- 数据库触发器或等价强制机制禁止普通 UPDATE 改写任一快照、ref、version 或 hash 字段；状态更新不受影响。
- 同一配置的 profile 与 registry policy 必须以一个不可变解析结果原子冻结；并发更新只能让新 job 得到
  更新前或更新后的完整版本之一，不能产生混合快照。
- parse worker、retry 和重新派发必须以 ParseJob 快照为唯一配置输入，不得重新读取当前 ParserProfile
  或当前 registry 覆盖其语义；使用新配置必须创建新的 ParseJob。
- 紧急禁用端点可通过独立 kill switch 阻止尚未执行的 job，但不得修改原快照；恢复后若需新策略必须新建 job。
- 数据迁移先 dry-run：仅将与 registry 精确匹配的旧 URL 映射为 endpoint_ref 并删除网络字段。
- 无法唯一映射的 profile 必须让迁移在写入前整体失败，输出 profile ID 与 URL hash，不输出完整 signed URL。
- 迁移必须单事务、可重复运行；downgrade 只能恢复 registry 中的规范端点，不能恢复未知危险地址。
- 在迁移完成前，运行时对含旧网络字段的 profile 一律拒绝，不得为兼容而继续发送请求。
- 迁移前已存在的 ParseJob 不得伪造历史配置：标记 snapshot_schema_version=0 和 legacy_unavailable；
  T11 必须拒绝将其宣称为完整可复现快照，除非有可信的历史工件可以确定性回填。

## 5. API 合同

### 5.1 ParserProfile CRUD

- 创建/更新仍使用现有项目路由，但 parser_options 按 parser_name 的 Pydantic 判别联合或等价模型校验。
- endpoint_ref 必须存在、启用且 parser_name 匹配；否则返回 422 和稳定错误码 invalid_parser_endpoint。
- 任意禁用网络/秘密字段返回 422 和 unsafe_parser_option；响应不得回显字段值。
- ParserProfileResponse 只返回 endpoint_ref、功能参数和计算值 credential_configured，不返回真实 URL、
  credential_ref 或 secret。
- 可增加只读端点列出当前用户可选的 endpoint_ref、显示名、parser_name 和 credential_configured；
  不返回主机、IP、端口、allowlist 或网络区域内部细节。
- 若 registry 缺少端点、凭证未配置或旧 profile 未迁移，触发解析在创建 Task/ParseJob 前返回 409；
  不得先下载 PDF 或把 Document 标成 parsing。

### 5.2 ParseJob 冻结合同

- 触发解析时先完成 T02 的 Document/Profile 同项目校验，再解析有效 registry policy、清除秘密、
  规范化并计算 hash，最后在同一事务创建 ParseJob、快照和 Task。
- ParseJobResponse 增加 snapshot_schema_version、parser_profile_sha256、endpoint_policy_ref、
  endpoint_policy_version 和 endpoint_policy_sha256；普通 API 不返回完整 EndpointPolicy 或 credential slot，
  如提供审计详情接口也只能返回经过递归脱敏的安全投影。
- Profile 或 registry 更新不得改变任何既有 ParseJob 响应；retry 必须保持相同 ref/version/hash。
- worker 若发现快照缺失、hash 不匹配、schema 不支持或含禁用秘密字段，必须在下载 PDF/联网前失败。
- T11 只能消费 ParseJob 中已冻结并验 hash 的快照，禁止回查“当前”ParserProfile/registry 填充 manifest。

### 5.3 出站请求

- public-remote 仅允许 HTTPS；禁止 URL userinfo、fragment、非 registry 端口和非规范 hostname。
- hostname 必须 IDNA/大小写/尾点规范化后与精确 origin 或边界正确的受控域后缀匹配。
- 解析得到的全部 A/AAAA 地址都必须为允许的公网地址；任一地址危险即整体拒绝。
- managed-local 仅允许 registry 中精确的 scheme/host/port/path，且不能获得远程 provider Token。
- credential request 默认禁止重定向；若 provider 必需，最多 3 跳，每跳重验且跨 origin 不转发 Authorization。
- provider 响应中的 upload、poll、archive URL 视为不可信输入，分别匹配 registry 的用途与 artifact origins。
- PDF 只可上传到允许的 upload origin；signed upload 不携带 provider Authorization。
- 结果下载不携带 provider Authorization，除非目标正是显式 credential origin 且 registry 允许该用途。
- HTTP 客户端必须限制连接/读取超时、最大响应体、最大压缩包与解压后大小，并拒绝非预期协议。
- 安全拒绝统一转换为可审计错误码，不把 URL query、响应体秘密或 PDF 内容写入 error_message。

## 6. 前端合同

- 项目设置页不再提供 base_url/upload_url/results_url/vlm_base_url 或自由 JSON 网络字段。
- 用户先选择 parser_name，再从服务端返回的安全列表选择 endpoint_ref，并编辑允许的功能参数。
- UI 可展示“凭证已配置/未配置”“端点由管理员管理”，但不得展示或推断真实 Token。
- 保存时展示 422 的字段级错误；409 的未迁移/未配置状态应阻止触发解析并给出管理员处理提示。
- 从旧 profile 读取时，只显示 requires_endpoint_remap 状态，不回显旧 URL，不提供“一键继续使用”。
- ParseJob 详情展示 profile 版本/hash 与 endpoint policy ref/version/hash，明确标注“创建时冻结”；
  前端不得用当前 Profile 名称或配置覆盖该历史信息。
- 前端不得把浏览器环境变量、localStorage 内容或用户输入拼入 parser_options 的网络字段。
- 不在客户端做安全 allowlist 判定；前端限制只用于 UX，服务端始终重新校验。

## 7. 预期修改面

- apps/api/app/config.py：registry 与凭证引用配置
- apps/api/app/models/parse.py 与 Alembic：ParseJob 快照、版本、hash 和不可变约束
- apps/api/app/schemas/document.py、documents router/service：创建时冻结及审计响应
- apps/api/app/schemas/config.py：按 parser_name 的严格 options 合同
- apps/api/app/services/config_service.py：endpoint_ref 校验与安全响应
- apps/api/app/workers/parse_worker.py：执行前复核和最晚凭证注入
- apps/api/app/security/egress.py 或等价统一安全传输层
- libs/parsing/parsing/mineru_parser.py、paddleocr_parser.py
- 两个 local service parser/manager 的 registry 接入
- apps/web/src/app/(dashboard)/projects/[id]/settings/page.tsx
- Alembic 数据迁移或 scripts/ 下受控迁移工具及 runbook
- tests/unit/security/、tests/contract/、解析器 fake transport 测试
- 实施完成时更新部署配置示例、安全 runbook 和根目录 DevLog.md

## 8. 依赖

- T00 提供不访问真实网络的记录型 fake transport、ParserProfile 工厂和迁移测试环境。
- T02 负责确保 Document 与 ParserProfile 属于同一项目；T03 不重复定义项目角色。
- T11 只读取本卡冻结的 ParseJob 快照与 hash；不得从当前 profile/registry 反向补全历史 manifest。
- 部署方需提供每个真实 provider 的精确 API/artifact origin 清单和 managed-local 服务清单。
- 若组织已有 egress proxy，可将统一策略放在 proxy，但应用层仍必须绑定 endpoint_ref 与 credential origin。

## 9. 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| provider 返回动态对象存储域名 | 由管理员维护用途限定 artifact allowlist；不能动态放宽到任意 HTTPS |
| DNS 校验后地址变化 | 固定已验证 IP 或校验 peer IP；连接复用也绑定已验证 origin |
| urllib 自动跟随重定向泄露凭证 | 禁用自动重定向，统一安全 transport 手动处理 |
| local service 合法使用私网地址 | 仅 managed-local registry 精确放行，禁止 Token，且与 public 策略分离 |
| 存量 profile 无法映射 | dry-run 在生产写入前失败，要求管理员给出映射 |
| 错误响应或 signed URL 泄密 | 结构化错误、query/headers 脱敏、响应体截断且不持久化 |
| 并发任务污染共享 options | 每任务复制不可变配置，凭证只存在于请求局部变量 |
| 快照固化后端点发生安全撤销 | 独立 kill switch 停止执行但保留快照；按新策略创建新 ParseJob |
| 存量 ParseJob 无法还原历史配置 | 明确标记 legacy_unavailable；T11 不得伪造可复现性 |

## 10. 实施步骤

1. 与部署方确认 provider API、上传、轮询、归档和 managed-local 端点清单。
2. 实现 typed ParserEndpointRegistry，启动时验证重复 ref、scheme、origin、凭证绑定和网络区域。
3. 增加 ParseJob 快照字段、规范 JSON/hash 工具、不可变约束和 legacy_unavailable 迁移。
4. 收紧 ParserProfile schema 与 CRUD，加入安全端点只读列表和安全响应序列化。
5. 在 ParseJob/Task 创建事务中冻结有效 profile/policy，让 worker 只消费并校验冻结快照。
6. 实现 URL 规范化、DNS/IP 分类、地址固定、重定向和敏感头转发策略。
7. 将 MinerU/PaddleOCR 及 local parser 全部迁移到统一 transport。
8. 把凭证注入移至受信请求的最后一刻，确保其他请求头中不存在全局 Token。
9. 编写 profile dry-run/迁移/回滚工具，对未知映射 fail closed。
10. 更新设置页，删除自由网络字段并展示 ParseJob 冻结版本/hash。
11. 增加恶意 URL、快照篡改、并发冻结、DNS rebinding 和迁移测试。
12. 更新环境配置、安全 runbook、发布检查清单和中文开发日志。

## 11. 自动化验收标准

在仓库根目录执行：

~~~powershell
conda activate DatasetGen
python -m pytest -q tests/unit/security/test_parser_egress.py tests/contract/test_parser_profiles.py tests/integration/test_parse_job_config_snapshot.py tests/test_remote_parsers.py tests/test_parser_credentials.py
python -m ruff check apps/api/app/security apps/api/app/models/parse.py apps/api/app/schemas apps/api/app/workers/parse_worker.py libs/parsing tests
~~~

在前端目录执行：

~~~powershell
cd apps/web
npm test -- --run src/app/parser-profile-security.test.tsx
npm run lint
npm exec tsc -- --noEmit
~~~

迁移验收：

~~~powershell
conda activate DatasetGen
python scripts/migrate_parser_profiles.py --dry-run
python scripts/migrate_parser_profiles.py --check
python -m pytest -q tests/integration/test_parse_job_snapshot_migration.py
~~~

必须满足：

1. profile 含任意层级/大小写变体的 URL、header、token、secret 字段均返回 422，数据库无写入。
2. 创建 ParseJob 后修改 Profile 或更新 registry，再执行及 retry 仍使用创建时的快照，
   原 snapshot/ref/version/hash 字节级不变；T11 fake 只读取该冻结值。
3. 快照规范化/hash 具有确定性；直接篡改 JSON、hash、ref 或 version 被数据库约束或 worker 校验拒绝。
4. 快照递归扫描证明不含测试 secret、Token、Authorization、PDF bytes 或预签名 URL/query。
5. Profile 更新与 ParseJob 创建并发时，100 次测试中每个 job 都只对应更新前或更新后的完整版本，
   不出现 profile/policy 混合，也不存在无快照 job。
6. localhost、127.0.0.1、0.0.0.0、RFC1918、169.254.169.254、IPv6 ::1/link-local/ULA、
   十进制/混淆 IP、userinfo、尾点和恶意子域测试全部 fail closed。
7. 允许 origin 重定向到私网、非 allowlist、不同端口或非 HTTPS 时，请求在下一跳前被拒绝。
8. DNS 首次返回公网、连接时切换私网的 rebinding 测试被拒绝；危险 peer 未收到请求。
9. 恶意 profile 指定 upload/results URL 时，记录型 fake 证明全局 Token 与 PDF 均从未发送。
10. MinerU 合法流程中 Token 只到 credential origin，PDF 只到允许 upload origin，archive 只从允许 origin 下载。
11. PaddleOCR 合法流程中 Token/PDF 仅到绑定的唯一 endpoint；响应重定向不泄露 Authorization。
12. 20 个跨项目并发解析任务各自使用不可变配置；恶意任务失败不影响可信任务，Token 不串到 local parser。
13. 错误、快照、Task.error_message、测试日志和 API 响应不包含秘密、PDF 或 signed query。
14. dry-run 对已知 profile 给出确定映射；存量 ParseJob 不能可信回填时标为 legacy_unavailable，
    且 T11 可复现性门禁失败；迁移重复执行结果一致。
15. 普通测试运行期间没有真实 DNS 或互联网访问，所有出站调用都由 fake transport 捕获。

## 12. 停止条件

出现以下任一情况时停止并请求安全/架构决策：

- 真实 provider 的上传或归档域名无法枚举到可维护的精确 origin/受控域边界；
- 当前 HTTP 栈或企业代理无法固定解析地址、获取 peer IP 或禁止自动重定向；
- 产品要求项目用户把全局 Token 或 PDF 发送到任意自定义 endpoint；
- 合法 managed-local 服务无法由管理员注册，只能接受用户任意私网 URL；
- 存量 profile 存在未知端点且管理员无法给出可信映射；
- provider 必须把 Authorization 跨 origin 转发，或要求把秘密放入 URL query；
- 无法为 EndpointPolicy 提供稳定 version/规范快照，或无法在 ParseJob 创建事务中原子冻结 profile/policy；
- 产品要求用当前 profile/registry 追溯性覆盖历史 ParseJob，或要求 T11 猜测 legacy job 的历史配置；
- 需要 per-project credential、通用 Secret Manager 或网络隔离才能满足业务需求。

## 13. 审查重点

- endpoint_ref 是否真正解析自服务端 registry，而不是用户可间接控制的 URL。
- 凭证是否只在已验证 credential origin 请求局部注入，且绝不进入共享 options。
- 初始、重定向、DNS 结果、signed upload、poll 和 archive URL 是否走同一安全边界。
- allowlist 是否按规范化 origin/域边界匹配，而非字符串前缀或包含判断。
- public-remote 与 managed-local 是否严格分离，local parser 是否永远拿不到全局 Token。
- ParseJob 是否在创建时完整冻结 profile/policy，hash 是否确定且数据库禁止后续改写。
- worker、retry 与 T11 是否只消费冻结快照，而没有回查当前配置改变历史语义。
- 快照和 API 序列化是否递归排除 secret、Token、PDF 与预签名 URL。
- 安全检查是否发生在 PDF 下载和任何网络连接之前。
- 迁移是否对未知 profile 整体 fail closed，降级是否不会重新启用危险地址。

## 14. 完成定义

- 所有自动化验收命令返回 0，恶意 URL、重定向、DNS rebinding 和并发隔离测试通过。
- 项目用户无法通过 ParserProfile 决定全局 Token 或 PDF 的网络去向。
- 所有解析器出站调用均经过统一安全 transport，不存在绕过的 urllib/urlopen 调用。
- 每个新 ParseJob 都有不可变、hash 可验证、无秘密的 profile/policy 快照；配置更新不改变历史 job。
- T11 的合同测试证明只消费冻结快照，对 legacy_unavailable 或 hash 失败的 job 拒绝生成完整快照。
- 存量 profile 已全部映射到 endpoint_ref；未知项为零，迁移检查可重复通过。
- 生产 registry、凭证引用、allowlist 和 managed-local 清单经安全审查并完成部署验证。
- 前端不再展示自由网络字段，OpenAPI、runbook 与根目录 DevLog.md 已同步更新。
