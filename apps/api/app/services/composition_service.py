"""T10：Dataset/Benchmark composition 服务（任务卡 §10.4-§10.5）。

唯一权威的编组入口：eligible 查询、add/remove、finalize 共用
:mod:`app.services.composition_policy` 的同一套资格规则与稳定错误码。

事务边界（数据库合同 §4）：
- 每次 add/remove 在锁定父 Dataset/Benchmark 行（``SELECT ... FOR UPDATE``）的
  同一事务内验证 draft、写 membership、递增 composition_revision 并重算
  composition-cjson-v1 hash；失败事务不得只更新其中一部分。
- ``MAX(ordinal)+1`` 在父行锁内计算；数据库 ordinal 唯一约束兜底并发。
- finalize 在父行锁下比较 expected revision/hash、重新验证全部固定
  approval/evidence/source verdict 及保存 hash，随后原子写 finalized 状态/
  revision/hash/canonicalization version/审核人/时间。
- 本 service 不做 ``db.commit()``；路由在同一 db session 完成授权检查后调用
  并统一提交，保证授权与写入无 TOCTOU 窗口。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curated import CuratedItem, CuratedRevision
from app.models.dataset import Benchmark, BenchmarkCase, Dataset, DatasetItem
from app.models.generation import Candidate
from app.models.review_record import ReviewRecord
from app.services.composition_policy import (
    CompositionGateError,
    assert_item_eligible,
    eligible_base_query,
)
from domain.composition import (
    COMPOSITION_CJSON_VERSION,
    composition_sha256,
)

#: 容器类型 -> ORM 模型 / membership 模型 / membership 容器外键。
_MODEL_BY_TYPE = {"dataset": Dataset, "benchmark": Benchmark}
_MEMBERSHIP_BY_TYPE = {"dataset": DatasetItem, "benchmark": BenchmarkCase}
_CONTAINER_COL_BY_TYPE = {"dataset": DatasetItem.dataset_id, "benchmark": BenchmarkCase.benchmark_id}


class CompositionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # 共享内部：容器行锁、membership 查询、资格数据加载、hash 重算
    # ------------------------------------------------------------------

    async def _lock_container(self, container_type: str, container_id: uuid.UUID):
        """锁定父行（FOR UPDATE）并返回容器对象；不存在返回 None。

        ``populate_existing=True`` 避免 identity map 返回加锁前的旧状态
        （T05 确认的 SQLAlchemy 行为，见 DevLog §T05）。
        """
        model = _MODEL_BY_TYPE[container_type]
        result = await self.db.execute(
            select(model)
            .where(model.id == container_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def _load_membership_rows(self, container_type: str, container_id: uuid.UUID) -> list:
        membership_model = _MEMBERSHIP_BY_TYPE[container_type]
        container_col = _CONTAINER_COL_BY_TYPE[container_type]
        result = await self.db.execute(
            select(membership_model)
            .where(container_col == container_id)
            .order_by(membership_model.ordinal, membership_model.id)
        )
        return list(result.scalars().all())

    @staticmethod
    def _membership_hash_rows(memberships: list) -> list[dict]:
        """把 membership ORM 行转换为 composition-cjson-v1 需要的固定字段。"""
        return [
            {
                "membership_id": m.id,
                "ordinal": m.ordinal,
                "curated_item_id": m.curated_item_id,
                "curated_revision_id": m.curated_revision_id,
                "curated_revision_sha256": m.curated_revision_sha256,
                "approval_record_id": m.approval_record_id,
                "approval_evidence_sha256": m.approval_evidence_sha256,
            }
            for m in memberships
        ]

    async def _recompute_hash(self, container: Dataset | Benchmark) -> str:
        container_type = "dataset" if isinstance(container, Dataset) else "benchmark"
        memberships = await self._load_membership_rows(container_type, container.id)
        return composition_sha256(
            container_id=container.id,
            container_type=container_type,
            memberships=self._membership_hash_rows(memberships),
        )

    async def _load_item_eligibility(
        self, curated_item_id: uuid.UUID, require_supported: bool
    ) -> tuple[CuratedItem | None, ReviewRecord | None, Candidate | None]:
        """加载资格判定所需数据（item + 固定 approval record + source candidate）。"""
        curated = (
            await self.db.execute(select(CuratedItem).where(CuratedItem.id == curated_item_id))
        ).scalar_one_or_none()
        if curated is None:
            return None, None, None
        approval_record = None
        if curated.approval_record_id is not None:
            approval_record = (
                await self.db.execute(
                    select(ReviewRecord).where(ReviewRecord.id == curated.approval_record_id)
                )
            ).scalar_one_or_none()
        source_candidate = None
        if require_supported:
            source_candidate = (
                await self.db.execute(
                    select(Candidate).where(Candidate.id == curated.candidate_id)
                )
            ).scalar_one_or_none()
        return curated, approval_record, source_candidate

    # ------------------------------------------------------------------
    # 详情组装（T10 §5.1 固定字段 + pinned_content 预览）
    # ------------------------------------------------------------------

    async def build_membership_details(
        self, container_type: str, container_id: uuid.UUID, memberships: list
    ) -> list[dict]:
        """把 membership 行组装为 §5.1 的固定 detail dict。

        每个 detail 的 ``curated_item.pinned_content`` 来自加入时固定的
        CuratedRevision.content（JSON object），绝不使用当前 CuratedItem.content；
        ``current_status/current_revision`` 反映当前状态供前端“退审/新 revision”提示。
        """
        if not memberships:
            return []
        curated_ids = [m.curated_item_id for m in memberships]
        rev_ids = [m.curated_revision_id for m in memberships]
        curated_result = await self.db.execute(
            select(CuratedItem).where(CuratedItem.id.in_(curated_ids))
        )
        curated_map = {ci.id: ci for ci in curated_result.scalars().all()}
        rev_result = await self.db.execute(
            select(CuratedRevision).where(CuratedRevision.id.in_(rev_ids))
        )
        rev_map = {r.id: r for r in rev_result.scalars().all()}

        details = []
        for m in memberships:
            ci = curated_map.get(m.curated_item_id)
            rev = rev_map.get(m.curated_revision_id)
            if ci is None or rev is None:
                # 固定 revision/approval 不可删除（RESTRICT），此处仅为防御。
                continue
            details.append(
                {
                    "id": m.id,
                    "container_id": container_id,
                    "curated_item_id": m.curated_item_id,
                    "curated_revision_id": m.curated_revision_id,
                    "curated_revision_sha256": m.curated_revision_sha256,
                    "approval_record_id": m.approval_record_id,
                    "approval_evidence_sha256": m.approval_evidence_sha256,
                    "ordinal": m.ordinal,
                    "created_at": m.created_at,
                    "curated_item": {
                        "id": ci.id,
                        "item_type": ci.item_type,
                        "current_status": ci.status,
                        "current_revision": ci.current_revision,
                        "pinned_revision": {
                            "id": rev.id,
                            "version": rev.version,
                            "content_sha256": rev.content_sha256,
                        },
                        "pinned_content": rev.content,
                        "approved_at": ci.approved_at,
                    },
                }
            )
        return details

    # ------------------------------------------------------------------
    # eligible 查询（T10 §5.2）
    # ------------------------------------------------------------------

    async def list_eligible_items(
        self,
        *,
        container_type: str,
        container_id: uuid.UUID,
        project_id: uuid.UUID,
        query: str | None,
        page: int,
        page_size: int,
        require_supported: bool,
    ) -> tuple[list[dict], int]:
        """分页返回可添加的 CuratedItemSummary。

        与 add/finalize 共用 :func:`eligible_base_query`；``query`` 只对受控的
        标题/内容摘要字段做子串搜索（LIKE 转义，不拼接原始 SQL）。摘要的
        pinned_revision/pinned_content 来自加入时固定的 approved revision。
        """
        base = eligible_base_query(
            container_type=container_type,
            container_id=container_id,
            project_id=project_id,
            require_supported=require_supported,
        )

        # query 仅用于受控摘要字段搜索；LIKE 通配符转义，避免注入。
        if query:
            q = query.strip()
            if q:
                escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                base = base.where(
                    or_(
                        CuratedItem.item_type.ilike(pattern, escape="\\"),
                        CuratedItem.content["title"].astext.ilike(pattern, escape="\\"),
                        CuratedItem.content["question"].astext.ilike(pattern, escape="\\"),
                        CuratedItem.content["summary"].astext.ilike(pattern, escape="\\"),
                    )
                )

        count_stmt = select(func.count()).select_from(base.subquery())
        total_result = await self.db.execute(count_stmt)
        total = int(total_result.scalar() or 0)

        offset = (page - 1) * page_size
        rows = (await self.db.execute(base.offset(offset).limit(page_size))).all()

        items = []
        for row in rows:
            items.append(
                {
                    "id": row.id,
                    "item_type": row.item_type,
                    "current_status": row.status,
                    "current_revision": row.current_revision,
                    "pinned_revision": (
                        {
                            "id": row.approved_revision_id,
                            "version": row.pinned_version,
                            "content_sha256": row.pinned_content_sha256,
                        }
                        if row.pinned_version is not None
                        else None
                    ),
                    "pinned_content": row.content,
                    "approved_at": row.approved_at,
                }
            )
        return items, total

    # ------------------------------------------------------------------
    # add / remove
    # ------------------------------------------------------------------

    async def add_membership(
        self,
        *,
        container_type: str,
        container_id: uuid.UUID,
        curated_item_id: uuid.UUID,
        require_supported: bool,
    ) -> DatasetItem | BenchmarkCase:
        """在父行锁内追加一条 membership，原子递增 revision 并重算 hash。

        返回新 membership（已 flush，未 commit）；commit 由路由统一执行。
        """
        container = await self._lock_container(container_type, container_id)
        if container is None:
            raise CompositionGateError("COMPOSITION_NOT_DRAFT", "容器不存在")
        if container.status != "draft":
            raise CompositionGateError("COMPOSITION_NOT_DRAFT", "已 finalize 的容器不可修改")

        curated, approval_record, source_candidate = await self._load_item_eligibility(
            curated_item_id, require_supported
        )
        # 已加入检测在父行锁内进行（并发追加同一 item 只有一个能通过）。
        membership_model = _MEMBERSHIP_BY_TYPE[container_type]
        container_col = _CONTAINER_COL_BY_TYPE[container_type]
        exists_result = await self.db.execute(
            select(membership_model.id).where(
                container_col == container_id,
                membership_model.curated_item_id == curated_item_id,
            )
        )
        already = exists_result.scalar_one_or_none() is not None

        assert_item_eligible(
            curated=curated,
            approval_record=approval_record,
            source_candidate=source_candidate,
            require_supported=require_supported,
            already_member=already,
        )

        # 父行锁内取 MAX(ordinal)+1（唯一约束兜底并发）。
        max_result = await self.db.execute(
            select(func.coalesce(func.max(membership_model.ordinal), 0)).where(
                container_col == container_id
            )
        )
        next_ordinal = int(max_result.scalar() or 0) + 1

        membership = membership_model(
            **{
                _CONTAINER_COL_BY_TYPE[container_type].key: container_id,
                "curated_item_id": curated_item_id,
                "ordinal": next_ordinal,
                "curated_revision_id": curated.approved_revision_id,
                "curated_revision_sha256": self._revision_content_sha256(curated.approved_revision_id),
                "approval_record_id": curated.approval_record_id,
                "approval_evidence_sha256": approval_record.evidence_sha256,
            }
        )
        self.db.add(membership)
        await self.db.flush()

        container.composition_revision += 1
        container.composition_sha256 = await self._recompute_hash(container)
        container.composition_canonicalization_version = COMPOSITION_CJSON_VERSION
        await self.db.flush()
        await self.db.refresh(membership)
        return membership

    async def _revision_content_sha256(self, revision_id: uuid.UUID) -> str:
        """读取固定 CuratedRevision 的 content_sha256（不可变记录，直接复制）。"""
        result = await self.db.execute(
            select(CuratedRevision.content_sha256).where(CuratedRevision.id == revision_id)
        )
        sha = result.scalar_one_or_none()
        if sha is None:
            raise CompositionGateError("COMPOSITION_HASH_INVALID", "固定 revision 缺失，无法保存 hash")
        return sha

    async def remove_membership(
        self,
        *,
        container_type: str,
        container_id: uuid.UUID,
        membership_id: uuid.UUID,
    ) -> bool:
        """删除同时属于该容器的 membership（错误父子组合由路由 scoped load 拦截）。

        成功后移除允许留空洞、不自动重排；每次 remove 原子递增 revision 并重算 hash。
        """
        container = await self._lock_container(container_type, container_id)
        if container is None:
            raise CompositionGateError("COMPOSITION_NOT_DRAFT", "容器不存在")
        if container.status != "draft":
            raise CompositionGateError("COMPOSITION_NOT_DRAFT", "已 finalize 的容器不可修改")

        membership_model = _MEMBERSHIP_BY_TYPE[container_type]
        container_col = _CONTAINER_COL_BY_TYPE[container_type]
        result = await self.db.execute(
            select(membership_model).where(
                membership_model.id == membership_id,
                container_col == container_id,
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            return False

        await self.db.delete(membership)
        await self.db.flush()

        container.composition_revision += 1
        container.composition_sha256 = await self._recompute_hash(container)
        container.composition_canonicalization_version = COMPOSITION_CJSON_VERSION
        await self.db.flush()
        return True

    # ------------------------------------------------------------------
    # finalize
    # ------------------------------------------------------------------

    async def finalize(
        self,
        *,
        container_type: str,
        container_id: uuid.UUID,
        expected_revision: int,
        expected_sha256: str,
        reviewer_id: uuid.UUID,
    ) -> Dataset | Benchmark:
        """expected-revision finalize：父行锁 + 全量资格复核 + 原子写 finalized。

        幂等：若已 finalized 且 revision/hash 与期望一致，直接返回当前结果。
        """
        container = await self._lock_container(container_type, container_id)
        if container is None:
            raise CompositionGateError("COMPOSITION_NOT_DRAFT", "容器不存在")

        # 幂等：重复相同 finalize 返回当前结果（任务卡 §5.3）。
        if container.status == "finalized":
            if (
                container.finalized_revision == expected_revision
                and container.finalized_sha256 == expected_sha256
            ):
                return container
            raise CompositionGateError(
                "COMPOSITION_REVISION_CONFLICT", "容器已 finalize，且与期望 revision/hash 不一致"
            )

        if container.composition_revision != expected_revision or container.composition_sha256 != expected_sha256:
            raise CompositionGateError(
                "COMPOSITION_REVISION_CONFLICT",
                "composition revision/hash 已过期，请刷新后重新确认",
            )

        # 重验全部固定 approval/evidence/source verdict 与保存 hash。
        memberships = await self._load_membership_rows(container_type, container.id)
        require_supported = container_type == "benchmark"
        for m in memberships:
            curated, approval_record, source_candidate = await self._load_item_eligibility(
                m.curated_item_id, require_supported
            )
            try:
                assert_item_eligible(
                    curated=curated,
                    approval_record=approval_record,
                    source_candidate=source_candidate,
                    require_supported=require_supported,
                    already_member=False,
                )
            except CompositionGateError as exc:
                raise CompositionGateError(
                    "COMPOSITION_FINALIZE_GATE_FAILED", f"finalize 复核失败：{exc}"
                ) from exc
            # 复核 membership 保存 hash 与源记录一致（含 canonicalization version 已知）。
            if (
                curated.approved_revision_id != m.curated_revision_id
                or curated.approval_record_id != m.approval_record_id
                or m.curated_revision_sha256 != self._revision_content_sha256(m.curated_revision_id)
                or approval_record is None
                or m.approval_evidence_sha256 != approval_record.evidence_sha256
            ):
                raise CompositionGateError(
                    "COMPOSITION_FINALIZE_GATE_FAILED",
                    "membership 保存 hash 与源记录不一致",
                )

        # 原子写 finalized 状态（数据库 CHECK 保证字段齐全且等于当时 composition 值）。
        container.status = "finalized"
        container.finalized_revision = container.composition_revision
        container.finalized_sha256 = container.composition_sha256
        container.finalized_canonicalization_version = container.composition_canonicalization_version
        container.finalized_by = reviewer_id
        container.finalized_at = datetime.now(UTC)
        await self.db.flush()
        await self.db.refresh(container)
        return container
