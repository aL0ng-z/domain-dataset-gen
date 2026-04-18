"""r1plus slice1 schema additions

Revision ID: 58918ea257fd
Revises: 6067649337aa
Create Date: 2026-04-18 14:19:11.922734

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '58918ea257fd'
down_revision: Union[str, None] = '6067649337aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- New enums ---
    clean_status_enum = postgresql.ENUM(
        "not_started", "section_planned", "in_progress", "review_pending", "completed",
        name="document_clean_status", create_type=True,
    )
    clean_status_enum.create(op.get_bind(), checkfirst=True)

    assignment_status_enum = postgresql.ENUM(
        "unassigned", "assigned", "in_progress", "completed", "returned",
        name="section_assignment_status", create_type=True,
    )
    assignment_status_enum.create(op.get_bind(), checkfirst=True)

    cleaned_ver_status_enum = postgresql.ENUM(
        "draft", "review_pending", "accepted", "rejected",
        name="cleaned_document_version_status", create_type=True,
    )
    cleaned_ver_status_enum.create(op.get_bind(), checkfirst=True)

    chunk_set_status_enum = postgresql.ENUM(
        "pending", "processing", "review_pending", "completed", "rejected",
        name="chunk_set_status", create_type=True,
    )
    chunk_set_status_enum.create(op.get_bind(), checkfirst=True)

    generation_batch_status_enum = postgresql.ENUM(
        "pending", "processing", "review_pending", "completed", "failed",
        name="generation_batch_status", create_type=True,
    )
    generation_batch_status_enum.create(op.get_bind(), checkfirst=True)

    review_action_enum = postgresql.ENUM(
        "approve", "reject", "needs_revision", "agree", "disagree",
        name="review_record_action", create_type=True,
    )
    review_action_enum.create(op.get_bind(), checkfirst=True)

    # --- New tables ---
    op.create_table(
        "cleaned_document_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_cleaning_job_id", sa.UUID(), sa.ForeignKey("cleaning_jobs.id"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("section_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("merged_markdown", sa.Text(), nullable=False),
        sa.Column("artifact_key", sa.String(500), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="cleaned_document_version_status", create_type=False),
            nullable=False,
            server_default="review_pending",
        ),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewed_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("document_id", "version", name="uq_cleaned_doc_ver_doc_version"),
    )

    op.create_table(
        "chunk_sets",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cleaned_document_version_id", sa.UUID(), sa.ForeignKey("cleaned_document_versions.id"), nullable=True),
        sa.Column("chunk_profile_id", sa.UUID(), sa.ForeignKey("chunk_profiles.id"), nullable=True),
        sa.Column("strategy", sa.String(50), nullable=True),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="chunk_set_status", create_type=False),
            nullable=False, server_default="pending",
        ),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("artifact_key", sa.String(500), nullable=True),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewed_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "generation_batches",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_set_id", sa.UUID(), sa.ForeignKey("chunk_sets.id"), nullable=True),
        sa.Column("model_config_id", sa.UUID(), sa.ForeignKey("model_configs.id"), nullable=True),
        sa.Column("prompt_template_id", sa.UUID(), sa.ForeignKey("prompt_templates.id"), nullable=True),
        sa.Column("selected_chunk_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="generation_batch_status", create_type=False),
            nullable=False, server_default="pending",
        ),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "review_records",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("reviewer_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "action",
            postgresql.ENUM(name="review_record_action", create_type=False),
            nullable=False,
        ),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_review_records_entity", "review_records", ["entity_type", "entity_id"])

    # --- Column additions on existing tables ---
    op.add_column("documents", sa.Column(
        "clean_status",
        postgresql.ENUM(name="document_clean_status", create_type=False),
        nullable=False, server_default="not_started",
    ))
    op.add_column("documents", sa.Column("active_clean_version_id", sa.UUID(), sa.ForeignKey("cleaned_document_versions.id"), nullable=True))
    op.add_column("documents", sa.Column("active_chunk_set_id", sa.UUID(), sa.ForeignKey("chunk_sets.id"), nullable=True))

    op.add_column("sections", sa.Column(
        "assignment_status",
        postgresql.ENUM(name="section_assignment_status", create_type=False),
        nullable=False, server_default="unassigned",
    ))
    op.add_column("sections", sa.Column("assigned_to", sa.UUID(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("sections", sa.Column("assigned_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("sections", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sections", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sections", sa.Column("return_reason", sa.String(500), nullable=True))

    op.add_column("chunks", sa.Column("chunk_set_id", sa.UUID(), sa.ForeignKey("chunk_sets.id"), nullable=True))

    op.add_column("candidates", sa.Column("author_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("candidates", sa.Column("source_generation_batch_id", sa.UUID(), sa.ForeignKey("generation_batches.id"), nullable=True))
    op.add_column("candidates", sa.Column("review_status", sa.String(30), nullable=False, server_default="pending"))
    op.add_column("candidates", sa.Column("thinking_text", sa.Text(), nullable=True))

    op.add_column("snapshot_manifests", sa.Column("chunk_set_id", sa.UUID(), sa.ForeignKey("chunk_sets.id"), nullable=True))
    op.add_column("snapshot_manifests", sa.Column("cleaned_version_id", sa.UUID(), sa.ForeignKey("cleaned_document_versions.id"), nullable=True))
    op.add_column("snapshot_manifests", sa.Column("generation_batch_id", sa.UUID(), sa.ForeignKey("generation_batches.id"), nullable=True))


def downgrade() -> None:
    op.drop_column("snapshot_manifests", "generation_batch_id")
    op.drop_column("snapshot_manifests", "cleaned_version_id")
    op.drop_column("snapshot_manifests", "chunk_set_id")

    op.drop_column("candidates", "thinking_text")
    op.drop_column("candidates", "review_status")
    op.drop_column("candidates", "source_generation_batch_id")
    op.drop_column("candidates", "author_id")

    op.drop_column("chunks", "chunk_set_id")

    op.drop_column("sections", "return_reason")
    op.drop_column("sections", "completed_at")
    op.drop_column("sections", "assigned_at")
    op.drop_column("sections", "assigned_by")
    op.drop_column("sections", "assigned_to")
    op.drop_column("sections", "assignment_status")

    op.drop_column("documents", "active_chunk_set_id")
    op.drop_column("documents", "active_clean_version_id")
    op.drop_column("documents", "clean_status")

    op.drop_index("ix_review_records_entity", table_name="review_records")
    op.drop_table("review_records")
    op.drop_table("generation_batches")
    op.drop_table("chunk_sets")
    op.drop_table("cleaned_document_versions")

    for enum_name in (
        "review_record_action",
        "generation_batch_status",
        "chunk_set_status",
        "cleaned_document_version_status",
        "section_assignment_status",
        "document_clean_status",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
