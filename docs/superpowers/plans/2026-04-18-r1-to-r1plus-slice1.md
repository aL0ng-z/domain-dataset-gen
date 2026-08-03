# R1 → R1+ Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the P1 data-model foundations for all future R1+ workflows + the full end-to-end P2 Clean workflow (admin assignment, merge full cleaned markdown, document-level final-review).

**Architecture:** Additive-only extension of the existing R1 codebase. Four new tables (`cleaned_document_versions`, `chunk_sets`, `generation_batches`, `review_records`) plus nullable/default columns on 5 existing tables. New service `clean_version_service.py`, extensions to `section_service.py`, new router endpoints under `documents.py` and `sections.py`. Frontend updates confined to `clean/page.tsx`.

**Tech Stack:** FastAPI + SQLAlchemy 2.x + Alembic + Pydantic v2 + Next.js 16 + shadcn/ui. MinIO for blob storage.

**Source spec:** `docs/superpowers/specs/2026-04-18-r1-to-r1plus-slice1-design.md`

---

## File Map

**New backend files:**
```
apps/api/app/models/cleaned_document_version.py
apps/api/app/models/chunk_set.py
apps/api/app/models/generation_batch.py
apps/api/app/models/review_record.py
apps/api/app/schemas/cleaned_version.py
apps/api/app/schemas/review_record.py
apps/api/app/services/clean_version_service.py
apps/api/app/routers/cleaned_versions.py
apps/api/migrations/versions/r1plus_slice1_schema.py
```

**Modified backend files:**
```
apps/api/app/models/__init__.py
apps/api/app/models/document.py
apps/api/app/models/section.py
apps/api/app/models/chunk.py
apps/api/app/models/generation.py
apps/api/app/models/export.py
apps/api/app/schemas/section.py
apps/api/app/schemas/document.py
apps/api/app/services/section_service.py
apps/api/app/routers/documents.py
apps/api/app/routers/sections.py
apps/api/app/main.py
```

**Modified frontend files:**
```
apps/web/src/app/(dashboard)/projects/[id]/documents/[did]/clean/page.tsx
apps/web/src/lib/api.ts            # only if new typed helpers needed; can use generic api.post
```

**Modified docs:**
```
DevLog.md
```

---

## Testing Strategy

This codebase does not yet have a pytest suite. For this slice:

1. **Migration verification**: Apply upgrade, assert expected tables/columns exist via `psql \d`; apply downgrade; re-apply upgrade.
2. **API smoke tests**: Curl each new endpoint and verify HTTP status + shape.
3. **Frontend build test**: `npm run build` with zero type errors.
4. **Non-regression**: Run the existing seed + login + upload → parse → section list → PATCH section → submit → review flow to confirm no breakage.

---

# Phase 1 — Foundation

Single agent, sequential. No parallelism here — later phases depend on the models and migration.

---

### Task 1: Create new SQLAlchemy models (4 files)

**Files:**
- Create: `apps/api/app/models/cleaned_document_version.py`
- Create: `apps/api/app/models/chunk_set.py`
- Create: `apps/api/app/models/generation_batch.py`
- Create: `apps/api/app/models/review_record.py`

- [ ] **Step 1: Create `cleaned_document_version.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

cleaned_document_version_status_enum = ENUM(
    "draft", "review_pending", "accepted", "rejected",
    name="cleaned_document_version_status", create_type=True,
)


class CleanedDocumentVersion(Base):
    __tablename__ = "cleaned_document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_cleaned_doc_ver_doc_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    source_cleaning_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cleaning_jobs.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    section_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    merged_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(cleaned_document_version_status_enum, nullable=False, default="review_pending")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

- [ ] **Step 2: Create `chunk_set.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

chunk_set_status_enum = ENUM(
    "pending", "processing", "review_pending", "completed", "rejected",
    name="chunk_set_status", create_type=True,
)


class ChunkSet(Base):
    __tablename__ = "chunk_sets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    cleaned_document_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cleaned_document_versions.id"), nullable=True)
    chunk_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chunk_profiles.id"), nullable=True)
    strategy: Mapped[str | None] = mapped_column(String(50), nullable=True)
    config_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(chunk_set_status_enum, nullable=False, default="pending")
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    artifact_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

- [ ] **Step 3: Create `generation_batch.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

generation_batch_status_enum = ENUM(
    "pending", "processing", "review_pending", "completed", "failed",
    name="generation_batch_status", create_type=True,
)


class GenerationBatch(Base):
    __tablename__ = "generation_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chunk_sets.id"), nullable=True)
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=True)
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_templates.id"), nullable=True)
    selected_chunk_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(generation_batch_status_enum, nullable=False, default="pending")
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

- [ ] **Step 4: Create `review_record.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

review_record_action_enum = ENUM(
    "approve", "reject", "needs_revision", "agree", "disagree",
    name="review_record_action", create_type=True,
)


