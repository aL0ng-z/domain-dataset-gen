# T01：JWT 令牌语义与前端认证状态

- 状态：待实施
- 优先级：P0-Security
- 建议规模：M
- 直接依赖：T00
- 后续依赖本卡：T02、T07
- 可并行：可与 T03、T04 并行

## 1. 目标与背景

当前 access token 没有类型声明，而 refresh token 仅增加了 type=refresh；通用认证依赖只校验签名和 sub，
因此有效期更长的 refresh token 可直接调用普通业务 API。PDF 与 WebSocket 还各自复制了一套较弱的 JWT
解码逻辑。前端在 localStorage、认证 Context、API 重试和 WebSocket 中分别维护认证状态，并发 401 时可能
重复刷新、覆盖新令牌或在刷新失败后继续携带旧令牌。

本卡建立单一令牌语义和单一前端认证状态：所有受保护入口只接受 access token，刷新入口只接受 refresh
token；多个并发请求共享一次刷新结果，并能一致地进入已认证或匿名状态。

## 2. 范围

1. access token 明确写入 type=access；refresh token 保持 type=refresh。
2. 提供唯一的 JWT 解码与声明校验函数，校验签名、算法、exp、iat、sub 和预期 type。
3. 所有 HTTP、PDF 和 WebSocket 的令牌解码都复用上述函数，不再自行调用 jwt.decode。
4. 通用用户依赖只接受 access token，并继续从数据库确认用户存在且处于启用状态。
5. /api/auth/refresh 只接受 refresh token；登录与刷新都返回新的 access/refresh 对。
6. 统一前端令牌读写、认证状态、401 刷新、退出登录和跨标签页同步。
7. 并发 401 使用 single-flight 刷新；每个原请求最多重试一次。
8. WebSocket 在 access token 更新后用新令牌重连；具体项目成员校验留给 T02。
9. 对认证失败日志做脱敏，不记录 JWT、Authorization 头或 refresh 请求体。

## 3. 明确不做

- 不在本卡实现项目成员、对象归属或角色授权；这些属于 T02。
- 不引入 refresh session 表、jti 黑名单、服务端登出撤销或设备管理。
- 不迁移到 HttpOnly Cookie，也不改变现有 Bearer Token 的总体传输模式。
- 不处理密码策略、MFA、OIDC/SSO、密钥轮换、JWKS 或多租户 issuer。
- 不改变现有角色层级，也不以 JWT 中的 role 作为最终授权依据。
- 不兼容缺少 type 声明的历史令牌；部署后用户需要重新登录。
- 不顺带修改任务重试、PDF 项目授权或 WebSocket 项目订阅逻辑。

## 4. 数据库合同

- 预计无数据库表、字段或 Alembic 迁移。
- User.id、User.is_active 和数据库中的当前角色继续是用户身份与权限事实源。
- 令牌中的 sub 必须是可解析的 User UUID；格式错误统一视为 401，不得产生 500。
- 本卡的 refresh token 仍是无状态令牌；返回新 refresh token 不代表旧 token 已被撤销。
- 测试必须覆盖用户被停用后，尚未过期的 access/refresh token 都立即失效。

## 5. API 合同

### 5.1 JWT 声明

access token 至少包含 sub、type=access、iat、exp；refresh token 至少包含
sub、type=refresh、iat、exp。签发和验证只能使用 settings.jwt_algorithm 指定的算法。

### 5.2 端点行为

| 场景 | 预期结果 |
|---|---|
| POST /api/auth/login 成功 | 200，响应结构仍为 access_token、refresh_token、token_type |
| POST /api/auth/refresh 携带有效 refresh token | 200，返回一对新令牌 |
| refresh 端点收到 access token | 401 |
| 任意受保护 HTTP/PDF 入口收到 refresh token | 401 |
| WebSocket 收到 refresh token | 握手不进入订阅，关闭码 4401 或项目统一的未认证码 |
| token 缺少 type、sub、exp，sub 非 UUID，签名错误或已过期 | 401 |
| token 对应用户不存在或已停用 | 401 |

