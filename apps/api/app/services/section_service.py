import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.section import Section, SectionComment, SectionLease, SectionRevision

LEASE_TTL_SECONDS = 300


class SectionLeaseHeldError(Exception):
    """该 Section 当前被其他用户持有有效租约。"""


class SectionLeaseLostError(Exception):
    """请求的 lease 不存在/已释放/已过期/不属于当前用户。"""


class SectionVersionConflictError(Exception):
    """期望 revision 与服务端当前 revision 不一致。"""

    def __init__(self, current_revision: int):
        super().__init__("内容版本已变化")
        self.current_revision = current_revision


class SectionService:
    def __init__(self, db: AsyncSession, redis_client: aioredis.Redis | None = None):
        self.db = db
        self.redis = redis_client

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    async def get_section(self, section_id: uuid.UUID) -> Section | None:
        result = await self.db.execute(
            select(Section)
            .where(Section.id == section_id)
            .options(selectinload(Section.lease))
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # 租约：数据库为正确性来源，Redis 仅缓存（值携带 lease_id）。
    # ------------------------------------------------------------------

    def _redis_key(self, section_id: uuid.UUID) -> str:
        return f"section_lease:{section_id}"

    async def _redis_set_lease(self, lease: SectionLease) -> None:
        """写缓存失败仅降级，绝不抛错阻塞数据库路径。"""
        if not self.redis:
            return
        with contextlib.suppress(Exception):  # noqa: BLE001 - Redis 故障不阻断数据库正确性
            await self.redis.set(
                self._redis_key(lease.section_id),
                str(lease.id),
                ex=LEASE_TTL_SECONDS,
            )

    async def _redis_release_lease(self, section_id: uuid.UUID, lease_id: uuid.UUID) -> None:
        """compare-and-delete：只有当缓存值仍等于 lease_id 时才删除，避免误删后来者。"""
        if not self.redis:
            return
        with contextlib.suppress(Exception):  # noqa: BLE001 - Redis 故障不阻断数据库正确性
            lua = """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """
            await self.redis.eval(lua, 1, self._redis_key(section_id), str(lease_id))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    async def acquire_lease(self, section_id: uuid.UUID, user_id: uuid.UUID) -> SectionLease:
        """数据库事务内的原子租约获取；每个 Section 至多一条未释放租约。

        算法（任务卡 §4.2）：
        1. SELECT sections ... FOR UPDATE 锁定目标 Section；
        2. 将该 Section 中 released_at IS NULL 且已过期的租约标记为释放；
        3. 若同用户已有有效租约，幂等返回原租约；若属其他用户返回冲突；
        4. 否则插入新租约；部分唯一索引作为并发请求的最终保护。
        """
        section = (
            await self.db.execute(
                select(Section)
                .where(Section.id == section_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if section is None:
            return None  # type: ignore[return-value]  # 由路由统一 404

        now = self._now()
        # 2. 过期未释放的租约视为已释放。
        await self.db.execute(
            update(SectionLease)
            .where(
                SectionLease.section_id == section_id,
                SectionLease.released_at.is_(None),
                SectionLease.expires_at <= now,
            )
            .values(released_at=now)
        )

        # 3. 同用户有效租约幂等返回；他人租约冲突。
        # populate_existing 强制刷新 identity map，避免读到加锁前缓存的旧状态。
        result = await self.db.execute(
            select(SectionLease)
            .where(
                SectionLease.section_id == section_id,
                SectionLease.released_at.is_(None),
                SectionLease.expires_at > now,
            )
            .execution_options(populate_existing=True)
        )
        active = result.scalars().first()
        if active is not None:
            if active.user_id == user_id:
                # 幂等重试：不新增记录，只顺延过期时间并返回同一 lease_id。
                active.expires_at = now + timedelta(seconds=LEASE_TTL_SECONDS)
                await self.db.flush()
                await self._redis_set_lease(active)
                await self.db.refresh(active)
                return active
            raise SectionLeaseHeldError("该 Section 已被其他用户锁定")

        # 4. 插入新租约；部分唯一索引作为并发请求的最终保护。
        # 极端并发下若两个请求都通过第 3 步检查，唯一索引会拒绝第二个 INSERT；
        # 捕获 IntegrityError 转成 SectionLeaseHeldError，避免泄漏为 500。
        lease = SectionLease(
            section_id=section_id,
            user_id=user_id,
            acquired_at=now,
            expires_at=now + timedelta(seconds=LEASE_TTL_SECONDS),
        )
        self.db.add(lease)
        try:
            await self.db.flush()
        except Exception as exc:  # noqa: BLE001 - 唯一索引兜底并发插入
            from sqlalchemy.exc import IntegrityError

            if isinstance(exc, IntegrityError):
                await self.db.rollback()
                raise SectionLeaseHeldError("该 Section 已被其他用户锁定") from exc
            raise
        await self._redis_set_lease(lease)
        await self.db.refresh(lease)
        return lease

    async def heartbeat_lease(
        self, section_id: uuid.UUID, user_id: uuid.UUID, lease_id: uuid.UUID
    ) -> SectionLease | None:
        """条件心跳：仅更新 id+section_id+user_id+未释放+未过期同时匹配的记录。"""
        now = self._now()
        result = await self.db.execute(
            select(SectionLease).where(
                SectionLease.id == lease_id,
                SectionLease.section_id == section_id,
                SectionLease.user_id == user_id,
                SectionLease.released_at.is_(None),
                SectionLease.expires_at > now,
            )
        )
        lease = result.scalar_one_or_none()
        if lease is None:
            return None

        lease.expires_at = now + timedelta(seconds=LEASE_TTL_SECONDS)
        await self.db.flush()
        await self._redis_set_lease(lease)
        await self.db.refresh(lease)
        return lease

    async def release_lease(
        self, section_id: uuid.UUID, user_id: uuid.UUID, lease_id: uuid.UUID
    ) -> bool:
        """释放只处理请求中的 lease_id；重复释放返回成功，绝不影响其他租约。"""
        now = self._now()
        result = await self.db.execute(
            select(SectionLease).where(
                SectionLease.id == lease_id,
                SectionLease.section_id == section_id,
                SectionLease.user_id == user_id,
                SectionLease.released_at.is_(None),
            )
        )
        lease = result.scalar_one_or_none()
        if lease is None:
            return False
        lease.released_at = now
        await self.db.flush()
        await self._redis_release_lease(section_id, lease_id)
        return True

    async def get_active_lease(self, section_id: uuid.UUID) -> SectionLease | None:
        now = self._now()
        result = await self.db.execute(
            select(SectionLease).where(
                SectionLease.section_id == section_id,
                SectionLease.released_at.is_(None),
                SectionLease.expires_at > now,
            )
        )
        return result.scalars().first()

    # ------------------------------------------------------------------
    # 保存 / 提交：租约 + 版本双校验，修订记录与正文同事务。
    # ------------------------------------------------------------------

    async def _require_lease(
        self, section_id: uuid.UUID, user_id: uuid.UUID, lease_id: uuid.UUID
    ) -> None:
        """校验租约归属、期限；Redis 不可用不放宽数据库校验。"""
        lease = await self.db.execute(
            select(SectionLease).where(
                SectionLease.id == lease_id,
                SectionLease.section_id == section_id,
                SectionLease.user_id == user_id,
                SectionLease.released_at.is_(None),
                SectionLease.expires_at > self._now(),
            )
        )
        if lease.scalar_one_or_none() is None:
            raise SectionLeaseLostError("租约已失效，请重新获取")

    async def update_section(
        self,
        section_id: uuid.UUID,
        cleaned_markdown: str,
        user_id: uuid.UUID,
        expected_revision: int,
        lease_id: uuid.UUID,
    ) -> Section:
        section = (
            await self.db.execute(
                select(Section)
                .where(Section.id == section_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if section is None:
            return None  # type: ignore[return-value]

        # 租约 + 用户 + 期限校验（数据库为准）。
        await self._require_lease(section_id, user_id, lease_id)

        # 版本乐观并发校验。
        if section.content_revision != expected_revision:
            raise SectionVersionConflictError(section.content_revision)

        # 修订记录与正文同事务；content_revision 严格 +1。
        prev = section.cleaned_markdown or section.raw_markdown
        revision = SectionRevision(
            section_id=section_id,
            revised_by=user_id,
            cleaned_markdown=prev,
            from_revision=expected_revision,
            to_revision=expected_revision + 1,
            revision_note="编辑前自动保存",
        )
        self.db.add(revision)

        section.cleaned_markdown = cleaned_markdown
        section.content_revision = expected_revision + 1
        section.cleaned_by = user_id
        section.status = "in_cleaning"
        if section.assignment_status in ("assigned", "returned"):
            section.assignment_status = "in_progress"
        await self.db.flush()
        await self.db.refresh(section)
        return section

    async def submit_for_review(
        self,
        section_id: uuid.UUID,
        user_id: uuid.UUID,
        expected_revision: int,
        lease_id: uuid.UUID,
    ) -> Section:
        section = (
            await self.db.execute(
                select(Section).where(Section.id == section_id).with_for_update()
            )
        ).scalar_one_or_none()
        if section is None:
            return None  # type: ignore[return-value]

        await self._require_lease(section_id, user_id, lease_id)
        if section.content_revision != expected_revision:
            raise SectionVersionConflictError(section.content_revision)

        section.status = "review_pending"
        await self.db.flush()

        # 提交成功后释放该 lease。
        await self.release_lease(section_id, user_id, lease_id)
        await self.db.refresh(section)
        return section

    async def review_section(self, section_id: uuid.UUID, action: str, user_id: uuid.UUID) -> Section | None:
        section = await self.get_section(section_id)
        if section is None:
            return None
        if action == "accept":
            section.status = "accepted"
        elif action == "reject":
            section.status = "rejected"
        else:
            raise ValueError("action must be 'accept' or 'reject'")
        await self.db.flush()
        await self.db.refresh(section)
        return section

    # --- Comments ---
    async def add_comment(
        self, section_id: uuid.UUID, user_id: uuid.UUID, comment_type: str, content: str
    ) -> SectionComment:
        comment = SectionComment(section_id=section_id, user_id=user_id, comment_type=comment_type, content=content)
        self.db.add(comment)
        await self.db.flush()
        await self.db.refresh(comment)
        return comment

    async def list_comments(self, section_id: uuid.UUID) -> list[SectionComment]:
        result = await self.db.execute(
            select(SectionComment).where(SectionComment.section_id == section_id).order_by(SectionComment.created_at)
        )
        return list(result.scalars().all())

    # --- Revisions ---
    async def list_revisions(self, section_id: uuid.UUID) -> list[SectionRevision]:
        result = await self.db.execute(
            select(SectionRevision)
            .where(SectionRevision.section_id == section_id)
            .order_by(SectionRevision.created_at.desc())
        )
        return list(result.scalars().all())

    # --- Assignment ---
    async def bulk_assign(
        self,
        section_ids: list[uuid.UUID],
        assignee_id: uuid.UUID,
        assigner_id: uuid.UUID,
        cleaning_job_id: uuid.UUID | None = None,
    ) -> int:
        if not section_ids:
            return 0
        query = select(Section).where(Section.id.in_(section_ids))
        if cleaning_job_id is not None:
            query = query.where(Section.cleaning_job_id == cleaning_job_id)
        result = await self.db.execute(query)
        sections = list(result.scalars().all())
        now = datetime.now(UTC)
        for s in sections:
            s.assigned_to = assignee_id
            s.assigned_by = assigner_id
            s.assigned_at = now
            # Preserve in_progress if an editor had already started
            if s.assignment_status not in ("in_progress",):
                s.assignment_status = "assigned"
            s.return_reason = None
        await self.db.flush()
        return len(sections)

    async def assign_section(
        self,
        section_id: uuid.UUID,
        assignee_id: uuid.UUID,
        assigner_id: uuid.UUID,
    ) -> Section | None:
        n = await self.bulk_assign([section_id], assignee_id, assigner_id)
        if n == 0:
            return None
        return await self.get_section(section_id)

    async def complete_section(self, section_id: uuid.UUID, user_id: uuid.UUID, is_admin: bool) -> Section | None:
        section = await self.get_section(section_id)
        if section is None:
            return None
        if not is_admin and section.assigned_to and section.assigned_to != user_id:
            raise ValueError("只有被分派者或管理员可以标记完成")
        section.assignment_status = "completed"
        section.completed_at = datetime.now(UTC)
        await self.db.flush()
        await self.db.refresh(section)
        return section

    async def return_section(self, section_id: uuid.UUID, reason: str) -> Section | None:
        section = await self.get_section(section_id)
        if section is None:
            return None
        section.assignment_status = "returned"
        section.return_reason = reason
        section.completed_at = None
        await self.db.flush()
        await self.db.refresh(section)
        return section