class ReviewRecord(Base):
    __tablename__ = "review_records"
    __table_args__ = (Index("ix_review_records_entity", "entity_type", "entity_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(review_record_action_enum, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 5: Verify all imports resolve**

Run: `cd apps/api && python -c "from app.models.cleaned_document_version import CleanedDocumentVersion; from app.models.chunk_set import ChunkSet; from app.models.generation_batch import GenerationBatch; from app.models.review_record import ReviewRecord; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/models/cleaned_document_version.py apps/api/app/models/chunk_set.py apps/api/app/models/generation_batch.py apps/api/app/models/review_record.py
git commit -m "feat(models): add R1+ CleanedDocumentVersion, ChunkSet, GenerationBatch, ReviewRecord tables"
```

---

### Task 2: Extend existing SQLAlchemy models

**Files:**
- Modify: `apps/api/app/models/document.py`
- Modify: `apps/api/app/models/section.py`
- Modify: `apps/api/app/models/chunk.py`
- Modify: `apps/api/app/models/generation.py`
- Modify: `apps/api/app/models/export.py`
- Modify: `apps/api/app/models/__init__.py`

- [ ] **Step 1: Extend `document.py`** — add new enum and columns

After the existing `document_status_enum`, add a second enum and extend the `Document` class:

```python
document_clean_status_enum = ENUM(
    "not_started", "section_planned", "in_progress", "review_pending", "completed",
    name="document_clean_status", create_type=True,
)
```

Add these columns on `Document` (place after `updated_at`):

```python
    clean_status: Mapped[str] = mapped_column(document_clean_status_enum, nullable=False, server_default="not_started")
    active_clean_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cleaned_document_versions.id"), nullable=True)
    active_chunk_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chunk_sets.id"), nullable=True)
```

- [ ] **Step 2: Extend `section.py`** — add assignment enum and columns

After the existing enums, add:

```python
section_assignment_status_enum = ENUM(
    "unassigned", "assigned", "in_progress", "completed", "returned",
    name="section_assignment_status", create_type=True,
)
```

Add columns on `Section` (after `cleaned_by`):

```python
    assignment_status: Mapped[str] = mapped_column(section_assignment_status_enum, nullable=False, server_default="unassigned")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
```

- [ ] **Step 3: Extend `chunk.py`** — add `chunk_set_id`

Add this column after `status`:

```python
    chunk_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chunk_sets.id"), nullable=True)
```

- [ ] **Step 4: Extend `generation.py`** — add new columns on `Candidate`

Add these on the `Candidate` class (after `reject_reason`):

```python
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    source_generation_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("generation_batches.id"), nullable=True)
    review_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="pending")
    thinking_text: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 5: Extend `export.py`** — add columns on `SnapshotManifest`

Add on `SnapshotManifest`:

```python
    chunk_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chunk_sets.id"), nullable=True)
    cleaned_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cleaned_document_versions.id"), nullable=True)
    generation_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("generation_batches.id"), nullable=True)
```

- [ ] **Step 6: Update `apps/api/app/models/__init__.py`** — add new model imports

Append after the last import:

```python
from app.models.cleaned_document_version import CleanedDocumentVersion  # noqa: F401
from app.models.chunk_set import ChunkSet  # noqa: F401
from app.models.generation_batch import GenerationBatch  # noqa: F401
from app.models.review_record import ReviewRecord  # noqa: F401
```

- [ ] **Step 7: Verify imports**

Run: `cd apps/api && python -c "from app import models; print('OK')"`
Expected: `OK` (no circular import / ForeignKey errors)

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/models/
git commit -m "feat(models): extend Document/Section/Chunk/Candidate/SnapshotManifest with R1+ fields"
```

---

### Task 3: Create Pydantic schemas

**Files:**
- Create: `apps/api/app/schemas/cleaned_version.py`
- Create: `apps/api/app/schemas/review_record.py`
- Modify: `apps/api/app/schemas/section.py`
- Modify: `apps/api/app/schemas/document.py`

- [ ] **Step 1: Create `cleaned_version.py`**

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from domain.schemas import BaseSchema


class CleanedDocumentVersionResponse(BaseSchema):
    id: uuid.UUID
    document_id: uuid.UUID
    source_cleaning_job_id: uuid.UUID | None
    version: int
    section_count: int
    artifact_key: str | None
    status: str
    created_by: uuid.UUID
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CleanedDocumentVersionDetailResponse(CleanedDocumentVersionResponse):
    merged_markdown: str


class CleanedFinalReviewRequest(BaseModel):
    version_id: uuid.UUID
    action: str = Field(description="'accept' or 'reject'")
    reason: str | None = None
    comment: str | None = None
```

- [ ] **Step 2: Create `review_record.py`**

```python
import uuid
from datetime import datetime

from domain.schemas import BaseSchema


class ReviewRecordResponse(BaseSchema):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    reviewer_id: uuid.UUID
    action: str
    reason: str | None
    comment: str | None
    created_at: datetime
```

- [ ] **Step 3: Extend `section.py`** — add assignment fields and request bodies

Replace the current `SectionResponse` with:

```python
class SectionResponse(BaseSchema):
    id: uuid.UUID
    cleaning_job_id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    heading_path: str
    source_pages: list | None
    raw_markdown: str
    cleaned_markdown: str | None
    status: str
    cleaned_by: uuid.UUID | None
    assignment_status: str
    assigned_to: uuid.UUID | None
    assigned_by: uuid.UUID | None
    assigned_at: datetime | None
    completed_at: datetime | None
    return_reason: str | None
    created_at: datetime
    updated_at: datetime
```

And append these request models at the bottom of the file:

```python
class SectionAssignRequest(BaseModel):
    assignee_id: uuid.UUID


class BulkAssignmentItem(BaseModel):
    section_ids: list[uuid.UUID]
    assignee_id: uuid.UUID


class BulkAssignRequest(BaseModel):
    assignments: list[BulkAssignmentItem]


class SectionReturnRequest(BaseModel):
    reason: str
```

- [ ] **Step 4: Extend `document.py`** — add clean_status and active IDs

Replace `DocumentResponse` with:

```python
class DocumentResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    file_size: int
    sha256: str
    status: str
    page_count: int | None
    uploaded_by: uuid.UUID
    clean_status: str
    active_clean_version_id: uuid.UUID | None
    active_chunk_set_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 5: Verify schema imports**

Run: `cd apps/api && python -c "from app.schemas.cleaned_version import CleanedDocumentVersionResponse, CleanedDocumentVersionDetailResponse, CleanedFinalReviewRequest; from app.schemas.section import SectionAssignRequest, BulkAssignRequest, SectionReturnRequest; from app.schemas.document import DocumentResponse; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/schemas/
git commit -m "feat(schemas): add R1+ CleanedDocumentVersion schemas + extend Section/Document"
```

---

### Task 4: Write Alembic migration

**Files:**
- Create: `apps/api/migrations/versions/r1plus_slice1_schema.py`

- [ ] **Step 1: Generate revision filename + stub**

Run: `cd apps/api && alembic revision -m "r1plus slice1 schema additions"`
Expected: a file is created under `migrations/versions/`. Rename it to `r1plus_slice1_schema.py` for clarity. Note the revision ID Alembic picked and the `down_revision` set by Alembic.

- [ ] **Step 2: Populate the migration**

Replace the stubbed `upgrade()` and `downgrade()` with this content. Keep the revision identifiers Alembic generated.

```python
"""r1plus slice1 schema additions

Revision ID: <keep the one alembic generated>
Revises: <keep whatever alembic put here, should be the latest head>
Create Date: 2026-04-18 ...

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "<keep>"
down_revision: Union[str, None] = "<keep>"
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
```

- [ ] **Step 3: Apply migration against the dev DB**

```bash
cd apps/api
alembic upgrade head
```
Expected output: `INFO [alembic.runtime.migration] Running upgrade ... -> <new_revision>, r1plus slice1 schema additions`

Verify columns in psql (inside `infra/docker-compose` postgres container):
```bash
docker compose -f infra/docker/docker-compose.yml exec postgres psql -U postgres -d dataset_gen -c "\d sections"
docker compose -f infra/docker/docker-compose.yml exec postgres psql -U postgres -d dataset_gen -c "\d documents"
docker compose -f infra/docker/docker-compose.yml exec postgres psql -U postgres -d dataset_gen -c "\dt"
```
Expected: new columns appear on `sections`/`documents`; new tables `cleaned_document_versions`, `chunk_sets`, `generation_batches`, `review_records` are listed.

- [ ] **Step 4: Round-trip test**

```bash
cd apps/api
alembic downgrade -1
alembic upgrade head
```
Expected: both commands succeed without error.

- [ ] **Step 5: Commit**

```bash
git add apps/api/migrations/versions/
git commit -m "feat(migrations): r1plus slice 1 schema additions (4 new tables + column extensions)"
```

---

# Phase 2 — Backend + Frontend in parallel

These three tasks are independent (different files, different services) and can run as parallel subagents. They all depend on Phase 1 completing.

---

### Task 5 (Agent A): Clean version service + documents router cleaning endpoints

**Files:**
- Create: `apps/api/app/services/clean_version_service.py`
- Create: `apps/api/app/routers/cleaned_versions.py`
- Modify: `apps/api/app/routers/documents.py`
- Modify: `apps/api/app/main.py` (register new router)

- [ ] **Step 1: Create `clean_version_service.py`**

```python
import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.document import Document
from app.models.review_record import ReviewRecord
from app.models.section import CleaningJob, Section
from storage import get_storage_client


class CleanVersionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._storage = get_storage_client(
            settings.minio_endpoint, settings.minio_access_key,
            settings.minio_secret_key, settings.minio_secure,
        )

    async def _next_version(self, document_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.coalesce(func.max(CleanedDocumentVersion.version), 0))
            .where(CleanedDocumentVersion.document_id == document_id)
        )
        return (result.scalar() or 0) + 1

    async def _latest_cleaning_job(self, document_id: uuid.UUID) -> CleaningJob | None:
        result = await self.db.execute(
            select(CleaningJob)
            .where(CleaningJob.document_id == document_id)
            .order_by(CleaningJob.created_at.desc())
        )
        return result.scalars().first()

    async def create_merged_version(self, document_id: uuid.UUID, user_id: uuid.UUID) -> CleanedDocumentVersion:
        doc = (await self.db.execute(select(Document).where(Document.id == document_id))).scalar_one_or_none()
        if doc is None:
            raise ValueError("document not found")

        sections_result = await self.db.execute(
            select(Section).where(Section.document_id == document_id).order_by(Section.ordinal)
        )
        sections = list(sections_result.scalars().all())
        if not sections:
            raise ValueError("no sections to merge")

        parts: list[str] = []
        for s in sections:
            body = s.cleaned_markdown if s.cleaned_markdown else s.raw_markdown
            if not body:
                continue
            parts.append(body.strip())
        merged = "\n\n".join(parts) + "\n"

        version = await self._next_version(document_id)
        cleaning_job = await self._latest_cleaning_job(document_id)

        artifact_key = f"cleaned/{document_id}/v{version}.md"
        await asyncio.to_thread(
            self._storage.upload_file,
            settings.minio_bucket_outputs, artifact_key, merged.encode("utf-8"), "text/markdown",
        )

        row = CleanedDocumentVersion(
            document_id=document_id,
            source_cleaning_job_id=cleaning_job.id if cleaning_job else None,
            version=version,
            section_count=len(sections),
            merged_markdown=merged,
            artifact_key=artifact_key,
            status="review_pending",
            created_by=user_id,
        )
        self.db.add(row)

        doc.clean_status = "review_pending"

        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def final_review(
        self, version_id: uuid.UUID, user_id: uuid.UUID,
        action: str, reason: str | None = None, comment: str | None = None,
    ) -> CleanedDocumentVersion:
        if action not in ("accept", "reject"):
            raise ValueError("action must be 'accept' or 'reject'")

        version = (
            await self.db.execute(select(CleanedDocumentVersion).where(CleanedDocumentVersion.id == version_id))
        ).scalar_one_or_none()
        if version is None:
            raise ValueError("version not found")

        record_action = "approve" if action == "accept" else "reject"
        record = ReviewRecord(
            entity_type="cleaned_document_version",
            entity_id=version_id,
            reviewer_id=user_id,
            action=record_action,
            reason=reason,
            comment=comment,
        )
        self.db.add(record)

        version.status = "accepted" if action == "accept" else "rejected"
        version.reviewed_by = user_id
        version.reviewed_at = datetime.now(timezone.utc)

        doc = (await self.db.execute(select(Document).where(Document.id == version.document_id))).scalar_one()
        if action == "accept":
            doc.clean_status = "completed"
            doc.active_clean_version_id = version.id
        else:
            doc.clean_status = "review_pending"  # stays; user can merge again

        await self.db.flush()
        await self.db.refresh(version)
        return version

    async def list_versions(self, document_id: uuid.UUID) -> list[CleanedDocumentVersion]:
        result = await self.db.execute(
            select(CleanedDocumentVersion)
            .where(CleanedDocumentVersion.document_id == document_id)
            .order_by(CleanedDocumentVersion.version.desc())
        )
        return list(result.scalars().all())

    async def get_version(self, version_id: uuid.UUID) -> CleanedDocumentVersion | None:
        result = await self.db.execute(
            select(CleanedDocumentVersion).where(CleanedDocumentVersion.id == version_id)
        )
        return result.scalar_one_or_none()
```

- [ ] **Step 2: Create `cleaned_versions.py` router**

```python
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.cleaned_version import CleanedDocumentVersionDetailResponse
from app.services.clean_version_service import CleanVersionService

router = APIRouter(prefix="/api/cleaned-versions", tags=["cleaned-versions"])


@router.get("/{vid}", response_model=CleanedDocumentVersionDetailResponse)
async def get_cleaned_version(
    vid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    service = CleanVersionService(db)
    version = await service.get_version(vid)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="版本不存在")
    return version
```

- [ ] **Step 3: Add cleaning endpoints to `documents.py`**

Add these imports near the top of `apps/api/app/routers/documents.py`:

```python
from app.schemas.cleaned_version import (
    CleanedDocumentVersionResponse, CleanedFinalReviewRequest,
)
from app.schemas.section import BulkAssignRequest
from app.services.clean_version_service import CleanVersionService
from app.services.section_service import SectionService
```

Append these endpoints to the end of `documents.py`:

```python
@router.post("/{did}/cleaning/assign", status_code=status.HTTP_200_OK)
async def bulk_assign_sections(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: BulkAssignRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
):
    doc = (await db.execute(select(Document).where(Document.id == did))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    service = SectionService(db)
    total_assigned = 0
    for assignment in body.assignments:
        n = await service.bulk_assign(assignment.section_ids, assignment.assignee_id, current_user.id)
        total_assigned += n

    # Flip document.clean_status if still not_started
    if doc.clean_status == "not_started":
        doc.clean_status = "section_planned"

    await db.commit()
    return {"assigned": total_assigned}


@router.post("/{did}/cleaning/merge", response_model=CleanedDocumentVersionResponse, status_code=status.HTTP_201_CREATED)
async def merge_clean_version(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
):
    service = CleanVersionService(db)
    try:
        version = await service.create_merged_version(did, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    await db.commit()
    return version


@router.post("/{did}/cleaning/final-review", response_model=CleanedDocumentVersionResponse)
async def final_review_clean_version(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: CleanedFinalReviewRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
):
    service = CleanVersionService(db)
    try:
        version = await service.final_review(
            body.version_id, current_user.id, body.action, body.reason, body.comment,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    await db.commit()
    return version


@router.get("/{did}/cleaning/versions", response_model=list[CleanedDocumentVersionResponse])
async def list_clean_versions(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    service = CleanVersionService(db)
    return await service.list_versions(did)
```

- [ ] **Step 4: Register the new router in `main.py`**

In `apps/api/app/main.py`, add these lines:

Insert after the existing router import block (after `from app.routers import datasets, benchmarks, exports`):

```python
from app.routers import cleaned_versions
```

Insert after `app.include_router(exports.router)`:

```python
app.include_router(cleaned_versions.router)
```

- [ ] **Step 5: Smoke test the endpoints**

Restart the API (`uvicorn` reload should pick it up automatically). Then, assuming valid JWT `$TOKEN`, project `$PID`, document `$DID` with parsed + cleaned sections:

```bash
# List versions (should be empty)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/projects/$PID/documents/$DID/cleaning/versions

# Merge
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/projects/$PID/documents/$DID/cleaning/merge
```
Expected: HTTP 201 with a version JSON containing `status: "review_pending"`.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/clean_version_service.py apps/api/app/routers/cleaned_versions.py apps/api/app/routers/documents.py apps/api/app/main.py
git commit -m "feat(api): cleaning assign/merge/final-review/versions endpoints"
```

---

### Task 6 (Agent B): Section service + router extensions

**Files:**
- Modify: `apps/api/app/services/section_service.py`
- Modify: `apps/api/app/routers/sections.py`

- [ ] **Step 1: Add service methods**

Append these methods to `apps/api/app/services/section_service.py` (inside the `SectionService` class):

```python
    # --- Assignment ---
    async def bulk_assign(
        self, section_ids: list[uuid.UUID], assignee_id: uuid.UUID, assigner_id: uuid.UUID,
    ) -> int:
        if not section_ids:
            return 0
        result = await self.db.execute(
            select(Section).where(Section.id.in_(section_ids))
        )
        sections = list(result.scalars().all())
        now = datetime.now(timezone.utc)
        for s in sections:
            s.assigned_to = assignee_id
            s.assigned_by = assigner_id
            s.assigned_at = now
            # Preserve in_progress if an editor had already started
            if s.assignment_status not in ("in_progress",):
                s.assignment_status = "assigned"
            s.return_reason = None
        await self.db.flush()
        return len(sections)

    async def assign_section(
        self, section_id: uuid.UUID, assignee_id: uuid.UUID, assigner_id: uuid.UUID,
    ) -> Section | None:
        n = await self.bulk_assign([section_id], assignee_id, assigner_id)
        if n == 0:
            return None
        return await self.get_section(section_id)

    async def complete_section(self, section_id: uuid.UUID, user_id: uuid.UUID, is_admin: bool) -> Section | None:
        section = await self.get_section(section_id)
        if section is None:
            return None
        if not is_admin and section.assigned_to and section.assigned_to != user_id:
            raise ValueError("只有被分派者或管理员可以标记完成")
        section.assignment_status = "completed"
        section.completed_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(section)
        return section

    async def return_section(self, section_id: uuid.UUID, reason: str) -> Section | None:
        section = await self.get_section(section_id)
        if section is None:
            return None
        section.assignment_status = "returned"
        section.return_reason = reason
        section.completed_at = None
        await self.db.flush()
        await self.db.refresh(section)
        return section
```

Also: add a behavioral tweak to the existing `update_section` method. When an editor saves, mark the section as `assignment_status = 'in_progress'` if it was `assigned`. Replace the body of `update_section` with:

```python
    async def update_section(self, section_id: uuid.UUID, cleaned_markdown: str, user_id: uuid.UUID) -> Section | None:
        section = await self.get_section(section_id)
        if section is None:
            return None

        revision = SectionRevision(
            section_id=section_id, revised_by=user_id,
            cleaned_markdown=section.cleaned_markdown or section.raw_markdown,
            revision_note="编辑前自动保存",
        )
        self.db.add(revision)

        section.cleaned_markdown = cleaned_markdown
        section.cleaned_by = user_id
        section.status = "in_cleaning"
        if section.assignment_status == "assigned":
            section.assignment_status = "in_progress"
        await self.db.flush()
        await self.db.refresh(section)
        return section
```

- [ ] **Step 2: Add endpoints to `sections.py` router**

Add imports at the top:

```python
from app.schemas.section import SectionAssignRequest, SectionReturnRequest
```

Append after the `/review` endpoint:

```python
@router.post("/{sid}/assign", response_model=SectionResponse)
async def assign_section_endpoint(
    sid: uuid.UUID,
    body: SectionAssignRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role(UserRole.reviewer))],
):
    service = SectionService(db)
    section = await service.assign_section(sid, body.assignee_id, current_user.id)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section 不存在")
    return section