- 认证错误响应不得泄露 token 的具体内容、用户是否存在或签名校验细节。
- HTTP 401 应携带 WWW-Authenticate: Bearer；合法 access token 但权限不足仍由后续授权层返回 403。
- /api/auth/refresh 继续通过 JSON 请求体接收 refresh_token，不接受 Authorization 头代替。
- /api/auth/me、注册管理接口和所有业务依赖均只接收 access token。
- OpenAPI/Pydantic 合同保持 TokenResponse 字段兼容；可补充字段说明和安全响应示例。

## 6. 前端合同

- apps/web/src/lib/auth.ts 成为令牌持久化的唯一读写入口；其他模块不得直接写 localStorage。
- 认证状态至少区分 bootstrapping、authenticated、refreshing、anonymous，避免初始化时误跳登录页。
- apiFetch 遇到 401 时仅对非登录、非刷新请求尝试一次刷新，并为所有并发调用共享同一个 Promise。
- 刷新成功后原请求使用最新 access token 各重试一次；不得复用首次请求的旧 Authorization 头。
- 刷新失败、响应格式错误或再次 401 时，原子清除两类令牌和用户状态，并只触发一次登录跳转。
- logout 必须清除 access token、refresh token、当前用户、刷新中的 Promise 和 WebSocket 连接。
- 同源多标签页通过 storage 事件同步登录、刷新和退出；旧标签不得把旧 token 覆盖回存储。
- WebSocket 获取令牌必须经过统一认证模块；令牌轮换后关闭旧连接并按现有退避策略重连。
- 本卡暂时保留 localStorage；代码与日志中不得输出 token。

## 7. 预期修改面

- apps/api/app/services/auth_service.py
- apps/api/app/dependencies.py
- apps/api/app/routers/auth.py
- apps/api/app/routers/documents.py 中 PDF 的重复解码部分
- apps/api/app/ws/task_ws.py 中 WebSocket 的重复解码部分
- apps/api/app/schemas/auth.py
- apps/web/src/lib/auth.ts
- apps/web/src/lib/api.ts
- apps/web/src/lib/ws.ts
- apps/web/src/contexts/auth-context.tsx
- 对应后端集成测试、前端单元/组件测试、OpenAPI 说明
- 实施完成时更新 docs/logs/dev-log.md

## 8. 依赖

- 依赖 T00 提供 access/refresh fixture、ASGI 客户端、前端 API mock、fake timer 和 CI 门禁。
- T02 复用本卡的 access-token 校验结果，增加项目与对象授权。
- 若 T00 尚未合并，可先完成设计和纯单元测试，但本卡不得在缺少集成回归时合并。

## 9. 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| 部署后历史 access token 因缺少 type 全部失效 | 在发布说明中明确要求重新登录，不引入临时兼容后门 |
| 并发刷新响应乱序覆盖新 token | single-flight；令牌写入只发生在共享刷新流程中 |
| 401 重试递归或形成请求风暴 | refresh/login 排除刷新逻辑；每请求最多重试一次 |
| Context、存储和 WebSocket 状态分叉 | 统一认证事件源，并测试 token 更新与 logout |
| 自定义入口继续使用弱解码 | 静态搜索禁止路由和 WS 直接调用 jwt.decode |
| 无状态 refresh token 无法立即撤销 | 记录为剩余风险；如业务要求撤销，另立会话管理任务卡 |

## 10. 实施步骤

