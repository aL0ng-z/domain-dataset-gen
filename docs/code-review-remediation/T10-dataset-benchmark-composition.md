# T10：Dataset/Benchmark 编组

- 状态：待实施
- 优先级：P0-Functional
- 建议规模：L
- 直接依赖：T02、T04、T09
- 被依赖：T11

## 1. 目标与背景

使 Dataset 和 Benchmark 能从页面安全编组已审批 CuratedItem，并形成 T11 可冻结的明确 composition revision。列表响应、条目摘要、添加入口、同项目校验、资格门禁、重复请求、并发 ordinal、批准 revision 固定和 finalized 不可变性必须在数据库、API 与前端保持一致。

## 2. 范围

1. 将 items/cases 列表落实为 T04 的参数化分页合同，并返回页面实际需要的 CuratedItem 摘要。
2. 仅允许向同项目 draft 容器添加符合 T09 门禁的 approved CuratedItem。
3. Dataset 接受证据完整的 approved item；Benchmark 额外要求 source Candidate verdict 为 `supported`。
4. 为 membership 与 ordinal 增加数据库唯一约束，以父记录行锁保证并发追加安全。
5. 为两类详情页实现可搜索、可分页的“添加条目/用例”对话框和可靠移除。
6. 收敛重复的添加路径，使所有入口调用同一服务和同一资格策略。
7. 每次 add/remove 原子递增 composition revision 并重算 canonical hash；membership 固定 T09 的 approved revision/approval record。
8. 增加 reviewer finalize action，以 expected revision 关闭 composition；finalized 后数据库和 API 均拒绝原地修改。

## 3. 明确不做

- 不改变 Candidate/CuratedItem 审批与证据规则；由 T09 负责。
- 不实现不可变导出、manifest、对象存储 key 或历史下载；由 T11 负责。
- 不实现拖拽排序、手工 ordinal 编辑、自动重排或 Dataset/Benchmark 之间复制。
- 不实现 taxonomy、难度分层、抽样、去重模型、Benchmark 运行或指标评测。
- 不定义 finalized 后重新打开、派生新版本或发布到外部目录的流程；本卡只定义一次性 finalize 和 finalized 后不可修改。
- 不自动删除历史非法 membership；先报告并按停止条件处理。

## 4. 数据库合同

