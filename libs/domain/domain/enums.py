from enum import Enum


class UserRole(str, Enum):
    admin = "admin"
    reviewer = "reviewer"
    editor = "editor"
    viewer = "viewer"


class DocumentStatus(str, Enum):
    uploaded = "uploaded"
    parsing = "parsing"
    parsed = "parsed"
    cleaning = "cleaning"
    cleaned = "cleaned"
    chunking = "chunking"
    chunked = "chunked"
    generating = "generating"
    generated = "generated"


class SectionStatus(str, Enum):
    draft = "draft"
    in_cleaning = "in_cleaning"
    review_pending = "review_pending"
    accepted = "accepted"
    rejected = "rejected"


class ChunkStatus(str, Enum):
    ready = "ready"
    generating = "generating"
    generated = "generated"


class CandidateStatus(str, Enum):
    ai_generated = "ai_generated"
    human_edited = "human_edited"
    review_pending = "review_pending"
    approved = "approved"
    rejected = "rejected"


class CuratedItemStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    exported = "exported"
    deprecated = "deprecated"


class TaskStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class TaskType(str, Enum):
    parse = "parse"
    clean = "clean"
    chunk = "chunk"
    generate = "generate"
    generate_batch = "generate_batch"
    export = "export"


class CommentType(str, Enum):
    parse_issue = "parse_issue"
    ocr_issue = "ocr_issue"
    layout_issue = "layout_issue"
    general = "general"


class ReviewVerdict(str, Enum):
    supported = "supported"
    partially_supported = "partially_supported"
    unsupported = "unsupported"
    out_of_scope = "out_of_scope"


class ExportFormat(str, Enum):
    sft_jsonl = "sft_jsonl"
    qa_json = "qa_json"
    messages = "messages"
    alpaca = "alpaca"
    sharegpt = "sharegpt"
    benchmark_json = "benchmark_json"


class ContextMode(str, Enum):
    single_chunk = "single_chunk"


class PromptTaskType(str, Enum):
    knowledge_extraction = "knowledge_extraction"
    qa_generation = "qa_generation"
    benchmark_case = "benchmark_case"


class ParseJobStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class CleaningJobStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class DatasetStatus(str, Enum):
    draft = "draft"
    finalized = "finalized"


class BenchmarkStatus(str, Enum):
    draft = "draft"
    finalized = "finalized"


# Role hierarchy for permission checks (higher index = more privilege)
ROLE_HIERARCHY = {
    UserRole.viewer: 0,
    UserRole.editor: 1,
    UserRole.reviewer: 2,
    UserRole.admin: 3,
}
