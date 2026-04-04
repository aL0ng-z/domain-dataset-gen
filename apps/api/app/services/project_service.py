import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project, ProjectMember
from domain.enums import UserRole


class ProjectService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_project(self, name: str, description: str | None, created_by: uuid.UUID) -> Project:
        project = Project(name=name, description=description, created_by=created_by)
        self.db.add(project)
        await self.db.flush()
        # Add creator as project admin
        member = ProjectMember(project_id=project.id, user_id=created_by, role="admin")
        self.db.add(member)
        await self.db.flush()
        await self.db.refresh(project)
        return project

    async def list_projects(self, user_id: uuid.UUID, user_role: str, page: int = 1, page_size: int = 20) -> tuple[list[Project], int]:
        offset = (page - 1) * page_size

        if UserRole(user_role) == UserRole.admin:
            # Admin sees all projects
            count_result = await self.db.execute(select(func.count()).select_from(Project))
            total = count_result.scalar() or 0
            result = await self.db.execute(
                select(Project).order_by(Project.created_at.desc()).offset(offset).limit(page_size)
            )
        else:
            # Others see only projects they are members of
            base = select(Project).join(ProjectMember, Project.id == ProjectMember.project_id).where(ProjectMember.user_id == user_id)
            count_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
            total = count_result.scalar() or 0
            result = await self.db.execute(base.order_by(Project.created_at.desc()).offset(offset).limit(page_size))

        return list(result.scalars().all()), total

    async def get_project(self, project_id: uuid.UUID) -> Project | None:
        result = await self.db.execute(select(Project).where(Project.id == project_id))
        return result.scalar_one_or_none()

    async def update_project(self, project_id: uuid.UUID, **kwargs) -> Project | None:
        project = await self.get_project(project_id)
        if project is None:
            return None
        for key, value in kwargs.items():
            if value is not None:
                setattr(project, key, value)
        await self.db.flush()
        await self.db.refresh(project)
        return project

    async def delete_project(self, project_id: uuid.UUID) -> bool:
        project = await self.get_project(project_id)
        if project is None:
            return False
        await self.db.delete(project)
        await self.db.flush()
        return True

    async def add_member(self, project_id: uuid.UUID, user_id: uuid.UUID, role: str) -> ProjectMember:
        member = ProjectMember(project_id=project_id, user_id=user_id, role=role)
        self.db.add(member)
        await self.db.flush()
        await self.db.refresh(member)
        return member

    async def list_members(self, project_id: uuid.UUID) -> list[ProjectMember]:
        result = await self.db.execute(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        )
        return list(result.scalars().all())

    async def remove_member(self, project_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            return False
        await self.db.delete(member)
        await self.db.flush()
        return True