@router.post("/{sid}/complete", response_model=SectionResponse)
async def complete_section_endpoint(
    sid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    service = SectionService(db)
    is_admin = current_user.role in ("admin", "reviewer")
    try:
        section = await service.complete_section(sid, current_user.id, is_admin)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section 不存在")
    return section


@router.post("/{sid}/return", response_model=SectionResponse)
async def return_section_endpoint(
    sid: uuid.UUID,
    body: SectionReturnRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role(UserRole.reviewer))],
):
    service = SectionService(db)
    section = await service.return_section(sid, body.reason)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section 不存在")
    return section
```

- [ ] **Step 3: Smoke test**

Assuming JWT `$TOKEN` (admin), a section id `$SID`, and a user id `$UID`:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"assignee_id\": \"$UID\"}" \
  http://localhost:8000/api/sections/$SID/assign
```

Expected: HTTP 200 with `assignment_status: "assigned"`, `assigned_to`, `assigned_by` populated.

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/services/section_service.py apps/api/app/routers/sections.py
git commit -m "feat(api): section assign/complete/return + assignment state transitions"
```

---

### Task 7 (Agent C): Frontend clean workbench updates

**Files:**
- Modify: `apps/web/src/app/(dashboard)/projects/[id]/documents/[did]/clean/page.tsx`

**Pre-work:** Per `apps/web/AGENTS.md`, this repo ships a modified Next.js 16 with differences. Before editing, read `apps/web/node_modules/next/dist/docs/` for any routing or dashboard-layout notes that touch `useParams`, `dynamic`, or client components. If nothing relevant is found in the docs, proceed.

- [ ] **Step 1: Add types and API helpers within the page file**

At the top of the file (before the component), add these types and `fetchCurrentUser`/`fetchMembers` helpers — kept in the same file since they are page-local:

```typescript
interface CleanedVersion {
  id: string;
  document_id: string;
  version: number;
  section_count: number;
  status: "draft" | "review_pending" | "accepted" | "rejected";
  created_by: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string;
}

