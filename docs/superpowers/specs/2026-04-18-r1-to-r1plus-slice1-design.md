# R1 → R1+ Slice 1: P1 Schema + P2 Clean Workflow — Design

> Date: 2026-04-18
> Status: Approved (by user on 2026-04-18)
> Source spec: `domain-dataset-gen_R1_改进清单.md`
> Scope: First slice of the R1+ plan — data model foundations for all future R1+ workflows + end-to-end Clean workflow (assignment, merge, final-review).

---

## 1. Context

The R1 improvement checklist (`domain-dataset-gen_R1_改进清单.md`) describes a substantial evolution of the R1 pipeline:

- Extend Section with admin-assignment fields
- Add `CleanedDocumentVersion`, `ChunkSet`, `GenerationBatch`, `ReviewRecord` tables
- Extend Candidate, Chunk, Document with state fields
- Add Clean / Chunk / Generate workbenches + admin controls

The full checklist spans roughly P0–P6 phases and is too large for a single implementation session. This spec covers **Slice 1 only**: all new data model (P1) + the full end-to-end Clean workflow (P2). Subsequent slices will pick up Chunk (P3), Generate (P4), Export (P5), and remaining frontend (P6).

### Non-goals in this slice
- Chunk workbench, generation batches, review wiring for candidates, export bundle changes, tests beyond smoke checks.
- Removing or renaming existing tables/columns (strictly additive).

---

## 2. Data Model Changes (P1)

All changes are additive. A single Alembic revision captures every change.

### 2.1 New tables

#### `cleaned_document_versions`
Merged full-document cleaned markdown. One row per admin-triggered merge attempt.

| Column | Type | Notes |
|---|---|---|
| id | UUID | PK, default uuid4 |
| document_id | UUID | FK documents, ON DELETE CASCADE |
| source_cleaning_job_id | UUID | FK cleaning_jobs, nullable |
| version | INTEGER | per-document increment |
| section_count | INTEGER | sections included in the merge |
| merged_markdown | TEXT | inlined MD (source of truth; artifact is mirror) |
| artifact_key | VARCHAR(500) | MinIO key, `outputs` bucket |
| status | ENUM(draft, review_pending, accepted, rejected) | default `review_pending` |
| created_by | UUID | FK users |
| reviewed_by | UUID | FK users, nullable |
| reviewed_at | TIMESTAMPTZ | nullable |
| created_at | TIMESTAMPTZ | default now |
| updated_at | TIMESTAMPTZ | on update |

Unique: `(document_id, version)`.

#### `chunk_sets`
Schema-only in this slice; populated by future P3 worker.

| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| document_id | UUID | FK documents, ON DELETE CASCADE |
| cleaned_document_version_id | UUID | FK cleaned_document_versions, nullable |
| chunk_profile_id | UUID | FK chunk_profiles, nullable |
| strategy | VARCHAR(50) | nullable |
| config_json | JSONB | nullable |
| status | ENUM(pending, processing, review_pending, completed, rejected) | default `pending` |
| total_chunks | INTEGER | default 0 |
| total_tokens | INTEGER | default 0 |
| summary_json | JSONB | nullable |
| artifact_key | VARCHAR(500) | nullable |
| created_by | UUID | FK users |
| reviewed_by | UUID | FK users, nullable |
| reviewed_at | TIMESTAMPTZ | nullable |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

#### `generation_batches`
Schema-only in this slice.

| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| document_id | UUID | FK documents, ON DELETE CASCADE |
| chunk_set_id | UUID | FK chunk_sets, nullable |
| model_config_id | UUID | FK model_configs, nullable |
| prompt_template_id | UUID | FK prompt_templates, nullable |
| selected_chunk_ids | JSONB | list of chunk UUIDs |
| status | ENUM(pending, processing, review_pending, completed, failed) | default `pending` |
| total_chunks | INTEGER | default 0 |
| completed_chunks | INTEGER | default 0 |
| summary_json | JSONB | nullable |
| created_by | UUID | FK users |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

