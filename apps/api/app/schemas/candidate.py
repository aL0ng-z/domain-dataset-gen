import uuid
from datetime import datetime

from pydantic import Field, field_validator, model_validator

from domain.schemas import BaseSchema, RequestSchema

#: review_evidence_spans 的版本化 bundle schema（任务卡 §4.1）。
REVIEW_EVIDENCE_SCHEMA_VERSION = 1
#: verdict 允许值（review_verdict_enum 对齐）。
REVIEW_VERDICTS = ("supported", "partially_supported", "unsupported", "out_of_scope")


class EvidenceSpan(RequestSchema):
    """单个结构化证据 span：Chunk + 精确原文字符范围（Unicode code point、左闭右开）。

    服务端对不可变 Chunk.content 做精确校验：
    ``0 <= start_char < end_char <= len(chunk.content)`` 且
    ``chunk.content[start_char:end_char] == quote_text``。
    """

    chunk_id: uuid.UUID
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=1)
    quote_text: str

    @model_validator(mode="after")
    def _span_bounds(self):
        if self.start_char >= self.end_char:
            raise ValueError("start_char 必须小于 end_char")
        return self


class CandidateReview(RequestSchema):
    """审核请求：supported/partially_supported 至少一个 span；
    unsupported/out_of_scope 必须提供 reject_reason。客户端不得发送额外 action。"""

    verdict: str
    evidence_spans: list[EvidenceSpan] | None = None
    reject_reason: str | None = None

    @field_validator("verdict")
    @classmethod
    def _verdict_valid(cls, v: str) -> str:
        if v not in REVIEW_VERDICTS:
            raise ValueError(f"verdict 必须为 {REVIEW_VERDICTS} 之一")
        return v

    @model_validator(mode="after")
    def _review_gate(self):
        requires_evidence = self.verdict in ("supported", "partially_supported")
        requires_reason = self.verdict in ("unsupported", "out_of_scope")
        if requires_evidence and not self.evidence_spans:
            raise ValueError("supported/partially_supported 必须提供至少一个证据 span")
        if requires_reason and not (self.reject_reason and self.reject_reason.strip()):
            raise ValueError("unsupported/out_of_scope 必须提供非空拒绝原因")
        return self


class CandidateUpdate(RequestSchema):
    """PATCH 请求仅允许 ``{"content": {"question": "...", "answer": "..."}}``。

    不声明 status/review 字段：editor 直接 PATCH status=approved 会被 extra=forbid
    拒绝为 422（任务卡 §5.1、§11 验收标准 6）。
    """

    content: dict | None = None


class CandidateResponse(BaseSchema):
    id: uuid.UUID
    generation_run_id: uuid.UUID
    chunk_id: uuid.UUID
    content: dict
    candidate_type: str
    status: str
    reviewed_by: uuid.UUID | None
    review_verdict: str | None
    review_evidence_spans: dict | None
    reject_reason: str | None
    created_at: datetime
    updated_at: datetime


class CandidateCommentCreate(RequestSchema):
    content: str


class CandidateCommentResponse(BaseSchema):
    id: uuid.UUID
    candidate_id: uuid.UUID
    user_id: uuid.UUID
    content: str
    created_at: datetime
