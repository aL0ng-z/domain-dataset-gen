# T04：API/前端合同单一事实源

- 状态：本地合同门禁完成，待 GitHub required checks
- 优先级：P0
- 建议规模：M
- 直接依赖：T00
- 被依赖：T05、T06、T07、T08、T09、T10

## 1. 目标与背景

以 FastAPI 生成的 OpenAPI 文档作为 HTTP 合同的唯一事实源，并由该文档生成前端类型和类型化调用入口。消除“后端返回 JSON 对象、前端声明为字符串”“后端返回裸数组、前端读取分页对象”“请求字段在两端名称不同”这类只能在运行时暴露的错误。

本卡只修复合同表达、生成和消费方式，不改变审核门禁、任务状态机、生成语义或编组规则。后续业务卡必须在本卡产出的合同和 CI 门禁上实施。

## 2. 范围

1. 为所有前端正在调用的 API 补齐稳定且唯一的 `operation_id`、请求模型、响应模型、查询参数边界和成功状态码。
2. 统一分页响应为 `PaginatedResponse[T]`，并修复未参数化泛型以及 Dataset items、Benchmark cases 的裸数组响应。
3. 明确 `Candidate.content`、`Candidate.review_evidence_spans`、`CuratedItem.content`、`CuratedRevision.content` 等字段是 JSON，而不是字符串。
4. 从 OpenAPI 自动生成并提交前端类型；建立类型化 API client，使页面不能再通过 `api.get<手写类型>()` 覆盖服务端合同。
5. 将当前页面迁移到生成类型；对于 JSON 字段，本卡只提供安全的只读序列化/摘要展示和请求前解析校验，不实现完整审核或审批体验。
6. 增加 OpenAPI 快照、生成产物漂移检查、代表性运行时响应校验和前端合同测试。
7. 记录合同变更规则和本地生成命令。
8. 定义后续业务卡可扩展的最小错误 envelope，使前端按稳定 `code` 分支，而不是匹配中文 `detail`。

## 3. 明确不做

- 不修复 JWT、对象级授权或跨项目访问；分别由 T01、T02 负责。
- 不实现单 Chunk/批量生成、GenerationBatch 或任务重试/取消；分别由 T08、T07 负责。
- 不定义 Candidate 证据 span 的最终业务约束，不开放 CuratedItem 审批，不实现 Dataset/Benchmark 添加器；分别由 T09、T10 负责。
- 不在本卡枚举所有历史业务错误码或决定各领域状态机；本卡只建立统一 envelope 和错误码注册规则，具体 code 由对应业务卡声明。
- 不采用 GraphQL，不替换现有认证刷新和网络传输实现，不为兼容错误的旧页面长期保留双合同。
- 不把数据库 ORM 模型直接暴露为前端类型。

## 4. 数据库合同

- 本卡不新增、不删除、不回填业务表或列，也不新增 Alembic revision。
- 数据库中的 `JSONB` 在 API 中必须映射为 JSON 值：对象至少生成 `Record<string, unknown>` 等价类型，数组必须保留元素类型；不得为了方便 Textarea 而改成 JSON 字符串。
- UUID、带时区时间和枚举分别通过 OpenAPI 表达为 `string(format: uuid)`、`string(format: date-time)` 和有限字符串联合类型。
- 若实施中发现现有数据库值无法通过已声明的 Pydantic 响应模型，停止本卡并记录数据样例；不得用 `Any`、静默丢字段或字符串化绕过。

## 5. API 合同

### 5.1 唯一事实源

- 源合同为应用实际导出的 OpenAPI JSON；Pydantic 请求/响应模型与显式路由元数据是其上游，前端声明不是合同源。
- 每个被前端调用的路由必须有稳定 `operation_id`，格式统一为 `<resource>_<action>`，重命名视为合同变更。
- 每个成功响应必须声明具体 `response_model`；禁止使用未参数化的 `PaginatedResponse`、裸 `dict` 或无 schema 的匿名返回值。
- `204` 响应无 body；异步接收统一为 `202`，同步创建统一为 `201`。已有端点若在本卡调整状态码，必须同步生成类型和合同测试。

### 5.2 分页合同

所有可增长集合使用结构 `{"items":[],"total":0,"page":1,"page_size":20}`。

- `page >= 1`，`1 <= page_size <= 100`；非法值返回 `422`。
- `total` 是过滤条件生效后的总数，不是当前页长度；空页仍返回相同四个键。
- 至少下列路由必须使用参数化分页模型：Candidates、CuratedItems、Dataset items、Benchmark cases、Datasets、Benchmarks、Documents、Chunks、Tasks、Exports、配置列表和模板列表。
- 因此本卡将 `GET /api/projects/{pid}/datasets/{did}/items` 从裸数组改为 `PaginatedResponse[DatasetItemResponse]`，将 `GET /api/projects/{pid}/benchmarks/{bid}/cases` 从裸数组改为 `PaginatedResponse[BenchmarkCaseResponse]`，并接受 `page/page_size`。

### 5.3 核心 JSON 合同