interface ProjectMember {
  user_id: string;
  role: string;
  username?: string;  // server sometimes returns this; guard for undefined
}

interface CurrentUser {
  id: string;
  username: string;
  role: "admin" | "reviewer" | "editor" | "viewer";
}
```

Update the `Section` interface to include the new fields:

```typescript
interface Section {
  id: string;
  ordinal: number;
  heading_path: string;
  status: string;
  locked_by?: string;
  locked_by_name?: string;
  raw_markdown?: string;
  cleaned_markdown?: string;
  assignment_status: "unassigned" | "assigned" | "in_progress" | "completed" | "returned";
  assigned_to: string | null;
  return_reason?: string | null;
}
```

- [ ] **Step 2: Load current user and project members in the component**

Near the top of `CleaningWorkbenchPage`, add state and fetchers:

```typescript
const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
const [members, setMembers] = useState<ProjectMember[]>([]);
const [versions, setVersions] = useState<CleanedVersion[]>([]);
const [assignFilter, setAssignFilter] = useState<"all" | "mine" | "mine_pending" | "unassigned">("all");
const [selectedForAssign, setSelectedForAssign] = useState<Set<string>>(new Set());
const [assigneePick, setAssigneePick] = useState<string>("");

useEffect(() => {
  api.get<CurrentUser>("/auth/me")
    .then((u) => {
      setCurrentUser(u);
      if (u.role === "editor") setAssignFilter("mine");
    })
    .catch(() => {});
  api.get<ProjectMember[]>(`/projects/${projectId}/members`)
    .then((m) => setMembers(m))
    .catch(() => setMembers([]));
}, [projectId]);

