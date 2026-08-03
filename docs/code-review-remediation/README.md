# Code Review 修复任务卡索引

## 1. 目标与使用方式

本目录把 2026-07-31 code review 中确认的问题拆成可独立实施、测试和审查的任务卡。修复工作的最终目标不是“让页面不报错”，而是恢复以下 R1/R1+ 产品不变量：

1. 任一资源访问都绑定真实项目和项目角色；
2. `Document → CleanedDocumentVersion → ChunkSet → GenerationBatch → Candidate → CuratedItem → Dataset/Benchmark → Export` 全链路可追溯；
3. Candidate 只能经人工审核和证据门禁进入正式资产；
4. 历史导出不可覆盖，且可由快照独立验证；
5. 异步任务状态、重试、取消和失败恢复与真实执行一致；
6. 前后端共享同一份可自动校验的 API 合同。

每张卡建议对应一个独立分支和一个独立 PR。PR 只实现该卡“范围”内的内容；遇到“停止条件”必须停止扩展，并记录新的设计决策或拆出后续卡片。

## 2. 任务卡清单

| ID | 任务卡 | 优先级 | 主要产出 | 直接依赖 |
|---|---|---|---|---|
| T00 | [自动化质量基线与测试底座](T00-quality-baseline-and-test-harness.md) | P0 | 可重复的后端、前端、集成测试与 CI 门禁 | 无 |
| T01 | [JWT 语义与前端认证状态](T01-jwt-token-and-frontend-auth-state.md) | P0-Security | access/refresh 严格分离，统一认证状态 | T00 |
| T02 | [项目资源对象级授权](T02-project-resource-authorization.md) | P0-Security | REST、PDF、WebSocket 的项目隔离与项目角色授权 | T00、T01 |
| T03 | [解析器出站与凭证安全](T03-parser-egress-and-credential-security.md) | P0-Security | 阻断 SSRF/凭证外送，冻结 ParseJob 配置 | T00 |
| T04 | [API/前端合同单一事实源](T04-api-frontend-contract-source-of-truth.md) | P0 | OpenAPI 驱动的类型、分页与 JSON 合同 | T00 |
| T05 | [清洗编辑并发与租约](T05-clean-edit-concurrency-and-lease.md) | P1 | 防串写、原子 lease、清洗版本并发发布 | T02、T04 |
| T06 | [版本化切分与 Token 预算](T06-versioned-chunking-and-token-budget.md) | P1 | 不可变 ChunkSet、active set、幂等重跑、Token 上限 | T02、T04、T05、T07 |
| T07 | [任务生命周期、派发、重试与取消](T07-task-lifecycle-dispatch-retry-cancel.md) | P1 | 持久化 payload、合法状态机、真实 retry/cancel | T01、T02、T04 |
| T08 | [生成链路修复](T08-generation-flow-repair.md) | P0-Functional | 单项/批量生成、冻结配置、派生 retry 批次 | T04、T06、T07 |
| T09 | [Candidate/CuratedItem 证据与审批](T09-candidate-curated-evidence-and-approval.md) | P0-Functional | JSON/证据门禁、固定 approved revision | T02、T04、T08 |
| T10 | [Dataset/Benchmark 编组](T10-dataset-benchmark-composition.md) | P0-Functional | 固定成员 revision、composition hash/finalize | T02、T04、T09 |
| T11 | [不可变导出与完整快照](T11-immutable-export-snapshot.md) | P0-Data | 唯一对象、完整版本图和 hash、稳定历史下载 | T03、T06、T07、T08、T09、T10 |

## 3. Code Review 问题闭环映射

