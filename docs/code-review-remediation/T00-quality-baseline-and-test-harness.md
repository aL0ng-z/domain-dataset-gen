# T00：自动化质量基线与测试底座

- 状态：待实施
- 优先级：P0（所有修复卡的质量前置条件）
- 建议规模：M
- 直接依赖：无
- 可并行：可与 T01、T03 的设计阶段并行；建议先于其代码合并

## 1. 目标与背景

当前后端只有 21 个以解析器和清洗切分为主的测试，鉴权、项目隔离、任务、生成、审核、编组和导出没有自动化回归。前端 TypeScript 检查通过，但 ESLint 有阻断错误，且没有组件/API 合同测试；Ruff 也未形成零新增门禁。

本卡建立所有后续修复可以复用的测试底座和 CI 质量门禁，使每张后续卡都能在独立 PR 中给出可重复证据。

## 2. 范围

1. 明确并脚本化 Python 3.11 `DatasetGen` 环境下的依赖安装和测试入口。
2. 建立三层后端测试：纯单元测试、数据库/API 集成测试、外部服务适配器契约测试。
3. 为 PostgreSQL、Redis、MinIO 提供完全隔离的测试配置、数据库和 bucket/key 前缀。
4. 为 FastAPI 建立登录、双项目、四角色、资源工厂和异步任务测试 fixture。
5. 为前端引入组件测试底座，至少支持 React 页面状态、API mock、请求竞态和错误态测试。
6. 建立 CI，执行后端测试与 lint、前端类型检查与 lint、前端构建和迁移 smoke test。
7. 清理现有 Ruff/ESLint 阻断项，形成“零错误、零新增 warning”基线；行为性问题不得借本卡顺手修复。
8. 增加覆盖率报告，但初始阈值只约束本卡新增的测试基础设施和后续新增/修改代码。

## 3. 明确不做

- 不修复任何已知业务缺陷、授权漏洞或前后端合同错误。
- 不引入 Celery、Kubernetes、云 CI 密钥或生产部署流程。
- 不要求把全部历史代码一次性提升到高覆盖率。
- 不对真实 MinerU、PaddleOCR、LLM 服务发出网络请求。
- 不以大范围 `noqa`、ESLint disable 或降低规则等级代替修复。

## 4. 数据库合同

- 测试数据库必须与开发数据库物理或逻辑隔离，推荐使用独立数据库 `datasetgen_test`；测试启动时若检测到非测试数据库名必须拒绝执行 destructive fixture。
- 每个 API 集成测试使用独立事务或独立 schema，测试结束后自动回滚/清理。
- Alembic 验收至少覆盖：空库 `upgrade head`、`downgrade` 到上一 revision、再次 `upgrade head`。
- 测试 MinIO 使用独立 bucket 或固定 `tests/{run_id}/` 前缀；Redis 使用独立 DB index 或唯一 key namespace。
- 本卡不新增业务表或业务列。

## 5. API 合同

- 提供可复用的 ASGI 测试客户端和认证 helper，能够显式传入 access token、refresh token、无 token 和伪造 token。
- 测试数据工厂至少能创建：两个 Project、admin/reviewer/editor/viewer、ProjectMember、Document 及下游资源。
- 外部解析器、LLM 和对象存储通过依赖注入或 adapter fake 隔离；fake 必须记录调用参数以支持安全断言。
- 本卡不改变现有生产 API 的路径、请求体或响应体。

## 6. 前端合同

- 增加统一的 API mock 层，测试必须按真实 HTTP 状态码和 JSON 响应运行，不允许只 mock 页面内部函数。
- 组件测试支持 fake timers、延迟响应、AbortSignal、401→refresh 和 WebSocket mock。
- 保留 `npm exec tsc -- --noEmit`；新增测试命令必须支持单文件运行和 CI 无交互运行。
- 本卡不改变页面业务交互和服务端响应类型。

## 7. 预期修改面