#### `review_records`
Structured reviews. Used in this slice for `entity_type='cleaned_document_version'`; same table reused later for `candidate` / `chunk_set`.

| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| entity_type | VARCHAR(50) | `cleaned_document_version` / `candidate` / `chunk_set` |
| entity_id | UUID | FK target row (soft, no DB-level FK due to polymorphism) |
| reviewer_id | UUID | FK users |
| action | ENUM(approve, reject, needs_revision, agree, disagree) | |
| reason | VARCHAR(500) | nullable |
| comment | TEXT | nullable |
| created_at | TIMESTAMPTZ | |

Index: `(entity_type, entity_id)`.

### 2.2 Column additions

#### `documents` — new columns
| Column | Type | Default | Notes |
|---|---|---|---|
| clean_status | ENUM(not_started, section_planned, in_progress, review_pending, completed) | `not_started` | R1+ clean business state (distinct from parse pipeline `status`) |
| active_clean_version_id | UUID | NULL | FK cleaned_document_versions, nullable |
| active_chunk_set_id | UUID | NULL | FK chunk_sets, nullable (future) |

#### `sections` — new columns
| Column | Type | Default | Notes |
|---|---|---|---|
| assignment_status | ENUM(unassigned, assigned, in_progress, completed, returned) | `unassigned` | R1+ ownership |
| assigned_to | UUID | NULL | FK users |
| assigned_by | UUID | NULL | FK users |
| assigned_at | TIMESTAMPTZ | NULL | |
| completed_at | TIMESTAMPTZ | NULL | |
| return_reason | VARCHAR(500) | NULL | admin return reason |

Existing `status` enum unchanged. Existing lease/comment/revision tables unchanged.

#### `chunks` — new columns (stub)
| Column | Type | Default |
|---|---|---|
| chunk_set_id | UUID (FK chunk_sets) | NULL |

#### `candidates` — new columns (stub, populated later)
| Column | Type | Default | Notes |
|---|---|---|---|
| author_id | UUID (FK users) | NULL | nullable for legacy rows |
| source_generation_batch_id | UUID (FK generation_batches) | NULL | |
| review_status | VARCHAR(30) | `'pending'` | NOT NULL; backfill sets 'pending' for existing rows |
| thinking_text | TEXT | NULL | |

`review_status` kept as VARCHAR (not ENUM) to avoid a schema change when future slice adds `peer_review`/`admin_spot_check` states.

#### `snapshot_manifests` — new columns (stub)
| Column | Type | Default |
|---|---|---|
| chunk_set_id | UUID | NULL |
| cleaned_version_id | UUID | NULL |
| generation_batch_id | UUID | NULL |

### 2.3 Backfill
Single data migration step inside the Alembic revision `upgrade()`:
- `UPDATE documents SET clean_status = 'not_started'` (NOT NULL, default covers new rows)
- `UPDATE sections SET assignment_status = 'unassigned'`
- `UPDATE candidates SET review_status = 'pending'`

### 2.4 State machine summary (new)

- `section.assignment_status`: `unassigned → assigned → in_progress → completed` with side branch `completed → returned → in_progress` (admin return).
- `document.clean_status`: `not_started → section_planned → in_progress → review_pending → completed`.
- `cleaned_document_version.status`: `review_pending → accepted | rejected`.

---

## 3. Clean Workflow Backend (P2)

### 3.1 New endpoints

Under `/api/projects/{pid}/documents/{did}/cleaning/`:

| Method | Path | Role | Behavior |
|---|---|---|---|
| POST | `/assign` | admin/reviewer | Body: `{ assignments: [{ section_ids: [uuid], assignee_id: uuid }] }`. Updates each section's `assigned_to`, `assigned_by`, `assigned_at`, `assignment_status='assigned'`. Sets `document.clean_status='section_planned'` or `'in_progress'` appropriately. Returns updated counts. |
| POST | `/merge` | admin/reviewer | Merges sections (ordered by `ordinal`) into one markdown blob. Writes MinIO `outputs/cleaned/{did}/v{n}.md`. Creates `cleaned_document_versions` row with `status='review_pending'`. Returns the new row. |
| POST | `/final-review` | admin/reviewer | Body: `{ version_id: uuid, action: 'accept'\|'reject', reason?: str, comment?: str }`. Writes a `review_records` entry. Updates version status. On accept, sets `document.clean_status='completed'` and `document.active_clean_version_id=<version>`. |
| GET | `/versions` | viewer+ | List CleanedDocumentVersion rows for the document (paginated). |