- `CandidateResponse.content`：JSON object，非 `string`、非可选。
- `CandidateResponse.review_evidence_spans`：JSON object 或 `null`；T09 会将其收紧为结构化 span 集合。
- `CuratedItemResponse.content`、`CuratedRevisionResponse.content`：JSON object，非字符串。
- `source_pages`：JSON array/object 或 `null`，按后端实际模型准确声明；在 T06/T09 确认最终结构前，禁止前端假定其为可直接显示的字符串。
- `GET /api/candidates` 的响应必须显式为 `PaginatedResponse[CandidateResponse]`。
- CuratedItem 的详情、证据、修订继续使用独立端点；本卡不虚构 `evidence_links`、`revision_history`、`version` 等未返回字段。

### 5.4 兼容与错误处理

- 本卡合同切换为一次性内部升级，不为错误字段（如 `template_id`、字符串 `evidence_spans`）增加永久 alias。确有外部调用方时停止并先形成版本/弃用决策。
- `404` 不泄露资源是否属于其他项目；对象级语义由 T02 落实。
- 除 FastAPI/Pydantic 字段校验 `422` 外，应用业务错误统一为 `ErrorResponse`：`{"code":"STABLE_MACHINE_CODE","message":"面向用户的说明","context":{},"request_id":"..."}`；`context` 只能含经 schema 声明的非敏感结构，允许为 `null`。
- `code` 使用稳定大写 snake case，并在 OpenAPI 中按端点声明有限联合；`message` 可本地化、不可作为前端控制流。`request_id` 用于日志关联，响应不得包含堆栈、SQL、Token 或跨项目对象信息。
- 校验错误继续使用单独的 `ValidationErrorResponse`，保留字段位置和原因；前端 client 将两类错误解析为判别联合，不把非 2xx body 强转为成功响应。
- T05–T11 可新增领域 code（如 `SECTION_VERSION_CONFLICT`），但必须复用 envelope、补 OpenAPI 响应和合同测试；禁止各自返回字符串 `detail`、`200 {success:false}` 或平行错误结构。

## 6. 前端合同

- 增加确定性的 `api:generate` 命令：导出 OpenAPI，再生成例如 `apps/web/src/lib/api/generated.ts` 的 `paths/components/operations` 类型。生成文件头明确“禁止手工编辑”。
- 类型化 client 以 HTTP method + path 为索引推导 path/query/body/response；保留现有 access-token、refresh、AbortSignal 和基础 URL 行为。
- 在已迁移页面禁止 `api.get<T>()`、`api.post<T>()` 这类由调用者任意指定响应的泛型；领域响应类型必须从生成的 `components` 或 `operations` 派生。
- 页面展示 JSON 时使用共享的 `formatJsonPreview(value)`/JSON viewer；编辑场景使用 `JSON.stringify(value, null, 2)` 初始化，并在发送前 `JSON.parse`、确认结果为 object。T09 可在此基础上替换为结构化表单。
- Dataset/Benchmark 详情页必须读取规范分页响应，空列表也能渲染；不得对裸数组访问 `.items/.total`。
- 加载、空态、`401/403/404/409/422` 和不可解析响应都有稳定 UI；业务分支读取生成类型中的 `error.code`，字段校验读取 `ValidationErrorResponse`，不得匹配中文 message/detail。

## 7. 预期修改面

- `apps/api/app/schemas/`：通用分页、JSON 字段及具体响应模型。
- `apps/api/app/routers/`：`operation_id`、参数化 `response_model`、分页参数和明确状态码。
- `apps/api/app/main.py` 或专用 OpenAPI 导出入口。
- `scripts/`：确定性 OpenAPI 导出/漂移检查脚本。
- `apps/web/package.json`、lockfile：OpenAPI 类型生成与检查命令及最小依赖。
- `apps/web/src/lib/api/`：生成类型、类型化 client、JSON 展示 helper。
- `apps/web/src/app/(dashboard)/projects/`：移除手写响应接口并迁移消费者。
- `tests/contract/`、前端 `*.test.*`、CI 配置、相关开发文档与根目录 `DevLog.md`。

## 8. 依赖

- 直接依赖 T00 的 API fixture、前端 API mock、测试命令和 CI 门禁。
- T05、T06、T07 及 T08–T10 必须基于本卡生成类型开发；不得各自再定义平行的手写合同。
- T01/T02 可与本卡并行，但合并后需要重新生成 OpenAPI 并运行认证/项目隔离合同回归。
- 工具依赖必须锁定版本；生成结果不能依赖运行时数据库、Redis、MinIO 或外部 LLM。

## 9. 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| OpenAPI 生成结果因排序或环境差异反复变化 | 固定 Python/Node/生成器版本，规范化 JSON 排序，并在全新 checkout 验证零漂移 |
| 一次迁移页面范围过大 | 先列出所有 `api.*<T>` 调用，按资源迁移；CI 最后禁止新增/残留调用者自报响应泛型 |
| 生成类型过宽，错误仍被 `unknown/any` 隐藏 | 后端为核心 JSON 与分页项提供具体 schema；CI 禁止生成产物和 API 边界出现显式 `any` |
| 状态码调整影响未知外部客户端 | 在实施前搜索仓库与部署说明；发现仓库外消费者时触发停止条件，先形成版本策略 |
| 前端为通过编译加入强制断言 | 合同测试审查 `as unknown as`、非空断言和手写镜像接口，要求删除而非掩盖 |

