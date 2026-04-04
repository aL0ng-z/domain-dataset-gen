import uuid
from datetime import datetime

from pydantic import BaseModel

from domain.schemas import BaseSchema


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ProjectResponse(BaseSchema):
    id: uuid.UUID
    name: str
    description: str | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ProjectMemberAdd(BaseModel):
    user_id: uuid.UUID
    role: str = "editor"


class ProjectMemberResponse(BaseSchema):
    project_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    joined_at: datetime
