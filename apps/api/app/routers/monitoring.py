import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_project_member
from app.models.user import User
from app.schemas.monitoring import DailyTrend, MonitoringSummary, UsageByGroup
from app.services.monitoring_service import MonitoringService
from domain.enums import UserRole

router = APIRouter(prefix="/api/projects/{pid}/monitoring", tags=["monitoring"])


@router.get("/summary", response_model=MonitoringSummary, operation_id="monitoring_get_summary")
async def get_summary(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    return await MonitoringService(db).get_summary(pid)


@router.get("/by-model", response_model=list[UsageByGroup], operation_id="monitoring_get_by_model")
async def get_by_model(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    return await MonitoringService(db).get_by_model(pid)


@router.get("/by-task-type", response_model=list[UsageByGroup], operation_id="monitoring_get_by_task_type")
async def get_by_task_type(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    return await MonitoringService(db).get_by_task_type(pid)


@router.get("/daily-trend", response_model=list[DailyTrend], operation_id="monitoring_get_daily_trend")
async def get_daily_trend(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    return await MonitoringService(db).get_daily_trend(pid)


@router.get("/by-template", response_model=list[UsageByGroup], operation_id="monitoring_get_by_template")
async def get_by_template(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    return await MonitoringService(db).get_by_template(pid)
