import uuid
from datetime import datetime

from pydantic import BaseModel

from domain.schemas import BaseSchema


class ExportRequest(BaseModel):
    export_profile_id: uuid.UUID


class ExportResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID | None
    benchmark_id: uuid.UUID | None
    export_profile_id: uuid.UUID
    format: str
    item_count: int
    snapshot_manifest_id: uuid.UUID
    created_by: uuid.UUID
    created_at: datetime


class SnapshotManifestResponse(BaseSchema):
    id: uuid.UUID
    manifest: dict
    created_at: datetime
