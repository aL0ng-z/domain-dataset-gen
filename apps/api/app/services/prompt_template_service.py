import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.config import ModelConfig
from app.models.prompt_template import PromptTemplate, PromptTemplateVersion


class PromptTemplateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, project_id: uuid.UUID, **kwargs) -> PromptTemplate:
        template = PromptTemplate(project_id=project_id, **kwargs)
        self.db.add(template)
        await self.db.flush()
        await self.db.refresh(template)
        return template

    async def get(self, template_id: uuid.UUID) -> PromptTemplate | None:
        result = await self.db.execute(
            select(PromptTemplate).where(PromptTemplate.id == template_id)
        )
        return result.scalar_one_or_none()

    async def list_templates(
        self,
        project_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
        task_type: str | None = None,
    ) -> tuple[list[PromptTemplate], int]:
        offset = (page - 1) * page_size

        base = select(PromptTemplate).where(PromptTemplate.project_id == project_id)
        count_base = select(func.count()).select_from(PromptTemplate).where(
            PromptTemplate.project_id == project_id
        )

        if task_type is not None:
            base = base.where(PromptTemplate.task_type == task_type)
            count_base = count_base.where(PromptTemplate.task_type == task_type)

        count_result = await self.db.execute(count_base)
        total = count_result.scalar() or 0

        result = await self.db.execute(
            base.order_by(PromptTemplate.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total

    async def update(self, template_id: uuid.UUID, **kwargs) -> PromptTemplate | None:
        template = await self.get(template_id)
        if template is None:
            return None

        # Save current state as a version before updating
        version_snapshot = PromptTemplateVersion(
            template_id=template.id,
            version=template.version,
            system_prompt=template.system_prompt,
            user_prompt_template=template.user_prompt_template,
            input_schema=template.input_schema,
            output_schema=template.output_schema,
        )
        self.db.add(version_snapshot)

        # Apply updates
        for key, value in kwargs.items():
            if value is not None:
                setattr(template, key, value)
        template.version += 1

        await self.db.flush()
        await self.db.refresh(template)
        return template

    async def duplicate(self, template_id: uuid.UUID) -> PromptTemplate | None:
        template = await self.get(template_id)
        if template is None:
            return None

        new_template = PromptTemplate(
            project_id=template.project_id,
            task_type=template.task_type,
            name=f"{template.name} (副本)",
            system_prompt=template.system_prompt,
            user_prompt_template=template.user_prompt_template,
            input_schema=template.input_schema,
            output_schema=template.output_schema,
            is_default=False,
        )
        self.db.add(new_template)
        await self.db.flush()
        await self.db.refresh(new_template)
        return new_template

    async def list_versions(
        self, template_id: uuid.UUID
    ) -> list[PromptTemplateVersion]:
        result = await self.db.execute(
            select(PromptTemplateVersion)
            .where(PromptTemplateVersion.template_id == template_id)
            .order_by(PromptTemplateVersion.version.desc())
        )
        return list(result.scalars().all())

    async def test_run(
        self,
        template_id: uuid.UUID,
        chunk_id: uuid.UUID,
        model_config_id: uuid.UUID,
    ) -> dict:
        """Build prompt from template + chunk, call LLM, return result.

        Returns a dict with keys: input_prompt, output, input_tokens, output_tokens, latency_ms.
        """
        import time

        template = await self.get(template_id)
        if template is None:
            raise ValueError("模板不存在")

        # Load chunk
        chunk_result = await self.db.execute(select(Chunk).where(Chunk.id == chunk_id))
        chunk = chunk_result.scalar_one_or_none()
        if chunk is None:
            raise ValueError("Chunk不存在")

        # Load model config
        config_result = await self.db.execute(
            select(ModelConfig).where(ModelConfig.id == model_config_id)
        )
        model_config = config_result.scalar_one_or_none()
        if model_config is None:
            raise ValueError("模型配置不存在")

        # Build the prompt
        user_prompt = template.user_prompt_template.replace("{{content}}", chunk.content)
        user_prompt = user_prompt.replace("{{heading_path}}", chunk.heading_path or "")

        input_prompt = (
            f"[System]: {template.system_prompt}\n\n[User]: {user_prompt}"
        )

        # Call LLM via OpenAI-compatible API
        import httpx

        start_ms = time.monotonic_ns() // 1_000_000
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{model_config.base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {model_config.api_key_encrypted}"},
                    json={
                        "model": model_config.model_name,
                        "messages": [
                            {"role": "system", "content": template.system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": model_config.temperature,
                        "max_tokens": model_config.max_tokens,
                        **(model_config.extra_params or {}),
                    },
                )
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            raise ValueError(f"LLM调用失败: {e}")

        end_ms = time.monotonic_ns() // 1_000_000
        latency_ms = end_ms - start_ms

        output_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        return {
            "input_prompt": input_prompt,
            "output": output_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
        }
