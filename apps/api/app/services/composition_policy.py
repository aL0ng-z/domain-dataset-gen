"""T10：Dataset/Benchmark 共享资格 policy（任务卡 §5.2/§10.3）。

同一套资格规则由 eligible 查询、add mutation 与 finalize 复核共用，Dataset 与
Benchmark 的差异只有“source Candidate verdict 是否要求 supported”：
- Dataset 资格：CuratedItem.status == approved，approval 指针齐全，且批准记录
  含至少一个有效 EvidenceLink snapshot。
- Benchmark 资格：Dataset 资格全部成立，且固定 approval 对应的 source Candidate
  ``review_verdict == 'supported'``。

本模块是纯查询构造 + 判定函数，不持有 db session；具体执行由 service 组装。
任何新增资格条件必须同时更新 eligible 查询与 :func:`assert_item_eligible`，
避免两套 service 复制漂移（任务卡 §9 风险）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, exists, func, or_, select

from app.models.curated import CuratedItem, CuratedRevision
from app.models.dataset import BenchmarkCase, DatasetItem
from app.models.generation import Candidate
from app.models.review_record import ReviewRecord

#: benchmark 额外要求 source Candidate verdict 为 supported。
REQUIRED_SOURCE_VERDICT = "supported"


class CompositionGateError(Exception):
    """资格/容器状态门禁不满足（映射为 409 稳定 code）。

    ``code`` 为任务卡 §5.3 错误码表中的领域 code：
    COMPOSITION_NOT_DRAFT / COMPOSITION_ITEM_INELIGIBLE / COMPOSITION_MEMBER_EXISTS
    / COMPOSITION_REVISION_CONFLICT / COMPOSITION_FINALIZE_GATE_FAILED /
    COMPOSITION_HASH_INVALID。
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _approval_ok_clause() -> exists:
    """CuratedItem 的审批指针指向一条带非空证据快照的 approve ReviewRecord。

    证据快照必须包含至少一个 EvidenceLink（任务卡 §5.2：`含至少一个有效
    EvidenceLink snapshot`）；空 ``evidence_links`` 列表视为无证据，不可编组。
    """
    return exists(
        select(ReviewRecord.id).where(
            ReviewRecord.id == CuratedItem.approval_record_id,
            ReviewRecord.entity_type == "curated_item",
            ReviewRecord.entity_id == CuratedItem.id,
            ReviewRecord.action == "approve",
            ReviewRecord.evidence_snapshot.is_not(None),
            ReviewRecord.evidence_sha256.is_not(None),
            func.jsonb_array_length(ReviewRecord.evidence_snapshot["evidence_links"]) > 0,
        )
    )


def eligible_base_query(
    *,
    container_type: str,
    container_id: uuid.UUID,
    project_id: uuid.UUID,
    require_supported: bool,
) -> Select:
    """构造 eligible CuratedItemSummary 查询基础。

    排除：其它项目、已加入该容器的 item、非 approved、缺审批指针、无证据快照的
    approve 记录；require_supported=True 时额外排除 source Candidate verdict 非
    supported。结果含 pinned revision 摘要列（LEFT JOIN CuratedRevision），供
    service 直接组装 summary，避免 N+1。page_size 受限（<=50）。
    """
    membership_table = DatasetItem if container_type == "dataset" else BenchmarkCase
    membership_col = (
        DatasetItem.dataset_id if container_type == "dataset" else BenchmarkCase.benchmark_id
    )
    # benchmark 的 supported 门禁作用在固定 approval 对应的 source Candidate 上：
    # CuratedItem.candidate_id 即提升自的 source Candidate（T09 不可变绑定）。
    source_ok = or_(
        CuratedItem.status != "approved",
        exists(
            select(Candidate.id).where(
                Candidate.id == CuratedItem.candidate_id,
                Candidate.review_verdict == REQUIRED_SOURCE_VERDICT,
            )
        ),
    )
    base = (
        select(
            CuratedItem.id,
            CuratedItem.project_id,
            CuratedItem.item_type,
            CuratedItem.status,
            CuratedItem.current_revision,
            CuratedItem.approved_revision_id,
            CuratedItem.approval_record_id,
            CuratedItem.approved_at,
            CuratedItem.created_at,
            CuratedItem.content,
            CuratedRevision.version.label("pinned_version"),
            CuratedRevision.content_sha256.label("pinned_content_sha256"),
        )
        .outerjoin(
            CuratedRevision,
            CuratedRevision.id == CuratedItem.approved_revision_id,
        )
        .where(
            CuratedItem.project_id == project_id,
            CuratedItem.status == "approved",
            CuratedItem.approved_revision_id.is_not(None),
            CuratedItem.approval_record_id.is_not(None),
            _approval_ok_clause(),
            ~exists(
                select(membership_table.id).where(
                    membership_table.curated_item_id == CuratedItem.id,
                    membership_col == container_id,
                )
            ),
        )
    )
    if require_supported:
        base = base.where(source_ok)
    return base


def assert_item_eligible(
    *,
    curated: CuratedItem,
    approval_record: ReviewRecord | None,
    source_candidate: Candidate | None,
    require_supported: bool,
    already_member: bool,
) -> None:
    """add/finalize 前置资格判定（与 eligible 查询同一套规则）。

    预检查询失败信息精确区分，供路由映射稳定 code：
    - 已加入 -> COMPOSITION_MEMBER_EXISTS；
    - 非 approved / 缺指针 / 无证据 / 非 supported -> COMPOSITION_ITEM_INELIGIBLE。
    """
    if already_member:
        raise CompositionGateError("COMPOSITION_MEMBER_EXISTS", "该知识条目已在容器中")
    if curated is None:
        raise CompositionGateError("COMPOSITION_ITEM_INELIGIBLE", "知识条目不存在")
    if curated.status != "approved":
        raise CompositionGateError("COMPOSITION_ITEM_INELIGIBLE", "仅 approved 条目可编组")
    if curated.approved_revision_id is None or curated.approval_record_id is None:
        raise CompositionGateError("COMPOSITION_ITEM_INELIGIBLE", "条目缺少批准 revision/审批记录")
    if approval_record is None or approval_record.evidence_snapshot is None or approval_record.evidence_sha256 is None:
        raise CompositionGateError("COMPOSITION_ITEM_INELIGIBLE", "批准记录缺少证据快照")
    evidence_links = (approval_record.evidence_snapshot or {}).get("evidence_links") or []
    if not evidence_links:
        raise CompositionGateError("COMPOSITION_ITEM_INELIGIBLE", "批准记录证据快照为空")
    if require_supported and (source_candidate is None or source_candidate.review_verdict != REQUIRED_SOURCE_VERDICT):
        raise CompositionGateError(
            "COMPOSITION_ITEM_INELIGIBLE", "source Candidate 评审结论必须为 supported"
        )
