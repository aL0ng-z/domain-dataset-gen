import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.config import ModelConfig
from app.models.generation import Candidate, GenerationRun
from app.models.prompt_template import PromptTemplate
from app.models.task import LlmUsageLog
from app.services.task_service import TaskService
from llm import LLMClient


async def run_generate_single(
    task_id: uuid.UUID,
    chunk_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
    model_config_id: uuid.UUID,
    project_id: uuid.UUID,
    db: AsyncSession,
    redis=None,
):
    task_service = TaskService(db, redis)
    await task_service.update_status(task_id, "processing", progress=10)

    try:
        chunk = (await db.execute(select(Chunk).where(Chunk.id == chunk_id))).scalar_one()
        template = (await db.execute(select(PromptTemplate).where(PromptTemplate.id == prompt_template_id))).scalar_one()
        model_config = (await db.execute(select(ModelConfig).where(ModelConfig.id == model_config_id))).scalar_one()

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

        await task_service.update_status(task_id, "processing", progress=30)

        # Call LLM
        client = LLMClient(
            base_url=model_config.base_url,
            api_key=model_config.api_key_encrypted,
            model_name=model_config.model_name,
            temperature=model_config.temperature,
            max_tokens=model_config.max_tokens,
        )

        response = await client.chat_completion(messages, response_format={"type": "json_object"})

        await task_service.update_status(task_id, "processing", progress=70)

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
            task_id=task_id,
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
        await task_service.update_status(task_id, "completed", progress=100)

    except Exception as e:
        # Log failed usage
        if "model_config_id" in dir():
            usage_log = LlmUsageLog(
                project_id=project_id,
                task_id=task_id,
                model_config_id=model_config_id,
                prompt_template_id=prompt_template_id,
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
                status="error",
                error_message=str(e),
            )
            db.add(usage_log)
            await db.flush()
        await task_service.update_status(task_id, "failed", error_message=str(e))


async def run_generate_batch(
    task_id: uuid.UUID,
    document_id: uuid.UUID,
    prompt_template_id: uuid.UUID,
    model_config_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
    redis=None,
):
    task_service = TaskService(db, redis)
    await task_service.update_status(task_id, "processing", progress=5)

    try:
        result = await db.execute(
            select(Chunk).where(Chunk.document_id == document_id, Chunk.status == "ready").order_by(Chunk.ordinal)
        )
        chunks = list(result.scalars().all())

        if not chunks:
            raise ValueError("没有可用于生成的 Chunk")

        for i, chunk in enumerate(chunks):
            progress = 5 + int(90 * i / len(chunks))
            await task_service.update_status(task_id, "processing", progress=progress)

            # Create subtask
            sub_task = await task_service.create_task(
                project_id=project_id,
                task_type="generate",
                entity_type="chunk",
                entity_id=chunk.id,
                created_by=user_id,
                parent_task_id=task_id,
            )

            await run_generate_single(
                sub_task.id, chunk.id, prompt_template_id, model_config_id, project_id, db, redis
            )

        await task_service.update_status(task_id, "completed", progress=100)

    except Exception as e:
        await task_service.update_status(task_id, "failed", error_message=str(e))