## 10. 实施步骤

1. 枚举前端所有 API 调用，生成“method/path/请求/成功响应/错误状态”基线表。
2. 修正 Pydantic 泛型、JSON 字段和路由 `response_model/operation_id`，统一分页参数。
3. 定义 ErrorResponse、ValidationErrorResponse、异常映射和错误码注册规则，先迁移本卡触及的端点。
4. 增加不连接外部基础设施的确定性 OpenAPI 导出脚本并提交规范化快照。
5. 引入并锁定 TypeScript 生成器，生成前端合同类型和类型化 client。
6. 按资源迁移页面，删除对应手写接口和调用者声明的成功响应泛型；加入共享 JSON 展示/解析 helper。
7. 为 Candidate、CuratedItem、Dataset items、Benchmark cases 增加后端运行时合同测试和前端 mock 合同测试。
8. 增加“重新生成后 git diff 为空”的 CI 门禁和禁止旧调用模式的静态检查。
9. 更新开发命令、合同变更说明，并按仓库规则写中文 DevLog。

## 11. 自动化验收标准

在 `conda activate DatasetGen` 后，除 T00 的全量门禁外，必须满足：

```powershell
python scripts/export_openapi.py --check
python -m pytest -q tests/contract/test_openapi.py tests/contract/test_core_response_shapes.py
cd apps/web
npm run api:generate
npm run api:check
npm test -- --run api-contract
npm exec tsc -- --noEmit
```

验收断言至少包括：

1. 连续两次生成 OpenAPI 和 TypeScript 产物字节一致，生成后工作树无漂移。
2. OpenAPI 中不存在前端已调用路由的重复/缺失 `operationId`，也不存在未参数化分页响应。
3. Candidate/CuratedItem 的 `content` 在运行时返回 object；生成的 TypeScript 类型不允许赋值为 `string`。
4. Dataset items 与 Benchmark cases 在空集、单页和越界页均返回四键分页对象，`total/page/page_size` 正确。
5. 使用真实 ASGI 响应喂给前端合同 mock 时，相关列表和详情页可渲染，不出现 `.slice is not a function`、React object child 或 `data.length` 异常。
6. 用旧请求字段或错误 JSON 类型调用端点返回 `422`，不会静默接受。
7. 代表性 `401/403/404/409` 均符合 ErrorResponse；字段校验 `422` 符合 ValidationErrorResponse；生成前端类型能以 `code` 判别且不读取中文 `detail`。
8. 未注册 code、`200 {success:false}`、字符串 detail 和响应中的堆栈/Token fixture 会使合同测试失败。
9. 静态检查确认迁移范围内没有手写镜像响应接口、`api.<method><T>`、`any` 或双重类型断言绕过。
10. Ruff、后端全量 pytest、前端 lint、TypeScript、组件测试和 build 均返回 0。

## 12. 停止条件

出现以下任一情况时停止本卡并请求合同或发布决策：

- 发现仓库外正在使用的客户端，且分页/状态码切换无法在当前发布窗口一次完成；
- 同一路由被两个页面依赖为互斥的响应形状，产品方尚未确认规范形态；
- 现有数据库数据不能被明确 schema 序列化，需要数据迁移或业务语义判断；
- 生成 OpenAPI 必须连接真实数据库、密钥或外部服务；
- 为让生成器工作必须升级 FastAPI/Pydantic/Next.js 等核心依赖并造成超出本卡范围的运行时破坏；
- 实施需要用 `Any`、关闭类型检查或长期兼容错误旧字段才能合并。

## 13. 审查重点

- OpenAPI 是否确实来自运行中的应用，而不是另一份人工维护 JSON。
- 后端 schema、运行时响应和生成的前端类型是否三者一致。
- 分页、UUID、时间、枚举和 JSON 字段是否保留真实语义。
- 业务错误是否统一使用可生成类型的 envelope/code，`422` 是否保留可定位字段的独立结构。
- 类型化 client 是否仍完整保留认证刷新、取消请求和错误处理。
- 页面是否删除了手写镜像类型，而非换位置继续复制。
- 合同漂移检查是否能在开发者漏跑生成命令时可靠失败。

## 14. 完成定义

- 本卡范围内所有前端 API 调用均由同一 OpenAPI 产物推导请求和响应类型。
- 已确认的四类运行时失配（生成请求字段、Candidate JSON、CuratedItem JSON、Dataset/Benchmark 分页）能被编译或合同测试提前阻断。
- 后续业务卡可声明稳定错误 code，而无需再发明响应结构；前端不依赖本地化 message/detail 做控制流。
- OpenAPI/TypeScript 生成确定、可重复，并成为 CI required gate。
- 全量自动化门禁通过，没有以断言、`any` 或双合同换取通过。
- 合同变更流程与本地命令已记录，根目录 `DevLog.md` 已用中文记录实施和验证结果。
