"""集中式项目成员校验与 project-scoped resource resolver（T02）。

本模块是对象级授权的唯一事实源：
- :func:`check_project_member` —— 校验调用者是 URL 中 pid 的成员且角色满足要求
  （全局 admin 可绕过成员/角色，但不能绕过“资源真实属于该 pid”的绑定）。
- :class:`ProjectResourceResolver` —— 用数据库外键 join/EXISTS 校验“对象真实属于 pid”，
  任何客户端提供的父子 ID 都不是授权事实。

状态码语义（任务卡 §5.1）：
- 资源不存在，或资源不属于 URL 中 pid -> 404（不泄露哪一个 ID 存在）；
- 项目存在但用户非成员 / 角色不足 -> 403；
- 同项目父子 ID 组合不成立 -> 404。

所有写操作必须与 scoped load 处于同一事务（本仓库路由在同一 db session 内
先 scoped load 再写入），避免授权检查与写入之间的 TOCTOU 窗口。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.chunk_set import ChunkSet
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.config import ChunkProfile, ExportProfile, ModelConfig, ParserProfile, TaskPolicy
from app.models.curated import CuratedItem, CuratedRevision, EvidenceLink
from app.models.dataset import Benchmark, BenchmarkCase, Dataset, DatasetItem
from app.models.document import Document
from app.models.export import Export, SnapshotManifest
from app.models.generation import Candidate, CandidateComment, GenerationRun
from app.models.parse import ParseJob
from app.models.project import ProjectMember
from app.models.prompt_template import PromptTemplate, PromptTemplateVersion
from app.models.section import CleaningJob, Section
from app.models.task import LlmUsageLog, Task
from app.models.user import User
from domain.enums import ROLE_HIERARCHY, UserRole

logger = logging.getLogger(__name__)

# 允许 admin 绕过成员/角色检查的全局角色。
GLOBAL_ADMIN_ROLE = UserRole.admin


def _role_level(role: str | UserRole) -> int:
    return ROLE_HIERARCHY.get(UserRole(role), -1)


async def check_project_member(
    db: AsyncSession,
    pid: uuid.UUID,
    user: User,
    min_role: UserRole,
) -> None:
    """校验 ``user`` 是项目 ``pid`` 的成员且角色不低于 ``min_role``。

    失败时抛出对应的 HTTPException：
    - 非成员 -> 403；
    - 成员但角色不足 -> 403。
    """
    from fastapi import HTTPException, status

    # 全局 admin 可绕过成员与最低项目角色检查（但不能绕过资源归属绑定）。
    if _role_level(user.role) >= _role_level(GLOBAL_ADMIN_ROLE):
        return

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == pid,
            ProjectMember.user_id == user.id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="非项目成员")

    member_level = _role_level(member.role)
    required_level = _role_level(min_role)
    if member_level < required_level:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="项目权限不足")


class ProjectResourceResolver:
    """按项目归属加载资源；资源不存在或不属于 pid 时返回 None（调用方统一 404）。

    归属链见任务卡 §4 数据库合同。所有查询均带 project 谓词，绝不先查对象再信任 pid。
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _one(self, stmt):
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # 归属链：资源.project_id（直接）
    # ------------------------------------------------------------------

    async def document(self, pid: uuid.UUID, did: uuid.UUID) -> Document | None:
        return await self._one(
            select(Document).where(Document.id == did, Document.project_id == pid)
        )

    async def model_config(self, pid: uuid.UUID, config_id: uuid.UUID) -> ModelConfig | None:
        return await self._one(
            select(ModelConfig).where(ModelConfig.id == config_id, ModelConfig.project_id == pid)
        )

    async def parser_profile(self, pid: uuid.UUID, profile_id: uuid.UUID) -> ParserProfile | None:
        return await self._one(
            select(ParserProfile).where(ParserProfile.id == profile_id, ParserProfile.project_id == pid)
        )

    async def chunk_profile(self, pid: uuid.UUID, profile_id: uuid.UUID) -> ChunkProfile | None:
        return await self._one(
            select(ChunkProfile).where(ChunkProfile.id == profile_id, ChunkProfile.project_id == pid)
        )

    async def export_profile(self, pid: uuid.UUID, profile_id: uuid.UUID) -> ExportProfile | None:
        return await self._one(
            select(ExportProfile).where(ExportProfile.id == profile_id, ExportProfile.project_id == pid)
        )

    async def task_policy(self, pid: uuid.UUID, policy_id: uuid.UUID) -> TaskPolicy | None:
        return await self._one(
            select(TaskPolicy).where(TaskPolicy.id == policy_id, TaskPolicy.project_id == pid)
        )

    async def prompt_template(self, pid: uuid.UUID, tid: uuid.UUID) -> PromptTemplate | None:
        return await self._one(
            select(PromptTemplate).where(PromptTemplate.id == tid, PromptTemplate.project_id == pid)
        )

    async def curated_item(self, pid: uuid.UUID, iid: uuid.UUID) -> CuratedItem | None:
        return await self._one(
            select(CuratedItem).where(CuratedItem.id == iid, CuratedItem.project_id == pid)
        )

    async def dataset(self, pid: uuid.UUID, did: uuid.UUID) -> Dataset | None:
        return await self._one(
            select(Dataset).where(Dataset.id == did, Dataset.project_id == pid)
        )

    async def benchmark(self, pid: uuid.UUID, bid: uuid.UUID) -> Benchmark | None:
        return await self._one(
            select(Benchmark).where(Benchmark.id == bid, Benchmark.project_id == pid)
        )

    async def export(self, pid: uuid.UUID, eid: uuid.UUID) -> Export | None:
        return await self._one(
            select(Export).where(Export.id == eid, Export.project_id == pid)
        )

    async def task(self, pid: uuid.UUID, tid: uuid.UUID) -> Task | None:
        return await self._one(
            select(Task).where(Task.id == tid, Task.project_id == pid)
        )

    async def llm_usage_log(self, pid: uuid.UUID, log_id: uuid.UUID) -> LlmUsageLog | None:
        return await self._one(
            select(LlmUsageLog).where(LlmUsageLog.id == log_id, LlmUsageLog.project_id == pid)
        )

    # ------------------------------------------------------------------
    # 归属链：资源 -> Document.project_id
    # ------------------------------------------------------------------

    async def parse_job(self, pid: uuid.UUID, jid: uuid.UUID) -> ParseJob | None:
        return await self._one(
            select(ParseJob)
            .join(Document, Document.id == ParseJob.document_id)
            .where(ParseJob.id == jid, Document.project_id == pid)
        )

    async def cleaning_job(self, pid: uuid.UUID, cjid: uuid.UUID) -> CleaningJob | None:
        return await self._one(
            select(CleaningJob)
            .join(Document, Document.id == CleaningJob.document_id)
            .where(CleaningJob.id == cjid, Document.project_id == pid)
        )

    async def section(self, pid: uuid.UUID, sid: uuid.UUID) -> Section | None:
        return await self._one(
            select(Section)
            .join(Document, Document.id == Section.document_id)
            .where(Section.id == sid, Document.project_id == pid)
        )

    async def cleaned_version(self, pid: uuid.UUID, vid: uuid.UUID) -> CleanedDocumentVersion | None:
        return await self._one(
            select(CleanedDocumentVersion)
            .join(Document, Document.id == CleanedDocumentVersion.document_id)
            .where(CleanedDocumentVersion.id == vid, Document.project_id == pid)
        )

    async def chunk_set(self, pid: uuid.UUID, csid: uuid.UUID) -> ChunkSet | None:
        return await self._one(
            select(ChunkSet)
            .join(Document, Document.id == ChunkSet.document_id)
            .where(ChunkSet.id == csid, Document.project_id == pid)
        )

    async def chunk(self, pid: uuid.UUID, cid: uuid.UUID) -> Chunk | None:
        """按 Chunk.document_id -> Document 校验归属，并复核冗余外键一致性。

        Chunk.section_id 与 document_id 同时存在时必须与其 Section.document_id 一致；
        不一致属于跨项目/损坏数据，拒绝访问并记录安全告警（任务卡 §4）。
        """
        chunk = await self._one(
            select(Chunk)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.id == cid, Document.project_id == pid)
        )
        if chunk is None:
            return None
        if chunk.section_id is not None:
            section_pid = await self._one(
                select(Document.project_id)
                .join(Section, Section.document_id == Document.id)
                .where(Section.id == chunk.section_id)
            )
            if section_pid != pid or section_pid is None:
                logger.warning(
                    "拒绝访问：Chunk %s 的 section_id 归属不一致 (project=%s)", cid, section_pid
                )
                return None
        return chunk

    # ------------------------------------------------------------------
    # 归属链：资源 -> Chunk -> Document.project_id
    # ------------------------------------------------------------------

    async def generation_run(self, pid: uuid.UUID, gid: uuid.UUID) -> GenerationRun | None:
        return await self._one(
            select(GenerationRun)
            .join(Chunk, Chunk.id == GenerationRun.chunk_id)
            .join(Document, Document.id == Chunk.document_id)
            .where(GenerationRun.id == gid, Document.project_id == pid)
        )

    async def candidate(self, pid: uuid.UUID, cid: uuid.UUID) -> Candidate | None:
        return await self._one(
            select(Candidate)
            .join(Chunk, Chunk.id == Candidate.chunk_id)
            .join(Document, Document.id == Chunk.document_id)
            .where(Candidate.id == cid, Document.project_id == pid)
        )

    async def candidate_comment(self, pid: uuid.UUID, ccid: uuid.UUID) -> CandidateComment | None:
        return await self._one(
            select(CandidateComment)
            .join(Candidate, Candidate.id == CandidateComment.candidate_id)
            .join(Chunk, Chunk.id == Candidate.chunk_id)
            .join(Document, Document.id == Chunk.document_id)
            .where(CandidateComment.id == ccid, Document.project_id == pid)
        )

    # ------------------------------------------------------------------
    # 归属链：资源 -> CuratedItem.project_id
    # ------------------------------------------------------------------

    async def curated_revision(self, pid: uuid.UUID, rid: uuid.UUID) -> CuratedRevision | None:
        return await self._one(
            select(CuratedRevision)
            .join(CuratedItem, CuratedItem.id == CuratedRevision.curated_item_id)
            .where(CuratedRevision.id == rid, CuratedItem.project_id == pid)
        )

    async def evidence_link(self, pid: uuid.UUID, elid: uuid.UUID) -> EvidenceLink | None:
        return await self._one(
            select(EvidenceLink)
            .join(CuratedItem, CuratedItem.id == EvidenceLink.curated_item_id)
            .where(EvidenceLink.id == elid, CuratedItem.project_id == pid)
        )

    # ------------------------------------------------------------------
    # 归属链：资源 -> Dataset/Benchmark.project_id
    # ------------------------------------------------------------------

    async def dataset_item(self, pid: uuid.UUID, item_id: uuid.UUID) -> DatasetItem | None:
        return await self._one(
            select(DatasetItem)
            .join(Dataset, Dataset.id == DatasetItem.dataset_id)
            .where(DatasetItem.id == item_id, Dataset.project_id == pid)
        )

    async def benchmark_case(self, pid: uuid.UUID, case_id: uuid.UUID) -> BenchmarkCase | None:
        return await self._one(
            select(BenchmarkCase)
            .join(Benchmark, Benchmark.id == BenchmarkCase.benchmark_id)
            .where(BenchmarkCase.id == case_id, Benchmark.project_id == pid)
        )

    # ------------------------------------------------------------------
    # 归属链：资源 -> Export.project_id（manifest 禁止按裸 ID 暴露）
    # ------------------------------------------------------------------

    async def snapshot_manifest(self, pid: uuid.UUID, manifest_id: uuid.UUID) -> SnapshotManifest | None:
        return await self._one(
            select(SnapshotManifest)
            .join(Export, Export.snapshot_manifest_id == SnapshotManifest.id)
            .where(SnapshotManifest.id == manifest_id, Export.project_id == pid)
        )

    # ------------------------------------------------------------------
    # 归属链：资源 -> PromptTemplate.project_id
    # ------------------------------------------------------------------

    async def prompt_template_version(
        self, pid: uuid.UUID, vid: uuid.UUID
    ) -> PromptTemplateVersion | None:
        return await self._one(
            select(PromptTemplateVersion)
            .join(PromptTemplate, PromptTemplate.id == PromptTemplateVersion.template_id)
            .where(PromptTemplateVersion.id == vid, PromptTemplate.project_id == pid)
        )

    # ------------------------------------------------------------------
    # 请求体外键引用的同项目校验
    # ------------------------------------------------------------------

    async def ensure_in_project(
        self,
        pid: uuid.UUID,
        checks: list[tuple[type, uuid.UUID]],
        *,
        detail: str = "资源不存在",
    ) -> None:
        """校验请求体引用的全部外键对象都属于同一项目。

        任一对象不存在或属于其它项目 -> 404；不泄露具体哪一个 ID 无效
        （任务卡 §5.1：跨项目引用统一表现为 404，不得暴露对象存在性）。
        """
        from fastapi import HTTPException, status

        resolvers = {
            Document: self.document,
            ModelConfig: self.model_config,
            ParserProfile: self.parser_profile,
            ChunkProfile: self.chunk_profile,
            ExportProfile: self.export_profile,
            TaskPolicy: self.task_policy,
            PromptTemplate: self.prompt_template,
            CuratedItem: self.curated_item,
            Dataset: self.dataset,
            Benchmark: self.benchmark,
            Task: self.task,
            Chunk: self.chunk,
            Section: self.section,
            CleanedDocumentVersion: self.cleaned_version,
            ChunkSet: self.chunk_set,
            GenerationRun: self.generation_run,
            Candidate: self.candidate,
        }
        for model, resource_id in checks:
            resolver = resolvers.get(model)
            if resolver is None:
                raise TypeError(f"ensure_in_project 未注册 resolver: {model.__name__}")
            if await resolver(pid, resource_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