- `dataset_items` 增加唯一约束 `(dataset_id, curated_item_id)` 与 `(dataset_id, ordinal)`。
- `benchmark_cases` 增加唯一约束 `(benchmark_id, curated_item_id)` 与 `(benchmark_id, ordinal)`。
- 两类 membership 新增非空 `curated_revision_id`、`curated_revision_sha256 CHAR(64)`、`approval_record_id` 和 `approval_evidence_sha256 CHAR(64)`；ID 分别 FK 到加入时 T09 的 `approved_revision_id/approval_record_id` 并使用 RESTRICT，hash 必须复制被引用不可变记录中的对应值。
- deferred constraint trigger 验证 membership 的 revision、approval record 与 CuratedItem 属于同一项目/同一条目，两个保存 hash 与源记录一致；列表与导出只读取该固定 revision，不跟随 CuratedItem 当前内容漂移。
- 两表增加 `created_at timestamptz NOT NULL DEFAULT now()`；API 的添加时间来自 membership，而不是 CuratedItem 创建时间。
- `ordinal` 为正整数。新增项在锁定父 Dataset/Benchmark 行后取当前 `MAX(ordinal)+1`；移除后允许留空洞，不自动重排。
- CuratedItem、CuratedRevision 和 approval record FK 使用限制删除语义；已被编组的批准资产不得因删除或退审而悬空。若现有 FK 行为不同，migration 调整并写 downgrade。
- 加约束前执行只读审计：重复 membership、重复 ordinal、跨项目关联、非 approved item、Benchmark 非 supported source、无 EvidenceLink。任何命中均触发停止条件，不在 migration 中猜测保留哪条。
- Dataset/Benchmark 增加 `composition_revision BIGINT NOT NULL DEFAULT 0`、`composition_sha256 CHAR(64) NOT NULL`、`composition_canonicalization_version VARCHAR(50) NOT NULL DEFAULT 'composition-cjson-v1'`、`finalized_revision BIGINT NULL`、`finalized_sha256 CHAR(64) NULL`、`finalized_canonicalization_version VARCHAR(50) NULL`、`finalized_by UUID NULL`、`finalized_at timestamptz NULL`。
- `composition-cjson-v1` 复用 T09 的 UTF-8/NFC/稳定 JSON 原则，按 `ordinal, membership_id` 排序，并只包含 schema/version、容器 id/type、membership id、ordinal、CuratedItem id、固定 CuratedRevision id/content SHA-256、approval record id/evidence SHA-256；明确排除当前 item 状态、显示名和更新时间等可变字段。空集合也有确定 hash。
- 每次 add/remove 在锁定父行的同一事务中验证 draft，写 membership，递增 revision 并重算 hash；失败事务不得只更新其中一部分。
- `status` 继续使用 `draft|finalized`。finalize 在父行锁下比较 expected revision、重新验证所有固定 approval/evidence/source verdict 及 hash，随后原子写 finalized 状态/revision/hash/canonicalization version/审核人/时间。CHECK 保证 finalized 字段齐全且等于当时 composition 值。
- finalized 父记录及其 membership 由数据库 trigger/受控 service 双重禁止 INSERT/UPDATE/DELETE；本卡不提供 reopen。迁移回填现有 composition 时若无法确定固定 approved revision/approval record，触发停止条件。

## 5. API 合同

### 5.1 详情与列表

- `GET /api/projects/{pid}/datasets/{did}` 响应增加准确 `item_count`。
- `GET /api/projects/{pid}/benchmarks/{bid}` 响应增加准确 `case_count`。
- `GET /api/projects/{pid}/datasets/{did}/items?page&page_size` 返回 `PaginatedResponse[DatasetItemDetailResponse]`。
- `GET /api/projects/{pid}/benchmarks/{bid}/cases?page&page_size` 返回 `PaginatedResponse[BenchmarkCaseDetailResponse]`。
- Dataset/Benchmark 详情还返回 `status/composition_revision/composition_sha256/composition_canonicalization_version/finalized_revision/finalized_sha256/finalized_at`。
- detail item 字段固定为 `id/container_id/curated_item_id/curated_revision_id/curated_revision_sha256/approval_record_id/approval_evidence_sha256/ordinal/created_at/curated_item`；嵌套摘要至少含 `id/item_type/current_status/current_revision/pinned_revision/pinned_content`，其中预览和导出使用 JSON object `pinned_content`。
- 排序固定为 `ordinal ASC, id ASC`；空页也返回 `items/total/page/page_size` 四个键。

### 5.2 可添加项查询

- 新增 `GET .../datasets/{did}/eligible-items?query&page&page_size` 与 `GET .../benchmarks/{bid}/eligible-items?query&page&page_size`。
- 只返回 URL 项目内、尚未加入该容器且满足对应资格的 CuratedItemSummary；`query` 对受控的标题/内容摘要字段搜索，不拼接原始 SQL。
- Dataset 资格：`CuratedItem.status == approved`、`approved_revision_id/approval_record_id` 完整且批准记录含至少一个有效 EvidenceLink snapshot。
- Benchmark 资格：Dataset 资格全部成立，且固定 approval 对应的 source Candidate `review_verdict == supported`。

### 5.3 添加与移除

