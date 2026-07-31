# T09：Candidate/CuratedItem 证据与审批

- 状态：待实施
- 优先级：P0-Functional
- 建议规模：L
- 直接依赖：T02、T04、T08
- 被依赖：T10、T11

## 1. 目标与背景

建立“生成候选 → 人工核对证据 → 提升正式条目 → reviewer 审批”的强制门禁。JSON 内容必须可安全编辑，证据必须能回到确定 Chunk 和原文字符范围，CuratedItem 状态不能通过通用 PATCH 绕过审批。

## 2. 范围

1. 收敛 Candidate 的审核状态与请求模型，验证 verdict、拒绝原因和结构化证据 span。
2. 将审核通过的 span 原子物化为 EvidenceLink，保存精确 quote 和来源坐标。
3. 防止同一 Candidate 重复提升；提升后创建 CuratedItem 初始 revision。
4. 将内容编辑与状态迁移分离；只有项目 reviewer 可执行 CuratedItem 审批。
5. 完成 Candidate 审核、JSON 编辑、证据选择、CuratedItem 修订/证据查看和审批 UI。
6. 为权限、门禁、并发编辑和证据完整性增加自动化测试。
7. 每次批准显式绑定被批准的 CuratedRevision 和证据快照；后续修订或退审不能改写历史批准事实。

## 3. 明确不做

- 不改变 LLM 生成、批次聚合或 retry/cancel；由 T08、T07 负责。
- 不实现 Dataset/Benchmark 编组或导出；由 T10、T11 负责。
- 不实现跨 Chunk 自动事实核验、语义相似检索、引用自动生成或模型二次裁判。
- 不允许手工新建无 Candidate 来源的 CuratedItem；若产品需要人工原创资产，应另立任务卡并定义来源策略。
- 不在本卡实现已导出条目的内容回写或快照更新。

## 4. 数据库合同

### 4.1 Candidate 与审核

- `Candidate.content` 保持非空 JSONB object；禁止存 JSON 字符串。
- `Candidate.status` 是唯一审核状态源：`ai_generated|human_edited|approved|rejected`；旧 `review_pending` 可兼容读取但新流程不写入。迁移核对后删除重复的 `review_status` 列；若两列历史值冲突，触发停止条件。
- `review_verdict` 仅允许 `supported|partially_supported|unsupported|out_of_scope`。
- `review_evidence_spans` 保存版本化 JSON：`{"schema_version":1,"spans":[...]}`；每个 span 含 `chunk_id/start_char/end_char/quote_text`。
- `reviewed_by`、verdict、evidence bundle、reject reason 和一条 `ReviewRecord(entity_type="candidate")` 在同一事务提交。

### 4.2 CuratedItem、revision 与证据

