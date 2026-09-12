import uuid
from datetime import datetime

from pydantic import Field, field_validator, model_validator

from domain.schemas import BaseSchema, RequestSchema

CURATED_REVIEW_ACTIONS = ("approve", "needs_revision")


class CuratedItemResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    candidate_id: uuid.UUID
    content: dict
    item_type: str
    status: str
    promoted_by: uuid.UUID
    # T09：乐观修订 + 审批指针（approved 当且仅当以下四者均非空）。
    current_revision: int
    approved_revision_id: uuid.UUID | None
    approval_record_id: uuid.UUID | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CuratedItemUpdate(RequestSchema):
    """编辑请求：只允许 editor 修改 draft；请求中不存在 status（422）。

    含 expected_revision 乐观锁：不等于当前版本返回 409 CURATED_REVISION_CONFLICT，
    不覆盖他人更新。
    """

    content: dict
    revision_note: str | None = None
    expected_revision: int = Field(ge=1)



class CuratedItemReview(RequestSchema):
    """审批请求：action 仅 approve|needs_revision，仅 reviewer。

    approve 绑定当前 revision（expected_revision == current_revision）与证据快照；
    needs_revision 要求非空 reason，退回 draft 并清空当前批准指针（历史不可变）。
    """

    action: str
    reason: str | None = None
    expected_revision: int = Field(ge=1)

    @field_validator("action")
    @classmethod
    def _action_valid(cls, v: str) -> str:
        if v not in CURATED_REVIEW_ACTIONS:
            raise ValueError(f"action 必须为 {CURATED_REVIEW_ACTIONS} 之一")
        return v

    @model_validator(mode="after")
    def _needs_revision_reason(self):
        if self.action == "needs_revision" and not (self.reason and self.reason.strip()):
            raise ValueError("needs_revision 必须提供非空 reason")
        return self


class CuratedRevisionResponse(BaseSchema):
    id: uuid.UUID
    curated_item_id: uuid.UUID
    revised_by: uuid.UUID
    version: int
    content: dict
    content_sha256: str
    canonicalization_version: str
    revision_note: str | None
    created_at: datetime


class EvidenceLinkResponse(BaseSchema):
    id: uuid.UUID
    curated_item_id: uuid.UUID
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    start_char: int
    end_char: int
    source_pages: list[int] | dict | None
    heading_path: str | None
    quote_text: str | None
