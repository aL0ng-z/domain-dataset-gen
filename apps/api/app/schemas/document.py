import uuid
from datetime import datetime

from domain.schemas import BaseSchema


class DocumentResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    file_size: int
    sha256: str
    status: str
    page_count: int | None
    uploaded_by: uuid.UUID
    clean_status: str
    active_clean_version_id: uuid.UUID | None
    active_chunk_set_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ParseJobResponse(BaseSchema):
    id: uuid.UUID
    document_id: uuid.UUID
    parser_profile_id: uuid.UUID
    status: str
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class ParseRequest(BaseSchema):
    parser_profile_id: uuid.UUID


class ChunkRequest(BaseSchema):
    chunk_profile_id: uuid.UUID


class GenerateBatchRequest(BaseSchema):
    prompt_template_id: uuid.UUID
    model_config_id: uuid.UUID
