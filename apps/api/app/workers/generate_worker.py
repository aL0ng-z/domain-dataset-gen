import json
import uuid

from sqlalchemy import select

from app.models.chunk import Chunk
from app.models.config import ModelConfig
from app.models.document import Document
from app.models.generation import Candidate, GenerationRun
from app.models.prompt_template import PromptTemplate
from app.models.task import LlmUsageLog
from app.workers.execution import ExecutionContext
from llm import LLMClient


async def run_generate_single_handler(ctx: ExecutionContext) -> None:
    """generate_single:v1 handler。

    payload: {"chunk_id": UUID, "prompt_template_id": UUID, "model_config_id": UUID}
    外部 LLM 调用前后调用 checkpoint；写入 GenerationRun/Candidate/LlmUsageLog
    在 handler 返回后由 runner 与 completed 转换原子提交。
    """
    payload = ctx.payload
    chunk_id = uuid.UUID(str(payload["chunk_id"]))
    prompt_template_id = uuid.UUID(str(payload["prompt_template_id"]))
    model_config_id = uuid.UUID(str(payload["model_config_id"]))
    project_id = ctx.project_id
    db = ctx.db

    # 项目链复核：chunk/prompt template/model config 必须属于 project_id。
    from app.authz import ProjectChainError, verify_project_chain

    chunk = (await db.execute(select(Chunk).where(Chunk.id == chunk_id))).scalar_one_or_none()
    if chunk is None:
        raise ProjectChainError("Chunk 不存在")
    await verify_project_chain(
        db,
        project_id,
        [
            (Chunk, chunk_id),
            (PromptTemplate, prompt_template_id),
            (ModelConfig, model_config_id),
        ],
        detail="生成任务项目链不一致",
    )
    await ctx.checkpoint()

    template = (
        await db.execute(select(PromptTemplate).where(PromptTemplate.id == prompt_template_id))
    ).scalar_one_or_none()
    model_config = (
        await db.execute(select(ModelConfig).where(ModelConfig.id == model_config_id))
    ).scalar_one_or_none()
    if template is None or model_config is None:
        raise ProjectChainError("生成配置不存在")

    # Build prompt
    user_prompt = template.user_prompt_template.replace("{{content}}", chunk.content)
    user_prompt = user_prompt.replace("{{heading_path}}", chunk.heading_path)

    messages = [
        {"role": "system", "content": template.system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    input_prompt = json.dumps(messages, ensure_ascii=False)

    # Create generation run
    gen_run = GenerationRun(
        chunk_id=chunk_id,
        prompt_template_id=prompt_template_id,
        model_config_id=model_config_id,
        context_mode="single_chunk",
        input_prompt=input_prompt,
        status="processing",
    )
    db.add(gen_run)
    await db.flush()
    await ctx.checkpoint()

    # Call LLM
    client = LLMClient(
        base_url=model_config.base_url,
        api_key=model_config.api_key_encrypted,
        model_name=model_config.model_name,
        temperature=model_config.temperature,
        max_tokens=model_config.max_tokens,
    )
    response = await client.chat_completion(messages, response_format={"type": "json_object"})
    await ctx.checkpoint()

    # Parse output
    try:
        content = json.loads(response.content)
    except json.JSONDecodeError:
        content = {"raw_text": response.content}

    gen_run.raw_output = response.content
    gen_run.status = "completed"

    # Create candidate
    candidate = Candidate(
        generation_run_id=gen_run.id,
        chunk_id=chunk_id,
        content=content,
        candidate_type=template.task_type,
        status="ai_generated",
    )
    db.add(candidate)

    # Log usage
    usage_log = LlmUsageLog(
        project_id=project_id,
        task_id=ctx.task_id,
        model_config_id=model_config_id,
        prompt_template_id=prompt_template_id,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        latency_ms=response.latency_ms,
        status="success",
    )
    db.add(usage_log)

    chunk.status = "generated"
    await db.flush()


async def run_generate_batch_handler(ctx: ExecutionContext) -> None:
    """generate_batch:v1 handler（父任务）。

    payload: {"document_id": UUID, "prompt_template_id": UUID, "model_config_id": UUID}
    对每个 Chunk 创建 generate_single 子任务（父任务提交后由 runner 分别执行）。
    父任务仅在全部子任务 completed 时 completed（聚合由 T08/后续处理，本卡保证
    子任务创建原子性 + 父任务不假 completed：任一子任务失败则父任务 failed）。
    """
    payload = ctx.payload
    document_id = uuid.UUID(str(payload["document_id"]))
    prompt_template_id = uuid.UUID(str(payload["prompt_template_id"]))
    model_config_id = uuid.UUID(str(payload["model_config_id"]))
    project_id = ctx.project_id
    db = ctx.db

    # 项目链复核：doc/prompt template/model config 属于 project_id。
    from app.authz import ProjectChainError, verify_project_chain

    await verify_project_chain(
        db,
        project_id,
        [
            (Document, document_id),
            (PromptTemplate, prompt_template_id),
            (ModelConfig, model_config_id),
        ],
        detail="批量生成任务项目链不一致",
    )
    await ctx.checkpoint()

    result = await db.execute(
        select(Chunk).where(Chunk.document_id == document_id, Chunk.status == "ready").order_by(Chunk.ordinal)
    )
    chunks = list(result.scalars().all())

    if not chunks:
        raise ProjectChainError("没有可用于生成的 Chunk")

    # 创建子任务（generate_single handler）。
    from app.services.task_service import TaskService

    created_by = ctx.payload.get("created_by")
    created_by_uuid = uuid.UUID(str(created_by)) if created_by else None
    task_service = TaskService(db)
    for i, chunk in enumerate(chunks):
        if i % 5 == 0:
            await ctx.checkpoint()
        await task_service.create_task(
            project_id=project_id,
            task_type="generate",
            entity_type="chunk",
            entity_id=chunk.id,
            created_by=created_by_uuid,
            payload={
                "chunk_id": str(chunk.id),
                "prompt_template_id": str(prompt_template_id),
                "model_config_id": str(model_config_id),
            },
            handler="generate_single",
            parent_task_id=ctx.task_id,
        )

    # 聚合：全部子任务进入终态后父任务才 completed；任一子任务 failed/cancelled
    # 则父任务 failed（不假 completed）。尚未全部终态则回队退避轮询。
    from sqlalchemy import func

    from app.models.task import Task as TaskModel
    from app.workers.queue import TERMINAL_STATUSES

    child_status_result = await db.execute(
        select(TaskModel.status, func.count())
        .where(TaskModel.parent_task_id == ctx.task_id)
        .group_by(TaskModel.status)
    )
    status_counts = dict(child_status_result.all())
    total_children = sum(status_counts.values())
    if total_children == 0:
        # 本轮刚创建（尚未提交，本事务内 count 应为 0）——下一轮聚合。
        ctx.requeue_after = 5
        await db.flush()
        return

    terminal_children = sum(
        count for status, count in status_counts.items() if status in TERMINAL_STATUSES
    )
    failed_children = sum(
        count for status, count in status_counts.items() if status in ("failed", "cancelled")
    )
    if failed_children:
        raise RuntimeError(f"批量生成存在失败子任务（{failed_children} 个），父任务失败")
    if terminal_children < total_children:
        # 仍有运行中的子任务：回队退避轮询。
        ctx.requeue_after = 10
        await db.flush()
        return
    # 全部 completed：父任务完成（由 runner 标 completed）。
    await db.flush()
