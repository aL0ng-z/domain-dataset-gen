import uuid
from datetime import datetime

from domain.schemas import BaseSchema, RequestSchema


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


class CleaningJobResponse(BaseSchema):
    id: uuid.UUID
    document_id: uuid.UUID
    parse_job_id: uuid.UUID
    status: str
    started_by: uuid.UUID
    parser_profile_id: uuid.UUID
    parser_profile_name: str | None
    parse_completed_at: datetime | None
    created_at: datetime
    completed_at: datetime | None


class ParseRequest(RequestSchema):
    parser_profile_id: uuid.UUID


class CleaningStartRequest(RequestSchema):
    parse_job_id: uuid.UUID | None = None


class CleaningStartResponse(BaseSchema):
    task_id: uuid.UUID | None
    cleaning_job_id: uuid.UUID
    reused: bool
    message: str


class ChunkRequest(RequestSchema):
    chunk_profile_id: uuid.UUID


class GenerateBatchRequest(RequestSchema):
    prompt_template_id: uuid.UUID
    model_config_id: uuid.UUID


class AsyncTaskAcceptedResponse(BaseSchema):
    """202 异步任务已接受的统一响应（无 body 以外的业务负载）。"""

    task_id: uuid.UUID
    message: str


class BulkAssignResponse(BaseSchema):
    assigned: int