Plus a flat detail endpoint:

| Method | Path | Role | Behavior |
|---|---|---|---|
| GET | `/api/cleaned-versions/{vid}` | viewer+ | Returns version detail including `merged_markdown`. |

### 3.2 New endpoints under `/api/sections/`

| Method | Path | Role | Behavior |
|---|---|---|---|
| POST | `/{sid}/assign` | admin/reviewer | Body: `{ assignee_id: uuid }`. Assigns/reassigns a single section. Always allowed (admin override). |
| POST | `/{sid}/complete` | editor (if assigned) / admin | Marks section `assignment_status='completed'`, sets `completed_at`. Leaves `section.status` untouched (editor still needs to go through existing submit/accept flow if desired). |
| POST | `/{sid}/return` | admin/reviewer | Body: `{ reason: str }`. Sets `assignment_status='returned'`, `return_reason=<reason>`. Does not alter `assigned_to` (editor can work on it again). |

### 3.3 Authorization & invariants

- Editors can edit a section if: (a) it's assigned to them, OR (b) no assignment exists AND they hold a lease (legacy path, preserves existing tests).
- Admin/reviewer can edit any section, hold lease, and force-release.
- Merge only proceeds if every section has `cleaned_markdown IS NOT NULL OR raw_markdown IS NOT NULL` (soft check — uses cleaned if present else raw). Explicitly NOT requiring `status='accepted'` — admin takes responsibility for snapshot state.
- Merge is idempotent only at the row-version level: each call creates a new version.
- `document.clean_status` derivation:
  - On first section assignment: `section_planned` → flips to `in_progress` when any section reaches `in_progress` or `completed`
  - On first merge: `review_pending`
  - On accept: `completed`
  - On reject: stays `review_pending` until next merge

### 3.4 Services

- New `apps/api/app/services/clean_version_service.py`:
  - `async create_merged_version(db, document_id, user_id) -> CleanedDocumentVersion`
  - `async final_review(db, version_id, user_id, action, reason, comment) -> CleanedDocumentVersion`
  - `async list_versions(db, document_id) -> list[CleanedDocumentVersion]`
- `section_service.py` additions: `assign_sections`, `complete_section`, `return_section`.

### 3.5 WebSocket events (new, reuse existing plumbing)

- `section.assigned` (on assign / bulk-assign): `{ section_id, assigned_to }`
- `section.completed` (on section complete): `{ section_id }`
- `cleaned_version.created` (on merge)
- `cleaned_version.reviewed` (on final-review)

---

## 4. Clean Workflow Frontend (P2)

All changes confined to:

- `apps/web/src/app/(dashboard)/projects/[id]/documents/[did]/clean/page.tsx`
- `apps/web/src/lib/api.ts` (add typed methods for the new endpoints)

**Do not create new files** unless absolutely necessary.

### 4.1 Role-aware UI

Components keyed off `current_user.role` from auth context.

### 4.2 Additions to the existing 3-column page

1. **Sidebar header additions** (above existing status filter):
   - "分派筛选" chip group: `全部 / 分派给我 / 我未完成 / 未分派`
   - Editors default to "分派给我"; admins default to "全部"
2. **Per-section row**: small user-avatar-or-initial marker showing `assigned_to` name when assigned; colored ring for `assignment_status`
3. **Admin bulk-assign panel** (new collapsible at sidebar top, admin/reviewer only):
   - Multi-select checkboxes per section
   - Assignee dropdown (project members, role=editor+)
   - "批量分派" button