const fetchVersions = useCallback(() => {
  api.get<CleanedVersion[]>(`/projects/${projectId}/documents/${docId}/cleaning/versions`)
    .then((v) => setVersions(v))
    .catch(() => setVersions([]));
}, [projectId, docId]);

useEffect(() => { fetchVersions(); }, [fetchVersions]);
```

- [ ] **Step 3: Compute filtered sections & completion counts**

Replace the existing `filteredSections` useMemo with this larger block:

```typescript
const isAdmin = currentUser?.role === "admin" || currentUser?.role === "reviewer";

const filteredSections = useMemo(() => {
  let base = sections;
  if (assignFilter === "mine" && currentUser) {
    base = base.filter((s) => s.assigned_to === currentUser.id);
  } else if (assignFilter === "mine_pending" && currentUser) {
    base = base.filter(
      (s) => s.assigned_to === currentUser.id && s.assignment_status !== "completed"
    );
  } else if (assignFilter === "unassigned") {
    base = base.filter((s) => s.assignment_status === "unassigned");
  }
  if (statusFilter !== "all") base = base.filter((s) => s.status === statusFilter);
  return base;
}, [sections, assignFilter, statusFilter, currentUser]);

const completionStats = useMemo(() => {
  const total = sections.length;
  const completed = sections.filter((s) => s.assignment_status === "completed").length;
  return { total, completed };
}, [sections]);

