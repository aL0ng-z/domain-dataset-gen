import uuid
from datetime import datetime

from domain.schemas import BaseSchema, RequestSchema


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


class CandidateUpdate(RequestSchema):
    content: dict | None = None


class CandidateReview(RequestSchema):
    verdict: str  # supported / partially_supported / unsupported / out_of_scope
    evidence_spans: dict | None = None
    reject_reason: str | None = None


class CandidateCommentCreate(RequestSchema):
    content: str


class CandidateCommentResponse(BaseSchema):
    id: uuid.UUID
    candidate_id: uuid.UUID
    user_id: uuid.UUID
    content: str
    created_at: datetime