1. 定义 TokenType 与统一 decode_token(expected_type)；规范 401 异常和日志脱敏。
2. 为 access/refresh 签发补齐明确 type，并验证必需声明及 UUID sub。
3. 改造 get_current_user 与 refresh_tokens，删除各入口重复的 JWT 解码。
4. 让 PDF 和 WebSocket 只复用统一 access 校验；不在本卡增加项目授权。
5. 抽取前端 TokenStore/AuthSession，收口 localStorage 访问。
6. 在 apiFetch 实现 single-flight 刷新、一次性重试和失败原子退出。
7. 让 AuthContext、跨标签页事件和 WebSocket 消费同一认证状态。
8. 增加后端负向矩阵及前端并发、乱序、退出回归测试。
9. 更新 OpenAPI 说明、发布注意事项和中文开发日志。

## 11. 自动化验收标准

在仓库根目录执行：

~~~powershell
conda activate DatasetGen
python -m pytest -q tests/integration/test_auth_tokens.py tests/integration/test_protected_token_types.py
python -m ruff check apps/api/app/services/auth_service.py apps/api/app/dependencies.py apps/api/app/routers/auth.py apps/api/app/ws/task_ws.py tests
~~~

在前端目录执行：

~~~powershell
cd apps/web
npm test -- --run src/lib/auth.test.ts src/lib/api.test.ts src/contexts/auth-context.test.tsx
npm run lint
npm exec tsc -- --noEmit
~~~

必须满足：

1. access token 可访问 /api/auth/me，refresh token 对同一路径稳定返回 401。
2. refresh token 对普通业务 API、PDF 入口均返回 401，对 WebSocket 不得创建 Redis 订阅。
3. access token、缺 type token、过期 token、伪造 token 和非 UUID sub 调用刷新均返回 401。
4. 用户停用后，两类未过期 token 都失败，且服务端日志不包含原 token。
5. 参数化测试覆盖 Authorization 缺失、Bearer 格式错误及算法不匹配。
6. 20 个同时收到 401 的前端请求只产生 1 次 refresh 请求；成功后各自仅重试 1 次。
7. 并发刷新失败时只执行一次清理和一次跳转，所有等待请求一致失败，不出现无限重试。
8. 模拟旧刷新响应晚到时，不得覆盖一次较新的登录会话。
9. token 轮换后 WebSocket 使用新 access token 重连；logout 后不再自动重连。
10. 全仓搜索除统一令牌模块外，不再存在业务入口直接调用 jwt.decode。

## 12. 停止条件

出现以下任一情况时停止实施并请求架构决策：

- 产品要求兼容无 type 的历史令牌，而又不接受一次性强制重新登录；
- 产品要求“退出即全设备撤销”或 refresh token 一次性轮换，需要引入持久化会话模型；
- 安全基线要求改用 HttpOnly Cookie、OIDC、独立 issuer/audience 或非对称密钥；
- 前端存在本卡未识别的第二套认证客户端，无法在不改变外部协议的情况下收口；
- PDF 或 WebSocket 必须改变传输协议才能通过安全评审；该变化应与 T02 联合设计。

## 13. 审查重点

- 所有受保护入口是否显式要求 type=access，而非仅仅拒绝已知的 refresh。
- 缺失或畸形声明是否统一返回 401，且不会因 UUID 转换异常产生 500。
- 数据库中的用户状态和角色是否仍为事实源。
- 前端 single-flight 是否真的共享同一刷新操作，重试是否严格有界。
- 登出、刷新失败、跨标签页和 WebSocket 是否同时收敛到同一状态。
- 测试是否包含真实 HTTP/WS 边界，而不只是直接调用解码函数。

## 14. 完成定义

- 本卡所有自动化验收命令返回 0，负向与并发场景全部通过。
- refresh token 无法调用任何受保护 HTTP、PDF 或 WebSocket 入口。
- 前端不存在并发刷新风暴、旧响应覆盖新会话或失败后残留认证状态。
- OpenAPI 与前端类型保持兼容，发布说明明确历史令牌失效影响。
- 代码中只有统一令牌模块负责 JWT 解码，安全敏感日志已验证脱敏。
- docs/logs/dev-log.md 已用中文记录实现范围、测试命令与结果。
