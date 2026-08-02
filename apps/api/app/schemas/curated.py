import uuid
from datetime import datetime

from domain.schemas import BaseSchema, RequestSchema


class CuratedItemResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    candidate_id: uuid.UUID
    content: dict
    item_type: str
    status: str
    promoted_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class CuratedItemUpdate(RequestSchema):
    content: dict | None = None
    status: str | None = None
    revision_note: str | None = None


class CuratedRevisionResponse(BaseSchema):
    id: uuid.UUID
    curated_item_id: uuid.UUID
    revised_by: uuid.UUID
    content: dict
    revision_note: str | None
    created_at: datetime


class EvidenceLinkResponse(BaseSchema):
    id: uuid.UUID
    curated_item_id: uuid.UUID
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    source_pages: dict | None
    heading_path: str | None
    quote_text: str | None


class AddToDatasetRequest(RequestSchema):
    dataset_id: uuid.UUID


class AddToBenchmarkRequest(RequestSchema):
    benchmark_id: uuid.UUID