- `POST .../datasets/{did}/items` 请求 `{ "curated_item_id": "uuid" }`，新建成功返回 `201 DatasetItemDetailResponse`。
- `POST .../benchmarks/{bid}/cases` 请求相同，返回 `201 BenchmarkCaseDetailResponse`。
- 容器/item 不存在或不属于 pid 返回 `404`；容器 finalized、资格不满足或归属冲突返回 `409`；重复 membership 返回 `409`；错误 UUID/body 返回 `422`。
- `DELETE .../items/{item_id}` 与 `DELETE .../cases/{case_id}` 只删除同时属于 URL 容器的 membership，成功 `204`；错误父子组合返回 `404`。
- 新增 `POST .../datasets/{did}/finalize` 与 Benchmark 等价端点，请求 `{ "expected_revision": 7, "expected_sha256": "..." }`；仅 reviewer，成功 `200` 返回 finalized 容器摘要。revision/hash 已变化返回 `409 COMPOSITION_REVISION_CONFLICT`，资格失效返回稳定 `409`，重复相同 finalize 幂等返回当前结果。
- 所有 mutation 仅 editor 以上角色；viewer 可读；项目角色和对象归属由 T02 的 resolver 同时验证。
- `curated-items/{iid}/add-to-dataset|add-to-benchmark` 等重复入口应删除；若存在已确认外部消费者，只能暂时委托上述同一 policy/service，并标注弃用截止版本，不保留另一套校验逻辑。

端点业务错误统一使用 T04 `ErrorResponse`，OpenAPI `code` 联合至少包含：

| code | 状态 | 适用条件 |
|---|---:|---|
| `COMPOSITION_NOT_DRAFT` | 409 | finalized 容器收到 add/remove/finalize 之外的重复 mutation |
| `COMPOSITION_ITEM_INELIGIBLE` | 409 | approved revision、证据或 Benchmark supported 门禁不满足 |
| `COMPOSITION_MEMBER_EXISTS` | 409 | 同一 CuratedItem 已在容器中 |
| `COMPOSITION_REVISION_CONFLICT` | 409 | finalize 的 expected revision/hash 已过期 |
| `COMPOSITION_FINALIZE_GATE_FAILED` | 409 | finalize 重验发现 membership/hash/资格不一致 |
| `COMPOSITION_HASH_INVALID` | 409 | 保存 hash、canonicalization version 或引用源 hash 无法复核 |

请求字段/UUID/分页边界错误继续使用 T04 `ValidationErrorResponse` 的 `422`；前端不得匹配中文 message。

## 6. 前端合同

- Dataset/Benchmark 详情页只消费生成的分页类型，不再声明 `created_at/content_preview/item_type` 等服务端没有的手写字段。
- 内容预览由共享 JSON formatter 从 membership 的 `pinned_content` 生成；当前 CuratedItem 已退审或产生新 revision 时显示提示，但不得把当前 draft 内容替换进容器。
- “添加条目/添加用例”按钮打开对话框，调用对应 eligible endpoint，支持搜索、分页、空态、选中和单项提交。
- 提交期间禁用对应行；成功后关闭或保留对话框并刷新列表、eligible 列表和 count。`409` 显示可理解原因并刷新，不乐观插入重复行。
- viewer 不显示添加/移除控件；editor 在 finalized 容器中看到只读说明。后端授权仍是权威。
- reviewer 在 draft 容器可见 finalize，确认框展示 item count、composition revision/hash；提交期间若发生 add/remove，按服务端 409 刷新并要求重新确认。
- 前端按生成的 `error.code` 区分重复成员、资格失败、revision 冲突、finalize 门禁与 hash 异常；hash 异常不得允许用户忽略后继续 finalize。
- 移除使用明确确认，成功后若当前页变空且 `page > 1`，回退一页；失败不改变本地列表。
- Dataset 和 Benchmark 共用选择器/分页状态组件，但资格说明分别显示，避免把 partially_supported item 误示为 Benchmark 可用。

## 7. 预期修改面