- `pyproject.toml`、`apps/api/pyproject.toml`
- `tests/conftest.py`、新增 `tests/unit/`、`tests/integration/`、`tests/contract/`
- `infra/docker/` 下测试专用 compose/env 配置或等价测试启动脚本
- `apps/web/package.json`、前端测试配置和 `apps/web/src/**/*.test.*`
- `.github/workflows/` 或仓库实际采用的 CI 目录
- `scripts/` 下统一的测试入口脚本
- `README.md`、相关 runbook、`docs/logs/dev-log.md`

## 8. 依赖

- 外部依赖：Docker Desktop、Node.js/npm、`DatasetGen` conda 环境。
- 后续所有任务卡依赖本卡提供的 fixture、命令和 CI 门禁。
- 若团队不使用 GitHub Actions，可替换 CI 提供方，但命令和门禁语义必须保持一致。

## 9. 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| 测试误连开发数据库或 bucket | 启动时校验数据库名、bucket 前缀和显式 `TESTING=true`；不满足即退出 |
| 外部服务测试不稳定 | 默认全部 fake；真实服务 smoke test 使用显式 marker，永不进入普通 CI |
| 为清 lint 误改业务行为 | 机械修复单独提交；任何行为变化停止并拆到对应业务卡 |
| CI 时间过长 | 单元/合同测试并行；基础设施集成测试单独 job；缓存依赖但不缓存测试数据 |
| Windows 与 CI 行为不同 | PowerShell 和 CI 命令都调用同一 Python/npm 入口，不复制测试逻辑 |

## 10. 实施步骤

1. 固化依赖安装、测试环境变量和一键测试入口。
2. 建立隔离的 PostgreSQL/Redis/MinIO 测试环境及安全校验。
3. 重构 `tests/conftest.py`，加入双项目、角色、Token 和资源 fixture。
4. 加入一个最小 FastAPI 数据库集成测试和一个外部 adapter fake 测试，证明底座可用。
5. 配置前端组件测试框架，加入一个延迟 API 响应与一个 401 mock 测试。
6. 机械清理当前 Ruff/ESLint 阻断项；行为性诊断拆入相应卡片。
7. 配置 CI job 和迁移 smoke test。
8. 更新开发命令、故障排查说明和中文开发日志。

## 11. 自动化验收标准

在仓库根目录执行：

```powershell
conda activate DatasetGen
python -m ruff check apps/api libs tests
python -m pytest -q
```

在前端目录执行：

```powershell
cd apps/web
npm run lint
npm exec tsc -- --noEmit
npm test -- --run
npm run build
```

必须满足：

1. 上述命令均返回 0，且不依赖手工设置 `PYTHONPATH`。
2. 空测试数据库可以完成 Alembic upgrade/downgrade/upgrade smoke test。
3. 测试能够创建两个项目及四种角色，并在结束后没有残留业务数据、Redis key 或 MinIO 对象。
4. 普通测试执行期间没有访问真实 MinerU、PaddleOCR 或 LLM 域名。
5. CI 在全新 checkout 上执行与本地相同的门禁，并上传后端与前端测试报告。
6. 修改文件若没有对应测试或明确的“仅机械变更”说明，CI/审查不得通过。

## 12. 停止条件

出现以下任一情况时停止本卡并请求设计或环境决策：

- 无法为测试取得独立 PostgreSQL、Redis 或 MinIO，存在触碰开发/生产数据的风险；
- 清理某条 lint 错误必须改变业务语义；
- 需要真实外部服务密钥才能让默认测试通过；
- 团队尚未确定 CI 提供方且提交 CI 配置会影响外部组织资源；
- 依赖升级导致运行时 API 破坏，需要超出测试底座的应用重构。

## 13. 审查重点

- 测试隔离是否有强制保护，而不只是文档约定；
- fixture 是否能表达双项目和项目角色，而不是只创建全局 admin；
- fake 是否在正确的 adapter 边界注入；
- CI 与本地命令是否一致；
- lint 清理是否完全机械且没有隐藏业务改动。

## 14. 完成定义

- 所有自动化验收标准通过；
- 后续任务可直接复用双项目、角色、Token、数据库、Redis、MinIO 和前端 API mock fixture；
- CI 是 required gate，不能在失败时合并；
- 测试运行说明和故障排查文档已更新；
- `docs/logs/dev-log.md` 已用中文记录实施和验证结果。
