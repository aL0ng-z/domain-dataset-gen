import uuid
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.section import Section, SectionComment, SectionLease, SectionRevision


class SectionService:
    def __init__(self, db: AsyncSession, redis_client: aioredis.Redis | None = None):
        self.db = db
        self.redis = redis_client

    async def get_section(self, section_id: uuid.UUID) -> Section | None:
        result = await self.db.execute(select(Section).where(Section.id == section_id))
        return result.scalar_one_or_none()

    async def update_section(self, section_id: uuid.UUID, cleaned_markdown: str, user_id: uuid.UUID) -> Section | None:
        section = await self.get_section(section_id)
        if section is None:
            return None

        revision = SectionRevision(
            section_id=section_id, revised_by=user_id,
            cleaned_markdown=section.cleaned_markdown or section.raw_markdown,
            revision_note="编辑前自动保存",
        )
        self.db.add(revision)

        section.cleaned_markdown = cleaned_markdown
        section.cleaned_by = user_id
        section.status = "in_cleaning"
        if section.assignment_status == "assigned":
            section.assignment_status = "in_progress"
        await self.db.flush()
        await self.db.refresh(section)
        return section

    async def submit_for_review(self, section_id: uuid.UUID) -> Section | None:
        section = await self.get_section(section_id)
        if section is None:
            return None
        section.status = "review_pending"
        await self.db.flush()
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

    # --- Leases ---
    async def acquire_lease(self, section_id: uuid.UUID, user_id: uuid.UUID) -> SectionLease:
        # Check existing active lease
        if self.redis:
            existing = await self.redis.get(f"lease:section:{section_id}")
            if existing and existing != str(user_id):
                raise ValueError("该 Section 已被其他用户锁定")

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=120)
        lease = SectionLease(section_id=section_id, user_id=user_id, expires_at=expires_at)
        self.db.add(lease)
        await self.db.flush()

        if self.redis:
            await self.redis.set(f"lease:section:{section_id}", str(user_id), ex=120)

        await self.db.refresh(lease)
        return lease

    async def heartbeat_lease(self, section_id: uuid.UUID, user_id: uuid.UUID) -> SectionLease | None:
        result = await self.db.execute(
            select(SectionLease).where(
                SectionLease.section_id == section_id,
                SectionLease.user_id == user_id,
                SectionLease.released_at.is_(None),
            ).order_by(SectionLease.acquired_at.desc())
        )
        lease = result.scalars().first()
        if lease is None:
            return None

        lease.expires_at = datetime.now(timezone.utc) + timedelta(seconds=120)
        await self.db.flush()

        if self.redis:
            await self.redis.set(f"lease:section:{section_id}", str(user_id), ex=120)

        await self.db.refresh(lease)
        return lease

    async def release_lease(self, section_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(SectionLease).where(
                SectionLease.section_id == section_id,
                SectionLease.user_id == user_id,
                SectionLease.released_at.is_(None),
            ).order_by(SectionLease.acquired_at.desc())
        )
        lease = result.scalars().first()
        if lease is None:
            return False

        lease.released_at = datetime.now(timezone.utc)
        await self.db.flush()

        if self.redis:
            await self.redis.delete(f"lease:section:{section_id}")

        return True

    # --- Comments ---
    async def add_comment(self, section_id: uuid.UUID, user_id: uuid.UUID, comment_type: str, content: str) -> SectionComment:
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
            select(SectionRevision).where(SectionRevision.section_id == section_id).order_by(SectionRevision.created_at.desc())
        )
        return list(result.scalars().all())

    # --- Assignment ---
    async def bulk_assign(
        self, section_ids: list[uuid.UUID], assignee_id: uuid.UUID, assigner_id: uuid.UUID,
    ) -> int:
        if not section_ids:
            return 0
        result = await self.db.execute(
            select(Section).where(Section.id.in_(section_ids))
        )
        sections = list(result.scalars().all())
        now = datetime.now(timezone.utc)
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
        self, section_id: uuid.UUID, assignee_id: uuid.UUID, assigner_id: uuid.UUID,
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
        section.completed_at = datetime.now(timezone.utc)
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