- `curated_items.candidate_id` 增加唯一约束；并发重复提升只能成功一次。
- CuratedItem 新增 `current_revision INTEGER NOT NULL DEFAULT 1`、`approved_revision_id UUID NULL REFERENCES curated_revisions(id) RESTRICT`、`approval_record_id UUID NULL REFERENCES review_records(id) RESTRICT`、`approved_by UUID NULL`、`approved_at timestamptz NULL`。
- `CuratedRevision` 新增 `version INTEGER NOT NULL`、`content_sha256 CHAR(64) NOT NULL`、`canonicalization_version VARCHAR(50) NOT NULL` 和唯一约束 `(curated_item_id, version)`；提升时写 v1，后续编辑只能 INSERT vN+1，revision 内容是该版本完整 JSON 快照。
- `curated-content-cjson-v1` 明确 UTF-8、Unicode NFC、object key 排序、数字/布尔/null 与空白规则；`content_sha256` 对规范字节计算小写 SHA-256。CuratedRevision 插入后由 trigger 禁止 UPDATE/DELETE，FK 使用 RESTRICT，修订只能新增不能覆写。
- `EvidenceLink` 新增 `start_char INTEGER NOT NULL`、`end_char INTEGER NOT NULL`；增加唯一约束 `(curated_item_id, chunk_id, start_char, end_char)`。
- EvidenceLink 的 `document_id/source_pages/heading_path/quote_text` 全由服务端根据 Chunk 和已验证 span 派生；客户端不能覆盖。
- approval ReviewRecord 必须保存 `entity_revision_id UUID REFERENCES curated_revisions(id) RESTRICT`、`revision_content_sha256 CHAR(64)`、`evidence_snapshot JSONB`、`evidence_sha256 CHAR(64)` 与 `canonicalization_version VARCHAR(50)`；批准记录插入后不可更新/删除。
- `curated-approval-cjson-v1` 对稳定排序的 EvidenceLink id、Chunk id、offset、quote/source hash 与 revision id/content hash 计算 evidence/approval hash；CHECK 约束 hash 格式和 approve action 必填字段。deferred constraint trigger 验证 `approved_revision_id`、`approval_record_id` 属于同一 CuratedItem 且两个 content hash 一致。
- `status=approved` 当且仅当 approved revision/record/reviewer/time 均非空；approve 在同一事务绑定当时的 `current_revision`。`needs_revision` 只清空当前批准指针和元数据，历史 revision、approval record 与 evidence snapshot 保持不可变。
- T10 membership 必须固定加入当时的 `approved_revision_id` 和 `approval_record_id`；后续 needs-revision/reapprove 不得静默把既有 Dataset/Benchmark membership 改到新内容。
- 本卡只开放审批迁移 `draft -> approved` 和 reviewer 的 `approved -> draft`（needs revision）；`exported/deprecated` 保留给 T11 定义，不由本卡端点写入。
- migration 必须有 upgrade/downgrade、历史回填和约束前检查；不得伪造缺失 quote 或自动把无证据旧条目设为 approved。

## 5. API 合同

### 5.1 Candidate 内容与审核

`PATCH /api/candidates/{cid}` 请求仅允许 `{"content":{"question":"...","answer":"..."}}`。

- 只允许项目 editor 以上角色；成功后状态为 `human_edited`，清空旧审核字段并写审计记录。
- 已提升 Candidate 默认不可再修改，返回 `409`；CuratedItem 后续修改走 revision。

`POST /api/candidates/{cid}/review` 请求为：

```json
{
  "verdict": "supported",
  "evidence_spans": [
    {"chunk_id": "uuid", "start_char": 10, "end_char": 28, "quote_text": "精确原文"}
  ],
  "reject_reason": null
}
```

- 仅 reviewer；`supported/partially_supported` 至少一个 span，`unsupported/out_of_scope` 必须有非空 `reject_reason`。
- offset 以 Unicode code point、左闭右开计数；每个 span 必须满足：Chunk 属于 Candidate 的 source batch/项目，`0 <= start < end <= len(chunk.content)`，且 `chunk.content[start:end] == quote_text`。
- span 按 `(chunk_id,start_char,end_char)` 去重并稳定排序；不匹配返回 `422`，状态冲突返回 `409`。
- supported/partially_supported 映射 Candidate.status=`approved`，其余映射 `rejected`；客户端不发送额外 `action` 字段。

`POST /api/candidates/{cid}/promote-to-curated`：仅 reviewer，只有 approved 且证据有效的 Candidate 可提升；成功返回 `201 CuratedItemResponse`，重复提升返回 `409` 并提供已有 item id，不创建第二份记录。

### 5.2 CuratedItem 编辑与审批

`PATCH /api/projects/{pid}/curated-items/{iid}` 请求为 `{"content":{"question":"...","answer":"..."},"revision_note":"修正文案","expected_revision":2}`。

- 请求中不存在 `status`；只允许 editor 修改 draft。`expected_revision` 不等于当前版本返回 `409`，不覆盖他人更新。
- 成功响应包含新 `current_revision`；内容更新和新 revision 原子提交。