const latestVersion = versions[0];
```

- [ ] **Step 4: Add handlers for assign/merge/final-review/complete**

Add these after the existing handlers:

```typescript
const toggleAssignSelect = (sid: string) => {
  setSelectedForAssign((prev) => {
    const next = new Set(prev);
    if (next.has(sid)) next.delete(sid); else next.add(sid);
    return next;
  });
};

const handleBulkAssign = useCallback(async () => {
  if (!assigneePick || selectedForAssign.size === 0) {
    toast.error("请选择章节和指派对象");
    return;
  }
  try {
    await api.post(`/projects/${projectId}/documents/${docId}/cleaning/assign`, {
      assignments: [{ section_ids: Array.from(selectedForAssign), assignee_id: assigneePick }],
    });
    toast.success("已分派");
    setSelectedForAssign(new Set());
    fetchSections();
  } catch {
    toast.error("分派失败");
  }
}, [assigneePick, selectedForAssign, projectId, docId, fetchSections]);

const handleComplete = useCallback(async () => {
  if (!selectedSectionId) return;
  try {
    await api.post(`/sections/${selectedSectionId}/complete`);
    toast.success("已标记完成");
    fetchSections();
  } catch {
    toast.error("标记完成失败");
  }
}, [selectedSectionId, fetchSections]);

const handleReturn = useCallback(async () => {
  if (!selectedSectionId) return;
  const reason = window.prompt("请输入退回原因：");
  if (!reason) return;
  try {
    await api.post(`/sections/${selectedSectionId}/return`, { reason });
    toast.success("已退回");
    fetchSections();
  } catch {
    toast.error("退回失败");
  }
}, [selectedSectionId, fetchSections]);

const handleMerge = useCallback(async () => {
  try {
    await api.post(`/projects/${projectId}/documents/${docId}/cleaning/merge`);
    toast.success("已生成合并版本");
    fetchVersions();
  } catch {
    toast.error("合并失败");
  }
}, [projectId, docId, fetchVersions]);