| Code review 发现 | 主任务卡 | 验证闭环 |
|---|---|---|
| 自动化回归不足，Ruff/ESLint 尚未形成稳定门禁 | T00 | 可重复测试环境、迁移 smoke test、后端/前端 CI |
| refresh token 可调用业务接口；HTTP、PDF、WebSocket 各自解码 | T01 | token type 负向矩阵、single-flight refresh、统一认证状态 |
| 平铺资源、PDF、WebSocket 可绕过项目或对象级授权 | T02 | 双项目四角色的 REST/PDF/WS 访问矩阵 |
| 用户可控解析器 URL 接收平台凭证和 PDF，形成 SSRF/外送风险 | T03 | allowlist、重定向/DNS 防护、凭证隔离、出站负向测试 |
| Candidate/CuratedItem JSON 与 Dataset/Benchmark 分页等前后端合同漂移 | T04 | OpenAPI 唯一事实源、生成类型和合同差异门禁 |
| 清洗页面迟到响应串写、lease 非原子、合并版本号/对象键并发冲突 | T05 | fencing token、revision CAS、版本发布并发测试、dirty guard |
| ChunkSet 未成为真实版本边界，重跑混入/重复，最终 Chunk 可超 token 预算 | T06 | 不可变 set、active pointer、幂等重跑和 tokenizer golden cases |
| Task retry/cancel 只改状态，进程重启后 payload 丢失，终态可被迟到写覆盖 | T07 | 持久化 dispatcher、attempt/lease/CAS、崩溃恢复与真实取消/重试 |
| 单 Chunk 入口导入失败，单项/批量生成状态与产物不一致 | T08 | 202 + Task、GenerationBatch/Run 聚合、部分失败与取消测试 |
| Candidate 结构化编辑、EvidenceLink 和 reviewer 审批门禁可被绕过 | T09 | JSON schema、证据区间、角色/状态机和并发审批负向测试 |
| Dataset/Benchmark 页面与分页不匹配，添加链路缺失且可跨项目/绕过 approved | T10 | 同项目资格查询、原子 ordinal、添加/移除 UI 与并发矩阵 |
| 导出路径可覆盖历史文件，manifest 未冻结完整版本图和 hash | T11 | export id 唯一对象、完整快照、hash 复核与历史下载回归 |

## 4. 建议实施波次

```text
Wave 0  T00
          |
Wave 1  T01   T03   T04
          |           |
Wave 2  T02
          |
Wave 3  T05   T07
          \   /
Wave 4     T06
            |
Wave 5     T08
            |
Wave 6     T09
            |
Wave 7     T10
            |
Wave 8     T11
```

T03 可在 T01/T02 之外独立实施，但合并后仍需运行 T02 的跨项目安全回归。T05 与 T07 在 T02/T04 完成后可以并行；T06 同时复用 T05 的不可变清洗版本和 T07 的持久任务派发，因此应在两者之后合并。T08 之后的卡片对应 R1 后半段主链，建议按依赖顺序合并，避免临时兼容层长期存在。

## 5. 共同实施约束

- Python 命令必须在 `conda activate DatasetGen` 后执行。
- 每张卡必须同时提交实现、迁移、自动化测试和所需文档；不得把测试留给后续卡片。
- 数据库迁移必须提供 upgrade、downgrade、既有数据回填策略和回滚说明。
- API 变更必须先更新 OpenAPI/Pydantic 合同，再更新生成的前端类型；禁止仅靠 TypeScript 泛型“声明”服务端响应。
- 安全负向测试至少使用两个项目、四种项目角色和 access/refresh 两类 Token。
- 对外部存储的写入必须使用测试专用 bucket/key 前缀，并在测试后清理。
- 任何状态迁移都必须覆盖成功、业务失败、基础设施失败、取消、重试和并发重复请求。
- 修改代码后按仓库要求在根目录 `DevLog.md` 使用中文记录，并使用中文 Conventional Commit 消息。

## 6. 合并与发布门槛

单张任务卡只有在自身“自动化验收标准”和“完成定义”全部满足时才可合并。T11 合并后，才允许重新执行并签署 R1 全链路验收：真实 PDF 上传、解析、清洗、终审、切分、单项/批量生成、Candidate 审核、CuratedItem 审批、Dataset/Benchmark 编组、导出、历史下载和快照复核。
