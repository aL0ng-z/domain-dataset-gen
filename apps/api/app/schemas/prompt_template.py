import uuid
from datetime import datetime

from pydantic import BaseModel

from domain.schemas import BaseSchema


class PromptTemplateCreate(BaseModel):
    task_type: str
    name: str
    system_prompt: str
    user_prompt_template: str
    input_schema: dict | None = None
    output_schema: dict | None = None


class PromptTemplateUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    user_prompt_template: str | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None


class PromptTemplateResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    task_type: str
    name: str
    version: int
    system_prompt: str
    user_prompt_template: str
    input_schema: dict | None
    output_schema: dict | None
    is_default: bool
    created_at: datetime
    updated_at: datetime


class PromptTemplateVersionResponse(BaseSchema):
    id: uuid.UUID
    template_id: uuid.UUID
    version: int
    system_prompt: str
    user_prompt_template: str
    input_schema: dict | None
    output_schema: dict | None
    created_at: datetime


class TestRunRequest(BaseModel):
    chunk_id: uuid.UUID
    model_config_id: uuid.UUID


class TestRunResponse(BaseModel):
    input_prompt: str
    output: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