const handleFinalReview = useCallback(async (action: "accept" | "reject") => {
  if (!latestVersion) return;
  const reason = action === "reject" ? window.prompt("驳回原因：") ?? undefined : undefined;
  try {
    await api.post(`/projects/${projectId}/documents/${docId}/cleaning/final-review`, {
      version_id: latestVersion.id, action, reason,
    });
    toast.success(action === "accept" ? "已通过" : "已驳回");
    fetchVersions();
  } catch {
    toast.error("操作失败");
  }
}, [latestVersion, projectId, docId, fetchVersions]);
```

- [ ] **Step 5: Render new UI — assign-filter chips, admin panel, completion bar**

Inside the sidebar (the block with `className="w-56 shrink-0 border-r flex flex-col"`), add above the existing status filter `<div className="p-2 border-b">...</div>`:

```tsx
<div className="p-2 border-b space-y-2">
  <div className="flex flex-wrap gap-1">
    {(["all", "mine", "mine_pending", "unassigned"] as const).map((v) => (
      <button
        key={v}
        onClick={() => setAssignFilter(v)}
        className={`text-[10px] px-1.5 py-0.5 rounded border ${
          assignFilter === v ? "bg-accent text-accent-foreground" : "bg-transparent"
        }`}
      >
        {v === "all" ? "全部" : v === "mine" ? "分派给我" : v === "mine_pending" ? "我未完成" : "未分派"}
      </button>
    ))}
  </div>
  {isAdmin && (
    <div className="space-y-1">
      <select
        className="w-full rounded border px-2 py-1 text-xs bg-transparent"
        value={assigneePick}
        onChange={(e) => setAssigneePick(e.target.value)}
      >
        <option value="">选择指派对象...</option>
        {members.map((m) => (
          <option key={m.user_id} value={m.user_id}>
            {m.username || m.user_id.slice(0, 8)} ({m.role})
          </option>
        ))}
      </select>
      <Button
        size="sm"
        variant="outline"
        className="w-full text-xs"
        disabled={selectedForAssign.size === 0 || !assigneePick}
        onClick={handleBulkAssign}
      >
        分派 {selectedForAssign.size} 个章节
      </Button>
    </div>
  )}
</div>
```

Update the section list row (`.map((section) => ...)`) to include a per-row checkbox (admin only) and show `assigned_to`:

```tsx
{filteredSections.map((section) => {
  const assignee = members.find((m) => m.user_id === section.assigned_to);
  return (
    <div key={section.id} className="flex items-center gap-1 px-1">
      {isAdmin && (
        <input
          type="checkbox"
          checked={selectedForAssign.has(section.id)}
          onChange={() => toggleAssignSelect(section.id)}
          className="shrink-0"
        />
      )}
      <button
        className={`flex-1 text-left rounded px-2 py-1.5 text-xs transition-colors ${
          selectedSectionId === section.id ? "bg-accent text-accent-foreground" : "hover:bg-muted"
        }`}
        onClick={() => setSelectedSectionId(section.id)}
      >
        <div className="flex items-center gap-1 justify-between">
          <span className="truncate flex-1">{section.ordinal + 1}. {section.heading_path || "无标题"}</span>
          <StatusBadge status={section.status} className="text-[9px] px-1 py-0" />
        </div>
        <div className="flex items-center gap-2 text-[10px] mt-0.5">
          <span className={`px-1 rounded ${
            section.assignment_status === "completed" ? "bg-green-100 text-green-700" :
            section.assignment_status === "in_progress" ? "bg-blue-100 text-blue-700" :
            section.assignment_status === "assigned" ? "bg-yellow-100 text-yellow-700" :
            section.assignment_status === "returned" ? "bg-red-100 text-red-700" :
            "bg-gray-100 text-gray-600"
          }`}>
            {section.assignment_status}
          </span>
          {assignee && <span className="text-muted-foreground">{assignee.username || assignee.user_id.slice(0, 8)}</span>}
        </div>
        {section.locked_by_name && (
          <div className="flex items-center gap-0.5 text-[10px] text-orange-600 mt-0.5">
            <LockIcon className="size-2.5" />
            {section.locked_by_name}
          </div>
        )}
      </button>
    </div>
  );
})}
```

- [ ] **Step 6: Add completion bar above the 3-column grid**

Inside the main 3-column section (just above `<div className="flex-1 grid grid-cols-3 min-h-0">`), add:

```tsx
<div className="flex items-center gap-2 px-3 py-1.5 border-b bg-muted/30 text-xs">
  <span>完成进度：{completionStats.completed} / {completionStats.total}</span>
  {isAdmin && (
    <Button
      size="xs"
      variant="outline"
      onClick={handleMerge}
      disabled={completionStats.completed === 0}
    >
      生成合并版本
    </Button>
  )}
  {latestVersion && (
    <>
      <Badge variant="secondary">
        v{latestVersion.version} · {latestVersion.status}
      </Badge>
      {isAdmin && latestVersion.status === "review_pending" && (
        <>
          <Button size="xs" variant="outline" onClick={() => handleFinalReview("accept")}>
            <CheckIcon className="size-3" /> 通过
          </Button>
          <Button size="xs" variant="outline" onClick={() => handleFinalReview("reject")}>
            <XIcon className="size-3" /> 驳回
          </Button>
          <Link
            href={`#`}
            onClick={async (e) => {
              e.preventDefault();
              const ver = await api.get<CleanedVersion & { merged_markdown: string }>(`/cleaned-versions/${latestVersion.id}`);
              const w = window.open("", "_blank");
              if (w) {
                w.document.write(`<pre style="white-space:pre-wrap;padding:16px;font-family:ui-monospace,monospace">${ver.merged_markdown.replace(/</g, "&lt;")}</pre>`);
                w.document.title = `合并版本 v${latestVersion.version}`;
              }
            }}
            className="text-xs text-blue-600 underline"
          >
            查看全文
          </Link>
        </>
      )}
    </>
  )}