`POST /api/projects/{pid}/curated-items/{iid}/review` 请求为 `{action, reason, expected_revision}`：

- `action` 仅 `approve|needs_revision`，仅 reviewer。
- approve 要求 source Candidate approved、至少一个有效 EvidenceLink、content 为非空 object，且 `expected_revision == current_revision`；成功原子写 approved revision/record/reviewer/time，并在 ReviewRecord 冻结证据坐标与 hash。
- needs_revision 要求非空 reason 和匹配的 expected revision，将 approved 条目退回 draft并清空当前批准指针/人/时间；不删除历史 revision、批准记录、evidence 或 T10 已固定到旧批准 revision 的 membership。
- revision/status 竞争返回 `409 CURATED_REVISION_CONFLICT`；重复 approve 只有在相同 revision、相同审批结果和幂等键下才返回同一 approval record，不能追加矛盾审批。
- 新 revision 重新批准后，旧 membership 仍指向旧 approved revision；升级容器内容必须由 T10 显式 remove/re-add，禁止审批端点批量改写容器。

端点业务错误均复用 T04 `ErrorResponse`，OpenAPI `code` 联合至少包含：

| code | 状态 | 适用条件 |
|---|---:|---|
| `CANDIDATE_REVIEW_STATE_CONFLICT` | 409 | Candidate 已处于不允许当前 review/edit 的状态 |
| `CANDIDATE_EVIDENCE_REQUIRED` | 409 | supported/partially_supported 缺少有效证据 |
| `CANDIDATE_ALREADY_PROMOTED` | 409 | Candidate 已有 CuratedItem；context 只返回同项目可见 item id |
| `CURATED_REVISION_CONFLICT` | 409 | expected/current revision 不一致 |
| `CURATED_APPROVAL_GATE_FAILED` | 409 | source、证据、内容或 approval hash 门禁不满足 |
| `CURATED_REVIEW_STATE_CONFLICT` | 409 | 当前状态不允许 approve/needs_revision 或并发 review 已胜出 |

字段类型、offset/quote、JSON schema 等请求校验错误使用 T04 `ValidationErrorResponse` 的 `422`，不再另造字符串 detail。
- 通用 PATCH、editor 或直接 SQL 之外的任何 API 均不得将状态设为 approved。

### 5.3 查询

- Candidate、CuratedItem 响应使用 T04 生成类型，`content` 为 object；CuratedItem 同时返回 `current_revision/approved_revision_id/approval_record_id`。
- `GET .../{iid}/evidence?page&page_size` 返回 `PaginatedResponse[EvidenceLinkResponse]`，字段含精确 offsets 和 quote。
- `GET .../{iid}/revisions?page&page_size` 返回 `PaginatedResponse[CuratedRevisionResponse]`，每项含 version、content、content_sha256、canonicalization_version，按 version DESC 稳定排序。
- 证据和修订不伪装为 CuratedItem 详情内嵌字段；页面显式请求各端点。

## 6. 前端合同

- Candidate 列表/详情用共享 JSON viewer/formatter 展示 `content`，不得对 object 调 `.slice()` 或直接作为 React child。
- Candidate JSON 编辑器以格式化字符串展示，发送前 parse 并校验为 object；解析失败留在本地并显示行列信息，不发 PATCH。
- 审核面板加载源 Chunk；用户选中文本后生成 start/end/quote，可添加、预览、删除多个 span。前端预校验只改善体验，后端仍是权威。
- verdict 改为 unsupported/out_of_scope 时要求拒绝原因；请求体不再发送未声明的 `action` 或字符串 evidence。
- CuratedItem 详情分别加载 item、evidence、revisions；保存发送 object、revision note 和 expected revision。
- reviewer 可见 approve/needs revision 控件；editor 不展示审批按钮。即使 UI 被绕过，后端仍返回 `403`。
- 审批 UI 显示将被批准的 revision，发送 `expected_revision`；详情同时区分当前 draft revision、当前 approved revision 和历史 approval record。
- 条目退审后，已编组容器显示“固定于历史批准 revision”，不得用当前 draft 内容替换其预览。
- `409` 并发冲突时保留本地草稿、展示服务端当前 revision，并提供重新加载；不得静默覆盖。
- 前端按生成的 `error.code` 区分 revision conflict、审批门禁、重复提升和状态冲突；不得匹配中文 message。

