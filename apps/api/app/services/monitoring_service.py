import uuid

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import LlmUsageLog
from app.schemas.monitoring import DailyTrend, MonitoringSummary, UsageByGroup


class MonitoringService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_summary(self, project_id: uuid.UUID) -> MonitoringSummary:
        result = await self.db.execute(
            select(
                func.coalesce(func.sum(LlmUsageLog.input_tokens), 0),
                func.coalesce(func.sum(LlmUsageLog.output_tokens), 0),
                func.count(),
                func.count().filter(LlmUsageLog.status == "error"),
                func.coalesce(func.avg(LlmUsageLog.latency_ms), 0),
            ).where(LlmUsageLog.project_id == project_id)
        )
        row = result.one()
        return MonitoringSummary(
            total_input_tokens=row[0], total_output_tokens=row[1],
            total_requests=row[2], total_errors=row[3], avg_latency_ms=float(row[4]),
        )

    async def get_by_model(self, project_id: uuid.UUID) -> list[UsageByGroup]:
        from app.models.config import ModelConfig

        result = await self.db.execute(
            select(
                ModelConfig.model_name,
                func.sum(LlmUsageLog.input_tokens),
                func.sum(LlmUsageLog.output_tokens),
                func.count(),
            )
            .join(ModelConfig, LlmUsageLog.model_config_id == ModelConfig.id)
            .where(LlmUsageLog.project_id == project_id)
            .group_by(ModelConfig.model_name)
        )
        return [
            UsageByGroup(group=row[0], input_tokens=row[1], output_tokens=row[2], request_count=row[3])
            for row in result.all()
        ]

    async def get_by_task_type(self, project_id: uuid.UUID) -> list[UsageByGroup]:
        from app.models.task import Task

        result = await self.db.execute(
            select(
                Task.task_type,
                func.sum(LlmUsageLog.input_tokens),
                func.sum(LlmUsageLog.output_tokens),
                func.count(),
            )
            .join(Task, LlmUsageLog.task_id == Task.id)
            .where(LlmUsageLog.project_id == project_id)
            .group_by(Task.task_type)
        )
        return [
            UsageByGroup(group=row[0], input_tokens=row[1], output_tokens=row[2], request_count=row[3])
            for row in result.all()
        ]

    async def get_daily_trend(self, project_id: uuid.UUID) -> list[DailyTrend]:
        result = await self.db.execute(
            select(
                cast(LlmUsageLog.created_at, Date),
                func.sum(LlmUsageLog.input_tokens),
                func.sum(LlmUsageLog.output_tokens),
                func.count(),
            )
            .where(LlmUsageLog.project_id == project_id)
            .group_by(cast(LlmUsageLog.created_at, Date))
            .order_by(cast(LlmUsageLog.created_at, Date))
        )
        return [
            DailyTrend(date=str(row[0]), input_tokens=row[1], output_tokens=row[2], request_count=row[3])
            for row in result.all()
        ]

    async def get_by_template(self, project_id: uuid.UUID) -> list[UsageByGroup]:
        from app.models.prompt_template import PromptTemplate

        result = await self.db.execute(
            select(
                PromptTemplate.name,
                func.sum(LlmUsageLog.input_tokens),
                func.sum(LlmUsageLog.output_tokens),
                func.count(),
            )
            .join(PromptTemplate, LlmUsageLog.prompt_template_id == PromptTemplate.id)
            .where(LlmUsageLog.project_id == project_id)
            .group_by(PromptTemplate.name)
        )
        return [
            UsageByGroup(group=row[0], input_tokens=row[1], output_tokens=row[2], request_count=row[3])
            for row in result.all()
        ]
