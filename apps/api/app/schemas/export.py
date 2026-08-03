import uuid
from datetime import datetime

from pydantic import Field

from domain.schemas import BaseSchema, RequestSchema


class ExportRequest(RequestSchema):
    """T11：导出请求。

    - ``export_profile_id``：导出配置。
    - ``expected_source_revision``：期望的 source composition revision（一致性校验）。
    - ``expected_source_sha256``：期望的 source composition SHA-256。
    - 支持 ``Idempotency-Key``（由路由处理）。
    """

    export_profile_id: uuid.UUID
    expected_source_revision: int = Field(ge=0)
    expected_source_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class ExportResponse(BaseSchema):
    """T11：导出记录响应（列表/详情）。"""

    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID | None
    benchmark_id: uuid.UUID | None
    source_type: str | None
    export_profile_id: uuid.UUID
    format: str
    status: str
    item_count: int | None
    output_sha256: str | None
    file_size: int | None
    integrity_status: str
    task_id: uuid.UUID | None
    retry_count: int
    error_code: str | None
    error_message: str | None
    is_legacy: bool
    created_by: uuid.UUID
    created_at: datetime
    completed_at: datetime | None
    snapshot_manifest_id: uuid.UUID | None


class ExportCreatedResponse(BaseSchema):
    """T11：导出请求创建响应（202）。"""

    export_id: uuid.UUID
    task_id: uuid.UUID
    status: str


class SnapshotManifestResponse(BaseSchema):
    """T11：快照清单响应。

    ``integrity_status``：
    - ``verified``：新格式，manifest_sha256 可验证。
    - ``unverified_legacy``：迁移前旧记录，不伪造完整性。
    """

    id: uuid.UUID
    export_id: uuid.UUID
    manifest: dict
    schema_version: int
    canonicalization_version: str
    manifest_sha256: str | None
    integrity_status: str
    sealed_at: datetime | None
    created_at: datetime


class VerifyShallowResult(BaseSchema):
    db_fields_present: bool
    version_id_present: bool
    metadata_ok: bool | None


class VerifyDeepItem(BaseSchema):
    item: str
    ok: bool
    detail: str | None = None


class ExportVerifyResponse(BaseSchema):
    """T11：浅验证与可选深度验证结果。"""

    export_id: uuid.UUID
    status: str
    shallow: VerifyShallowResult
    deep: list[VerifyDeepItem] | None = None


class ExportDownloadLinkResponse(BaseSchema):
    """经鉴权即时签发的短期对象版本下载链接。"""

    url: str
    expires_at: datetime
    filename: str
