import uuid
from datetime import datetime

from pydantic import BaseModel

from domain.schemas import BaseSchema


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


class CuratedItemUpdate(BaseModel):
    content: dict | None = None
    status: str | None = None


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


class AddToDatasetRequest(BaseModel):
    dataset_id: uuid.UUID


class AddToBenchmarkRequest(BaseModel):
    benchmark_id: uuid.UUID