4. **Toolbar additions**:
   - For assigned editors: "完成 (分派)" button next to existing "提交审核"
   - For admins: "退回" button (when section assignment_status=completed)
5. **Document-level final-review bar** (new strip above the 3-column workspace):
   - Progress chip: "`X / Y` sections completed"
   - Admin: "生成合并版本" button (enabled when completed_count > 0)
   - Shows latest `CleanedDocumentVersion` status + admin accept/reject buttons if `review_pending`
   - Clicking the version opens a modal/drawer showing merged markdown preview

### 4.3 Checks Next.js 16 docs

Before writing frontend code, agent MUST read relevant guides in `apps/web/node_modules/next/dist/docs/` (per `apps/web/AGENTS.md`). Specifically any routing or server-component docs touched by this change.

---

## 5. Execution Plan

### Phase 1 — Foundation (sequential, single agent)
1. Write new SQLAlchemy models (`cleaned_document_version.py`, `chunk_set.py`, `generation_batch.py`, `review_record.py`).
2. Extend existing models (`document.py`, `section.py`, `chunk.py`, `generation.py`, `export.py`).
3. Write Pydantic schemas (new + extended).
4. Write one Alembic revision `r1plus_slice1_schema.py` with all new tables, columns, enums, and backfill.
5. Run migration locally (via Docker Compose PG) to confirm upgrade + downgrade round-trip.

### Phase 2 — Backend + Frontend (3 parallel agents)
- **Agent A (backend clean-version)**: `clean_version_service.py` + documents router (assign/merge/final-review/versions) + cleaned-versions router.
- **Agent B (backend sections)**: Extend `section_service.py` + sections router (assign/complete/return). Preserve existing endpoints.
- **Agent C (frontend)**: Update `clean/page.tsx` + `api.ts` per Section 4.

### Phase 3 — Review (subagent: code-reviewer)
Reviews all changes against this spec. Flags missing invariants, broken existing behavior, or scope creep.

### Phase 4 — Smoke test (single agent)
- Start DB in Docker
- Apply migration
- Curl smoke: upload → parse → clean/start → assign → complete all sections → merge → final-review
- Frontend: `npm run build` passes with zero errors
- Run existing pytest suite if present to confirm no regressions

### Phase 5 — Docs
- Append to `docs/logs/dev-log.md` with R1+ slice-1 entry
- Append to `docs/r1-testing-issues.md` if new issues found
- Commit all work with a single coherent commit message (only if user asks)

---

## 6. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Existing lease-based clean flow tests break | Additive-only columns; new endpoints new paths; existing endpoints untouched |
| Migration fails on production-like data | Backfill is unconditional; defaults cover new columns; no NOT NULL added to pre-existing columns |
| Frontend role-check bypass | Server enforces all auth; frontend role-UI is presentation only |
| Candidate `review_status` ENUM divergence | Use VARCHAR now; later slice can add CHECK constraint once states are stabilized |
| Agent scope drift | Each agent gets an explicit task list scoped to its file set; code-reviewer enforces |

---

## 7. Acceptance Criteria

- [ ] Alembic migration upgrade → downgrade → upgrade round-trip succeeds locally
- [ ] All 4 new tables + 16 new columns land correctly
- [ ] `POST /cleaning/assign`, `/merge`, `/final-review`, `GET /versions`, `GET /cleaned-versions/{vid}` work via curl
- [ ] `POST /sections/{sid}/assign`, `/complete`, `/return` work via curl
- [ ] Clean workbench frontend builds (`npm run build`) with zero errors, all new UI wires to endpoints
- [ ] Existing endpoints under `/sections/` and `/documents/` still return the same shapes (schema additions only)
- [ ] Dev log updated with slice-1 summary

---

## 8. Out of scope (next slices)

- Chunk workbench (P3) — chunk_sets usage, chunk statistics, admin confirm
- Generate workbench (P4) — generation_batches, candidate author tracking, non-author review enforcement
- Export bundle (P5) — new bundle structure, manifest expansion
- Frontend task panel additions, chunk/generate statistics
- Tests beyond smoke (unit tests, integration suite expansion)