- `apps/api/app/models/dataset.py`、Alembic migration。
- `apps/api/app/schemas/dataset.py` 及 CuratedItem summary schema。
- `apps/api/app/routers/datasets.py`、`benchmarks.py`、`curated_items.py`。
- `apps/api/app/services/dataset_service.py`、`benchmark_service.py`，提取共享 composition policy/service。
- Dataset/Benchmark 详情页、共享 eligible item picker 和 JSON preview 组件。
- 后端集成/并发/授权测试、前端组件测试、T04 生成物、文档和 `docs/logs/dev-log.md`。

## 8. 依赖

- T02：URL 项目、容器、membership、CuratedItem 的对象级授权和角色判断。
- T04：分页、嵌套 JSON 响应、生成类型及类型化 client。
- T09：权威 approved 状态、明确 approved revision/approval record、source Candidate verdict 和 EvidenceLink snapshot。
- T11 依赖本卡稳定且受约束的 composition 关系生成不可变快照。

## 9. 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| `MAX(ordinal)+1` 并发重复 | 在同一事务 `SELECT parent FOR UPDATE`，再计算/插入；数据库 ordinal 唯一约束兜底 |
| 两套 service 的资格规则漂移 | 抽取共享 policy，Dataset/Benchmark 仅配置额外 verdict 条件 |
| count 与列表不一致 | count 与过滤查询共享 predicate；mutation 后重新请求，不由前端自行加减作为权威 |
| 迁移遇到历史非法记录 | 先审计并停止，由数据负责人决定修复，migration 不静默删改 |
| eligible 内容搜索拖慢数据库 | 限制 page_size 和搜索字段；需要全文索引时单独评估，不在本卡引入搜索基础设施 |
| add/remove 与 finalize 竞态产生混合快照 | 同一父行锁 + expected revision；提交顺序决定唯一结果，失败方刷新重试 |
| 条目退审/重批导致容器内容漂移 | membership 固定批准 revision/record；升级必须显式 remove/re-add，并产生新 composition revision |
| canonical hash 实现漂移 | 持久化 canonicalization version，固定 composition-cjson-v1/golden fixture，并由 T11 原样消费记录值 |

## 10. 实施步骤

1. 编写历史 composition 审计查询并确认 migration 可安全添加约束。
2. 增加 created_at、固定批准 revision/record/hash、composition canonicalization/revision/hash/finalize 字段、唯一约束/FK/trigger 与 downgrade。
3. 提取同项目、draft、approved、evidence、supported 的共享资格 policy。
4. 定义 canonical composition v1；在锁定父行的事务中实现 append/remove + revision/hash，并收敛旧添加路径。
5. 实现 expected-revision finalize、资格复核和数据库不可变门禁。
6. 实现 detail count、固定 revision 分页响应和两个 eligible endpoints，重新生成 T04 类型。
7. 实现共享添加器及两个详情页的添加、移除、finalize、分页和只读状态。
8. 完成并发、授权、资格与前端测试，运行全量门禁并写中文 DevLog。

## 11. 自动化验收标准

