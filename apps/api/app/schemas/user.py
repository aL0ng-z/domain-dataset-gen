import uuid
from datetime import datetime

from domain.schemas import BaseSchema, RequestSchema


class UserResponse(BaseSchema):
    id: uuid.UUID
    username: str
    email: str
    role: str
    is_active: bool
    created_at: datetime


class UserUpdate(RequestSchema):
    username: str | None = None
    email: str | None = None
    role: str | None = None
    is_active: bool | None = None
