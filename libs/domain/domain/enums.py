from enum import StrEnum


class UserRole(StrEnum):
    admin = "admin"
    reviewer = "reviewer"
    editor = "editor"
    viewer = "viewer"


class DocumentStatus(StrEnum):
    uploaded = "uploaded"
    parsing = "parsing"
    parsed = "parsed"
    cleaning = "cleaning"
    cleaned = "cleaned"
    chunking = "chunking"
    chunked = "chunked"
    generating = "generating"
    generated = "generated"


class SectionStatus(StrEnum):
    draft = "draft"
    in_cleaning = "in_cleaning"
    review_pending = "review_pending"
    accepted = "accepted"
    rejected = "rejected"


class ChunkStatus(StrEnum):
    ready = "ready"
    generating = "generating"
    generated = "generated"


class ChunkSetStatus(StrEnum):
    """切分集合状态（T06 §4.1）。completed/rejected 不可变；failed 可由有效 retry
    run token 转回 pending；cancelled 需以新请求/key 创建新 set。"""

    pending = "pending"
    processing = "processing"
    review_pending = "review_pending"
    completed = "completed"
    rejected = "rejected"
    failed = "failed"
    cancelled = "cancelled"


class CandidateStatus(StrEnum):
    ai_generated = "ai_generated"
    human_edited = "human_edited"
    review_pending = "review_pending"
    approved = "approved"
    rejected = "rejected"


class CuratedItemStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    exported = "exported"
    deprecated = "deprecated"


class TaskStatus(StrEnum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    cancelling = "cancelling"


class TaskType(StrEnum):
    parse = "parse"
    clean = "clean"
    chunk = "chunk"
    generate = "generate"
    generate_batch = "generate_batch"
    export = "export"


class CommentType(StrEnum):
    parse_issue = "parse_issue"
    ocr_issue = "ocr_issue"
    layout_issue = "layout_issue"
    general = "general"


class ReviewVerdict(StrEnum):
    supported = "supported"
    partially_supported = "partially_supported"
    unsupported = "unsupported"
    out_of_scope = "out_of_scope"


class ExportFormat(StrEnum):
    sft_jsonl = "sft_jsonl"
    qa_json = "qa_json"
    messages = "messages"
    alpaca = "alpaca"
    sharegpt = "sharegpt"
    benchmark_json = "benchmark_json"


class ContextMode(StrEnum):
    single_chunk = "single_chunk"


class PromptTaskType(StrEnum):
    knowledge_extraction = "knowledge_extraction"
    qa_generation = "qa_generation"
    benchmark_case = "benchmark_case"


class ParseJobStatus(StrEnum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class CleaningJobStatus(StrEnum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class DatasetStatus(StrEnum):
    draft = "draft"
    finalized = "finalized"


class BenchmarkStatus(StrEnum):
    draft = "draft"
    finalized = "finalized"


# Role hierarchy for permission checks (higher index = more privilege)
ROLE_HIERARCHY = {
    UserRole.viewer: 0,
    UserRole.editor: 1,
    UserRole.reviewer: 2,
    UserRole.admin: 3,
}
