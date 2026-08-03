import uuid
from datetime import datetime

from pydantic import Field

from domain.schemas import BaseSchema, RequestSchema


# --- Dataset ---
class DatasetCreate(RequestSchema):
    name: str
    description: str | None = None


class DatasetUpdate(RequestSchema):
    name: str | None = None
    description: str | None = None


class DatasetResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None
    status: str
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DatasetDetailResponse(DatasetResponse):
    """T10：详情页额外返回准确 item_count 与 composition/finalize 摘要。"""

    item_count: int
    composition_revision: int
    composition_sha256: str
    composition_canonicalization_version: str
    finalized_revision: int | None
    finalized_sha256: str | None
    finalized_canonicalization_version: str | None
    finalized_by: uuid.UUID | None
    finalized_at: datetime | None


class DatasetItemAdd(RequestSchema):
    curated_item_id: uuid.UUID


class DatasetFinalizeRequest(RequestSchema):
    expected_revision: int = Field(ge=0)
    expected_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


# --- Benchmark ---
class BenchmarkCreate(RequestSchema):
    name: str
    description: str | None = None


class BenchmarkUpdate(RequestSchema):
    name: str | None = None
    description: str | None = None


class BenchmarkResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None
    status: str
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class BenchmarkDetailResponse(BenchmarkResponse):
    """T10：详情页额外返回准确 case_count 与 composition/finalize 摘要。"""

    case_count: int
    composition_revision: int
    composition_sha256: str
    composition_canonicalization_version: str
    finalized_revision: int | None
    finalized_sha256: str | None
    finalized_canonicalization_version: str | None
    finalized_by: uuid.UUID | None
    finalized_at: datetime | None


class BenchmarkCaseAdd(RequestSchema):
    curated_item_id: uuid.UUID


class BenchmarkFinalizeRequest(RequestSchema):
    expected_revision: int = Field(ge=0)
    expected_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


# --- Membership 详情（T10 §5.1 固定字段）---


class PinnedRevisionRef(BaseSchema):
    """加入时固定的 approved CuratedRevision 摘要。"""

    id: uuid.UUID
    version: int
    content_sha256: str


class CuratedItemSummaryResponse(BaseSchema):
    """membership 嵌套的 CuratedItem 摘要（至少含 §5.1 固定字段）。

    pinned_content 来自加入时固定的 CuratedRevision.content（JSON object），
    current_status/current_revision 反映当前状态用于前端“退审/新 revision”提示。
    """

    id: uuid.UUID
    item_type: str
    current_status: str
    current_revision: int
    pinned_revision: PinnedRevisionRef
    pinned_content: dict
    approved_at: datetime | None = None


class DatasetItemDetailResponse(BaseSchema):
    id: uuid.UUID
    container_id: uuid.UUID
    curated_item_id: uuid.UUID
    curated_revision_id: uuid.UUID
    curated_revision_sha256: str
    approval_record_id: uuid.UUID
    approval_evidence_sha256: str
    ordinal: int
    created_at: datetime
    curated_item: CuratedItemSummaryResponse


class BenchmarkCaseDetailResponse(BaseSchema):
    id: uuid.UUID
    container_id: uuid.UUID
    curated_item_id: uuid.UUID
    curated_revision_id: uuid.UUID
    curated_revision_sha256: str
    approval_record_id: uuid.UUID
    approval_evidence_sha256: str
    ordinal: int
    created_at: datetime
    curated_item: CuratedItemSummaryResponse