1. Dataset items 与 Benchmark cases 的空集、两页、越界页均返回规范分页对象，排序和 total 正确；详情 count 与数据库一致。
2. eligible 查询排除已添加、非 approved、无 evidence 和跨项目 item；Benchmark 还排除 partially_supported source。
3. Dataset 可添加有证据的 approved/partially_supported item；Benchmark 对同一 item 返回 `409`，supported item 可添加。
4. viewer 添加/移除返回 `403`；editor 跨项目 ID、伪造父子组合返回 `404` 且数据库无变化。
5. 对同一 item 的两个并发添加最多一个 `201`、一个 `409`，只有一条 membership。
6. 对两个不同 item 的并发添加都成功且 ordinal 唯一；重复运行至少 50 轮无唯一键异常泄漏为 `500`。
7. finalized 容器的 add/remove 均返回 `409`；读取不受影响。
8. membership 固定加入时的 approved revision/approval record；条目 needs-revision、编辑、重新批准后，既有容器预览/hash 不变，remove/re-add 才升级。
9. add/remove 每次只递增一次 revision 且 hash 可由数据库成员重算；事务回滚时 revision/hash/membership 全部不变。
10. finalize 与 add/remove 并发至少重复 50 轮：要么 mutation 先完成且旧 expected revision 的 finalize 返回 409，要么 finalize 先完成且 mutation 返回 409；不存在 finalized 后写入或混合 hash。
11. finalized 后直接 ORM/SQL 修改 membership 或 composition 字段被数据库门禁拒绝；T11 可用 finalized revision/hash 稳定读取。
12. 篡改 membership 保存的 revision/evidence hash、换成另一 CuratedItem 的 revision/approval record、使用未知 canonicalization version，均被 constraint/重算门禁拒绝。
13. T09/T10 golden fixture 在数据库 service 与 T11 reader 得到相同 composition SHA-256；改变 pinned content/evidence hash 或 ordinal 必然改变 composition hash。
14. 每个领域失败返回表中稳定 code；字段校验保持结构化 422，前端不匹配中文 message。
15. 删除 membership 不删除 CuratedItem/EvidenceLink，错误 container + item_id 组合不能删除其他容器记录。
16. 前端测试覆盖加载/空态/搜索分页/添加/重复 409/移除/页回退/viewer/finalize/退审后固定预览；不出现 `data.length` 或 JSON object 渲染错误。
17. migration 往返、T02 安全矩阵、T04 漂移检查、Ruff、pytest、lint、TypeScript、组件测试和 build 全部通过。

## 12. 停止条件

- 历史审计发现重复、跨项目、非 approved、无证据或 Benchmark 非 supported 的 membership，且数据负责人尚未决定保留/移除策略；
- 产品要求 finalized 容器仍可原地修改，但尚未定义版本和已导出快照语义；
- 无法为存量 membership 确定当时获批的 CuratedRevision/approval record，或产品不接受显式 legacy/unverified 处置；
- 存在仓库外调用方依赖 CuratedItem 侧添加端点，且没有弃用版本/迁移窗口；
- T09 无法提供权威 approved、EvidenceLink 或 source verdict，资格查询只能猜测；
- 数据库隔离级别或部署代理不支持父行锁语义，需要改用全新序列/排序设计；
- 产品要求本卡同时支持拖拽排序、批量导入或跨项目复制，导致合同明显扩张。

## 13. 审查重点

- 同项目与资格校验是否在单次数据库事务内、在插入前完成。
- 唯一约束和父行锁是否同时存在并正确处理 IntegrityError 为 `409`。
- Dataset 与 Benchmark 的差异是否只有明确的 supported 门禁，没有复制漂移。
- 列表 schema 是否真实包含页面消费字段，时间是否来自 membership。
- 旧添加入口是否已删除或确实委托同一 policy，不存在旁路。
- 前端是否只展示 eligible 项并正确处理服务端竞态拒绝。
- composition revision/hash 是否与 membership 同事务，finalize 是否用同一父锁和 expected revision 形成线性化边界。
- membership 是否始终读取固定批准 revision，退审/重批是否可能静默改变已编组内容。
- membership 保存 hash、T09 不可变源记录与 composition canonicalization version 是否由数据库/服务共同复核。

## 14. 完成定义

- 两类容器均可从 UI 搜索、分页、添加和移除合格 CuratedItem，viewer/finalized 为只读。
- 分页、count、嵌套内容和 created_at 合同与生成类型一致。
- 跨项目、未审批、无证据、Benchmark 非 supported、重复和并发请求均被 API 与数据库约束阻断。
- composition 只有一套权威 policy/service，可供 T11 稳定读取。
- 每个 finalized 容器都有不可变 composition revision/hash，且所有 membership 固定明确 approved revision/approval record。
- composition hash 的规范版本与全部固定输入 hash 均已持久化，可由 T11 独立重算而不读取当前 CuratedItem。
- migration、OpenAPI、自动化门禁和中文 DevLog 全部完成。