## 7. 预期修改面

- `apps/api/app/models/generation.py`、`models/curated.py`、ReviewRecord 关联及 Alembic migration。
- `apps/api/app/schemas/candidate.py`、`curated.py`、共享 JSON/evidence schema。
- `apps/api/app/routers/candidates.py`、`curated_items.py`。
- `apps/api/app/services/candidate_service.py`、`curated_item_service.py`、证据验证/审批策略。
- Candidate 页面、CuratedItem 列表/详情页、JSON editor/evidence selector/revision 组件。
- 后端权限与事务测试、前端组件测试、OpenAPI 生成物、文档和 `docs/logs/dev-log.md`。

## 8. 依赖

- T02：Candidate、Chunk、CuratedItem 与用户项目/角色的对象级授权。
- T04：JSON、分页、枚举及请求响应的生成类型。
- T08：稳定的 GenerationBatch/Run/Chunk 来源链和 Candidate 产出。
- T10 只能消费本卡 `approved` 且证据完整的 CuratedItem；T11 使用 revision/evidence 构建快照。

## 9. 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| Unicode/换行导致字符 offset 不一致 | 统一为 Unicode code point；前端 helper 将 DOM 的 UTF-16 offset 显式换算，并用中英文、emoji、CRLF fixture 验证 |
| 历史 review_status 与 status 冲突 | migration 先生成冲突报告；无法无损映射时停止，不猜测审核结论 |
| 并发提升或审批产生重复资产 | 数据库唯一约束、条件更新和同事务 ReviewRecord |
| 修改 content 后旧证据不再支持 | Candidate 提升后冻结；CuratedItem 每次审批重新检查 evidence，reviewer 对修订内容负责 |
| approved 条目退审使容器静默混入 draft 内容 | membership 固定 approved revision/approval record；退审不重写容器，升级必须显式 remove/re-add |
| revision 或 approval hash 规范漂移 | 固定两个 canonicalization version、共享 golden bytes；版本未知或 hash 不符时拒绝批准/编组 |
| 前端角色隐藏被当成安全措施 | 所有负向角色测试直接调用 API，后端依赖 T02 做最终授权 |

## 10. 实施步骤

1. 固定状态机、EvidenceSpan 和 revision 合同，编写 migration 前数据审计查询。
2. 实施 schema migration、revision/approval canonical hash、不可变/deferred trigger 与回填；空证据或不可验证旧条目保持 draft。
3. 实现 span 精确校验、Candidate review、幂等提升和 EvidenceLink 物化事务。
4. 移除 CuratedItemUpdate.status，加入乐观 revision、approved revision/approval record 指针与 reviewer review action。
5. 参数化 evidence/revisions 查询并重新生成 T04 类型。
6. 完成 Candidate JSON/证据审核 UI 和 CuratedItem 修订/审批 UI。
7. 补齐权限、状态、证据、并发和回滚测试，运行全量门禁并写中文 DevLog。

## 11. 自动化验收标准