</div>
```

- [ ] **Step 7: Add toolbar buttons for complete / return**

Add to the existing toolbar, after "提交审核" button:

```tsx
{selectedSection?.assigned_to === currentUser?.id && selectedSection?.assignment_status !== "completed" && (
  <Button variant="outline" size="sm" onClick={handleComplete}>
    <CheckIcon className="size-3" />
    完成 (分派)
  </Button>
)}
{isAdmin && selectedSection?.assignment_status === "completed" && (
  <Button variant="outline" size="sm" onClick={handleReturn}>
    <XIcon className="size-3" />
    退回
  </Button>
)}
```

- [ ] **Step 8: Build the frontend**

```bash
cd apps/web
npm run build
```
Expected: completes with 0 errors. Warnings are acceptable.

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/app/\(dashboard\)/projects/\[id\]/documents/\[did\]/clean/page.tsx
git commit -m "feat(web): clean workbench — admin assignment, completion bar, final-review"
```

---

# Phase 3 — Code Review

### Task 8: Code reviewer subagent

- [ ] **Step 1: Dispatch code-reviewer against the spec and the changes**

Launch the code-reviewer subagent with:
- Spec path: `docs/superpowers/specs/2026-04-18-r1-to-r1plus-slice1-design.md`
- Plan path: `docs/superpowers/plans/2026-04-18-r1-to-r1plus-slice1.md`
- Changed files (from `git diff master --name-only`)
- Focus areas: (a) non-regression of existing endpoints, (b) enum naming consistency, (c) auth levels on new endpoints, (d) FK constraints and cascades, (e) migration idempotency.

Expected: reviewer returns either "clean, no blockers" or a list of issues.

- [ ] **Step 2: Fix any blockers the reviewer surfaces, then commit**

If issues found, fix inline and commit separately.

---

# Phase 4 — Smoke Test + Dev Log

### Task 9: End-to-end smoke test

- [ ] **Step 1: Apply migration + start services fresh**

```bash
docker compose -f infra/docker/docker-compose.yml up -d postgres redis minio
cd apps/api && alembic upgrade head && cd ../..
```

- [ ] **Step 2: Start API, frontend, log in, upload a test PDF, parse, start cleaning**

Use the existing seed admin to generate a JWT. Via the frontend, navigate to the clean workbench. Verify:
1. Section list shows `assignment_status` badges (all `unassigned` initially)
2. Admin assignment panel is visible; multi-select works; assignee dropdown lists members; "分派 N 个章节" button submits
3. After assignment, assigned sections show the correct user marker
4. Mark at least one section as completed via the "完成 (分派)" button
5. Click "生成合并版本" — a new version appears in the completion bar with status `review_pending`
6. Click "查看全文" — a new tab opens showing the merged markdown
7. Click "通过" — version flips to `accepted`, document shows clean_status = `completed` via API

- [ ] **Step 3: Non-regression check**

Run the old flow: edit a section markdown, click 保存, 提交审核, 通过. Verify `section.status` transitions as before (draft → in_cleaning → review_pending → accepted). Verify that `assignment_status` also transitions (assigned → in_progress on save).

- [ ] **Step 4: Record smoke results + issues**

If any issue found during smoke test, append to `docs/r1-testing-issues.md` following its existing format.

---

### Task 10: Update dev log + final commit

**Files:**
- Modify: `DevLog.md`

- [ ] **Step 1: Append a new section to `dev-log.md`**

Under the existing "R1: Lab Pilot → 测试修复阶段" section, append a new "### R1+ Slice 1 (2026-04-18)" subsection summarizing:

- What shipped: 4 new tables, 16 new columns, clean-workflow endpoints, frontend assignment/merge/final-review UI
- Known limitations: still no tests for Chunk/Generate/Export, non-author review not enforced yet (P4)
- Next step: Slice 2 (P3 Chunk workflow) or Slice 3 (P4 Generate)

Include the usual file/commit counts.

- [ ] **Step 2: Final commit**

Do not commit automatically — ask the user whether they want a summary commit. If yes, create it.

---

## Self-Review Checklist (ran during writing)

- [x] Spec coverage — every new table/column/endpoint in the spec maps to a task
- [x] Placeholder scan — no TBD/TODO; every step has real code
- [x] Type consistency — `assignment_status`, `clean_status`, `review_status` names match across spec/migration/models/schemas
- [x] Migration references enum types that are created first before tables use them
- [x] Frontend per-row assignment-status colors use string match that matches backend enum values exactly
- [x] `get_current_user` vs `require_project_member` vs `require_role` used consistently per endpoint
- [x] Auth: `complete_section` permits editor-self; `assign`/`return` require reviewer+

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-18-r1-to-r1plus-slice1.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task phase with full context, review between tasks, fast iteration. Phases 1 & 2 run sequentially; within Phase 2 the three agents run in parallel.
2. **Inline Execution** — Execute in this session with checkpoints.

Given the R1 改进清单 user message said "The subagents are allowed", I'll proceed with **subagent-driven** by default.
