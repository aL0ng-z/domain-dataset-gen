import uuid
from datetime import datetime

from domain.schemas import BaseSchema, RequestSchema


class ChunkSetSummary(BaseSchema):
    """切分集合汇总：冻结配置、来源、hash 与统计（T06 §5）。"""

    id: uuid.UUID
    document_id: uuid.UUID
    version: int
    is_legacy: bool
    status: str
    cleaned_document_version_id: uuid.UUID | None
    cleaned_document_version: int | None = None
    chunk_profile_id: uuid.UUID | None
    chunk_profile_name: str | None = None
    strategy: str | None
    config_json: dict | None
    total_chunks: int
    total_tokens: int
    source_sha256: str | None
    output_sha256: str | None
    splitter_version: str | None
    error_message: str | None
    task_id: uuid.UUID | None
    completed_at: datetime | None
    created_at: datetime
    is_active: bool = False


class ChunkSetListResponse(BaseSchema):
    """分页版本历史（T06 §5 GET chunk-sets）。"""

    items: list[ChunkSetSummary]
    total: int
    page: int
    page_size: int


class ChunkSetDetailResponse(BaseSchema):
    """单个集合详情：冻结配置与汇总。"""

    id: uuid.UUID
    document_id: uuid.UUID
    version: int
    is_legacy: bool
    status: str
    cleaned_document_version_id: uuid.UUID | None
    chunk_profile_id: uuid.UUID | None
    strategy: str | None
    config_json: dict | None
    total_chunks: int
    total_tokens: int
    source_sha256: str | None
    output_sha256: str | None
    splitter_version: str | None
    error_message: str | None
    task_id: uuid.UUID | None
    completed_at: datetime | None
    created_at: datetime
    is_active: bool = False


class ChunkSetRequest(RequestSchema):
    """发起切分请求（T06 §5）：cleaned_version_id 省略时取 Document active。"""

    chunk_profile_id: uuid.UUID
    cleaned_version_id: uuid.UUID | None = None


class ChunkTriggerResponse(BaseSchema):
    """202 切分已接受：task/chunk_set/幂等重放语义。"""

    task_id: uuid.UUID
    chunk_set_id: uuid.UUID
    reused: bool
    status: str
    message: str