1. JSON object 可显示、编辑、保存；字符串/数组/null content 返回 `422`，前端无 object 渲染异常。
2. supported 无 span、拒绝无原因、越界 offset、quote 不匹配、跨项目/批次 Chunk 均失败且不改变 Candidate。
3. 合法审核一次事务写 verdict、bundle、reviewer、状态和 ReviewRecord；模拟 flush 失败时全部回滚。
4. 提升准确写 1 CuratedItem、v1 revision 和每个去重 span 对应的 EvidenceLink；quote、页码、标题、document 均来自服务端。
5. 两个并发提升请求最多一个 `201`，另一个 `409`，数据库只有一个 CuratedItem。
6. editor 直接 PATCH `status=approved` 得到 `422`，调用 review 得到 `403`；reviewer 缺证据 approve 得到 `409/422`。
7. 两个相同 `expected_revision` 并发保存只有一个成功；失败者本地草稿可保留，revision 版本连续且内容正确。
8. approve v2 后 approval record 精确绑定 v2 的 id/content hash 与规范证据 hash；退审、编辑 v3、重新批准后，v2 记录及任何固定 v2 的 membership 均未变化。
9. approve 与并发编辑、两个相反 review action 竞争时最多一个状态迁移成功；不能批准已过期 revision，也不能产生矛盾 active approval。
10. 对 CuratedRevision 或 approve ReviewRecord 直接 UPDATE/DELETE 被数据库拒绝；篡改 content/hash、跨 item revision/record 指针或错误 canonicalization version 无法通过 deferred constraint。
11. 同一 content/evidence golden fixture 在后端、迁移工具和 T10 计算中得到相同小写 SHA-256；修改一个 code point/offset 必然改变 hash。
12. 每个领域失败返回表中稳定 code；字段校验保持结构化 422，前端分支不读取中文 message。
13. evidence/revisions 为空、多页、越界页时分页字段正确；详情页分别加载并渲染。
14. 两项目四角色负向矩阵、T04 漂移检查、Ruff、pytest、lint、TypeScript、组件测试和 build 全部通过。

## 12. 停止条件

- 无法确定历史 `status/review_status` 的权威来源且迁移会改变已审核结论；
- 产品要求允许无证据或仅 partially_supported 的条目直接成为 approved 正式资产；
- Chunk 文本在 Candidate 审核后可原地修改，导致 offset/quote 无法稳定复核，且 T06 未提供不可变版本；
- 发现合法 Candidate 需要跨多个未关联 Chunk 取证，但 source batch 关系不足以证明归属；
- 已有外部客户端依赖通用 PATCH status 或字符串 evidence，尚无版本迁移决定；
- 需要删除、合并历史 CuratedItem 才能添加唯一约束但未获得数据处置批准。
- 无法为历史 CuratedRevision/approval 确定性计算内容或证据 hash，且业务方不接受保持 draft/legacy_unverified。

## 13. 审查重点

- 审批状态是否只能经 reviewer 专用 action 迁移。
- span 是否对不可变原文做精确校验，EvidenceLink 是否服务端派生。
- 提升、revision、审批和 ReviewRecord 是否各自在单一事务中原子提交。
- approved revision、证据快照与 approval record 是否绑定，退审/重批是否可能改写旧 membership 的内容。
- CuratedRevision 和 approval record 是否由数据库禁止改写，canonicalization version/hash 是否可跨 T09/T10/T11 重算。
- JSON 是否从数据库到 UI 始终保持 object，未出现双重编码。
- 数据库唯一约束是否真正兜住并发，而非只依赖前端按钮禁用。

## 14. 完成定义

- Candidate 审核、证据选择、提升、CuratedItem 修订和 reviewer 审批可从 UI 完成。
- 任一 approved CuratedItem 都能复核 source Candidate、明确 approved revision、不可变 approval/evidence snapshot 和至少一个精确 EvidenceLink。
- 被批准的 revision/content hash 与 approval/evidence hash 均版本化且不可变，可供 T10 固定和 T11 独立验证。
- 通用 PATCH、错误角色、跨项目 ID、空证据和并发重复请求均无法绕过门禁。
- T10/T11 可只依赖 approved 状态、revision 和 evidence，无需猜测旧 JSON。
- migration、OpenAPI、自动化门禁和中文 DevLog 全部完成。
