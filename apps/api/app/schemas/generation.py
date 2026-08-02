import uuid
from datetime import datetime

from domain.schemas import BaseSchema, RequestSchema


class GenerateAcceptedResponse(BaseSchema):
    """202 接收响应：不假装同步返回 Candidate。"""

    task_id: uuid.UUID
    generation_batch_id: uuid.UUID
    status: str = "queued"


class GenerateRequest(RequestSchema):
    """单 Chunk 生成请求（服务端固定选择 [cid]，只发送前两个字段）。"""

    prompt_template_id: uuid.UUID
    model_config_id: uuid.UUID


class GenerateBatchRequest(RequestSchema):
    """批量生成请求；selected_chunk_ids 可省略（省略 = active ChunkSet 全部 ready）。"""

    prompt_template_id: uuid.UUID
    model_config_id: uuid.UUID
    selected_chunk_ids: list[uuid.UUID] | None = None


class GenerationBatchResponse(BaseSchema):
    """批次详情（不返回 credential；快照仅摘要）。"""

    id: uuid.UUID
    document_id: uuid.UUID
    chunk_set_id: uuid.UUID
    model_config_id: uuid.UUID
    prompt_template_id: uuid.UUID
    selected_chunk_ids: list[str]
    status: str
    total_chunks: int
    completed_chunks: int
    summary_json: dict | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    # retry 线性派生链
    retry_of_generation_batch_id: uuid.UUID | None

    # provenance / legacy
    is_legacy: bool
    provenance_status: str
    provenance_error_code: str | None

    # 快照摘要（脱敏：只暴露版本/hash 前缀/renderer，不返回 credential 或完整快照）
    prompt_template_version_id: uuid.UUID | None
    prompt_template_sha256_prefix: str | None
    model_config_sha256_prefix: str | None
    renderer_version: str | None


class GenerationRunResponse(BaseSchema):
    """Run 详情（是否返回完整 input_prompt 受权限与敏感数据策略控制）。"""

    id: uuid.UUID
    chunk_id: uuid.UUID
    generation_batch_id: uuid.UUID | None
    status: str
    context_mode: str
    raw_output: str | None
    error_message: str | None
    input_prompt: str | None
    rendered_prompt_sha256: str | None
    is_legacy: bool
    provenance_status: str
    provenance_error_code: str | None
    created_at: datetime
    completed_at: datetime | None
