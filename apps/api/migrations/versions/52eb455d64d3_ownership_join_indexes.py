"""add ownership-join indexes for project-scoped authorization

T02 项目资源授权：为 scoped resolver 的归属链 join 路径补充索引。
任务卡 §4：EXPLAIN 证明缺少索引时，可新增项目归属 join 所需索引，
并提交可逆 Alembic 迁移。

新增索引均为对象级授权（ProjectResourceResolver）的 WHERE/JOIN 谓词列：
- chunks.document_id / chunks.section_id  -> Chunk -> Document 归属
- sections.document_id / sections.cleaning_job_id -> Section 归属
- candidates.chunk_id / generation_runs.chunk_id -> Candidate/GenerationRun 归属
- parse_jobs.document_id / cleaning_jobs.document_id -> ParseJob/CleaningJob 归属
- cleaned_document_versions.document_id -> CleanedDocumentVersion 归属
- curated_items.project_id / datasets.project_id / benchmarks.project_id /
  exports.project_id -> 直接 project 归属（多数已有 FK 隐式索引，此处补齐缺失的）
- prompts/templates/config 类的 project_id 由 FK 自动建索引，不重复添加

Revision ID: <revision>
Revises: 58918ea257fd
Create Date: 2026-08-02

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '52eb455d64d3'
down_revision: str | None = '58918ea257fd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Chunk 归属链
    op.create_index(op.f('ix_chunks_document_id'), 'chunks', ['document_id'])
    op.create_index(op.f('ix_chunks_section_id'), 'chunks', ['section_id'])
    # Section 归属链
    op.create_index(op.f('ix_sections_document_id'), 'sections', ['document_id'])
    op.create_index(op.f('ix_sections_cleaning_job_id'), 'sections', ['cleaning_job_id'])
    # Candidate / GenerationRun 归属链
    op.create_index(op.f('ix_candidates_chunk_id'), 'candidates', ['chunk_id'])
    op.create_index(op.f('ix_generation_runs_chunk_id'), 'generation_runs', ['chunk_id'])
    # ParseJob / CleaningJob 归属链
    op.create_index(op.f('ix_parse_jobs_document_id'), 'parse_jobs', ['document_id'])
    op.create_index(op.f('ix_cleaning_jobs_document_id'), 'cleaning_jobs', ['document_id'])
    # CleanedDocumentVersion 归属链
    op.create_index(op.f('ix_cleaned_document_versions_document_id'), 'cleaned_document_versions', ['document_id'])
    # 直接 project 归属（部分表 FK 未自动建索引）
    op.create_index(op.f('ix_curated_items_project_id'), 'curated_items', ['project_id'])
    op.create_index(op.f('ix_datasets_project_id'), 'datasets', ['project_id'])
    op.create_index(op.f('ix_benchmarks_project_id'), 'benchmarks', ['project_id'])
    op.create_index(op.f('ix_exports_project_id'), 'exports', ['project_id'])
    # tasks / llm_usage_logs 项目归属
    op.create_index(op.f('ix_tasks_project_id'), 'tasks', ['project_id'])
    op.create_index(op.f('ix_llm_usage_logs_project_id'), 'llm_usage_logs', ['project_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_llm_usage_logs_project_id'), table_name='llm_usage_logs')
    op.drop_index(op.f('ix_tasks_project_id'), table_name='tasks')
    op.drop_index(op.f('ix_exports_project_id'), table_name='exports')
    op.drop_index(op.f('ix_benchmarks_project_id'), table_name='benchmarks')
    op.drop_index(op.f('ix_datasets_project_id'), table_name='datasets')
    op.drop_index(op.f('ix_curated_items_project_id'), table_name='curated_items')
    op.drop_index(op.f('ix_cleaned_document_versions_document_id'), table_name='cleaned_document_versions')
    op.drop_index(op.f('ix_cleaning_jobs_document_id'), table_name='cleaning_jobs')
    op.drop_index(op.f('ix_parse_jobs_document_id'), table_name='parse_jobs')
    op.drop_index(op.f('ix_generation_runs_chunk_id'), table_name='generation_runs')
    op.drop_index(op.f('ix_candidates_chunk_id'), table_name='candidates')
    op.drop_index(op.f('ix_sections_cleaning_job_id'), table_name='sections')
    op.drop_index(op.f('ix_sections_document_id'), table_name='sections')
    op.drop_index(op.f('ix_chunks_section_id'), table_name='chunks')
    op.drop_index(op.f('ix_chunks_document_id'), table_name='chunks')
