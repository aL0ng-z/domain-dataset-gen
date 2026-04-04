# R1: Lab Pilot — Design Specification

> Date: 2026-04-04
> Status: Approved
> Scope: Release 1 (Sprints S0-S8), full MVP pipeline
> Implementation approach: Layer-first (infra → models → backend → frontend → integration)

---

## 1. Project Structure

### Repository Layout

```
dataset-gen/
├── apps/
│   ├── api/                        # FastAPI backend
│   │   ├── pyproject.toml
│   │   ├── alembic.ini
│   │   ├── app/
│   │   │   ├── main.py             # FastAPI app entry + CORS + lifespan
│   │   │   ├── config.py           # Pydantic Settings (env vars)
│   │   │   ├── database.py         # SQLAlchemy async engine + session factory
│   │   │   ├── dependencies.py     # get_db, get_current_user, require_role, require_project_member
│   │   │   ├── models/             # SQLAlchemy ORM models (one file per domain)
│   │   │   │   ├── __init__.py
│   │   │   │   ├── user.py
│   │   │   │   ├── project.py
│   │   │   │   ├── config.py       # model_configs, parser/chunk/export_profiles, task_policies
│   │   │   │   ├── document.py
│   │   │   │   ├── parse.py
│   │   │   │   ├── section.py
│   │   │   │   ├── chunk.py
│   │   │   │   ├── prompt_template.py
│   │   │   │   ├── generation.py   # generation_runs, candidates
│   │   │   │   ├── curated.py      # curated_items, curated_revisions, evidence_links
│   │   │   │   ├── dataset.py      # datasets, dataset_items, benchmarks, benchmark_cases
│   │   │   │   ├── export.py       # exports, snapshot_manifests
│   │   │   │   └── task.py         # tasks, llm_usage_logs
│   │   │   ├── schemas/            # Pydantic request/response schemas
│   │   │   │   ├── __init__.py
│   │   │   │   ├── auth.py
│   │   │   │   ├── user.py
│   │   │   │   ├── project.py
│   │   │   │   ├── config.py
│   │   │   │   ├── document.py
│   │   │   │   ├── section.py
│   │   │   │   ├── chunk.py
│   │   │   │   ├── prompt_template.py
│   │   │   │   ├── candidate.py
│   │   │   │   ├── curated.py
│   │   │   │   ├── dataset.py
│   │   │   │   ├── export.py
│   │   │   │   ├── task.py
│   │   │   │   └── monitoring.py
│   │   │   ├── routers/            # FastAPI routers (one per domain)
│   │   │   │   ├── __init__.py
│   │   │   │   ├── auth.py
│   │   │   │   ├── users.py
│   │   │   │   ├── projects.py
│   │   │   │   ├── config.py       # 5 profile sub-routers
│   │   │   │   ├── documents.py
│   │   │   │   ├── sections.py
│   │   │   │   ├── chunks.py
│   │   │   │   ├── prompt_templates.py
│   │   │   │   ├── candidates.py
│   │   │   │   ├── curated_items.py
│   │   │   │   ├── datasets.py
│   │   │   │   ├── benchmarks.py
│   │   │   │   ├── exports.py
│   │   │   │   ├── tasks.py
│   │   │   │   └── monitoring.py
│   │   │   ├── services/           # Business logic layer
│   │   │   │   ├── __init__.py
│   │   │   │   ├── auth_service.py
│   │   │   │   ├── user_service.py
│   │   │   │   ├── project_service.py
│   │   │   │   ├── config_service.py
│   │   │   │   ├── document_service.py
│   │   │   │   ├── section_service.py
│   │   │   │   ├── chunk_service.py
│   │   │   │   ├── prompt_template_service.py
│   │   │   │   ├── candidate_service.py
│   │   │   │   ├── curated_item_service.py
│   │   │   │   ├── dataset_service.py
│   │   │   │   ├── benchmark_service.py
│   │   │   │   ├── export_service.py
│   │   │   │   ├── task_service.py
│   │   │   │   └── monitoring_service.py
│   │   │   ├── workers/            # Background task functions
│   │   │   │   ├── __init__.py
│   │   │   │   ├── parse_worker.py
│   │   │   │   ├── clean_worker.py
│   │   │   │   ├── chunk_worker.py
│   │   │   │   ├── generate_worker.py
│   │   │   │   └── export_worker.py
│   │   │   └── ws/                 # WebSocket
│   │   │       ├── __init__.py
│   │   │       └── task_ws.py
│   │   └── migrations/
│   │       ├── env.py
│   │       └── versions/
│   └── web/                        # Next.js 15 frontend
│       ├── package.json
│       ├── next.config.ts
│       ├── tailwind.config.ts
│       ├── tsconfig.json
│       ├── components.json         # shadcn/ui config
│       └── src/
│           ├── app/                # App Router pages (see Section 6)
│           ├── components/         # Shared UI components
│           │   ├── ui/             # shadcn/ui primitives
│           │   ├── sidebar.tsx
│           │   ├── project-tabs.tsx
│           │   ├── data-table.tsx
│           │   ├── status-badge.tsx
│           │   ├── task-floating-panel.tsx
│           │   ├── file-uploader.tsx
│           │   ├── confirm-dialog.tsx
│           │   └── pagination.tsx
│           ├── lib/
│           │   ├── api.ts          # Fetch wrapper + auth token injection
│           │   ├── ws.ts           # WebSocket client + reconnect
│           │   ├── auth.ts         # JWT storage, refresh logic
│           │   └── utils.ts
│           ├── hooks/
│           │   ├── use-auth.ts
│           │   ├── use-ws.ts
│           │   └── use-pagination.ts
│           └── contexts/
│               ├── auth-context.tsx
│               └── ws-context.tsx
├── libs/
│   ├── domain/
│   │   ├── pyproject.toml
│   │   └── domain/
│   │       ├── __init__.py
│   │       ├── enums.py
│   │       └── schemas.py          # BaseSchema, PaginatedResponse, ErrorResponse
│   ├── parsing/
│   │   ├── pyproject.toml
│   │   └── parsing/
│   │       ├── __init__.py
│   │       ├── base.py             # BaseParser, ParseResult
│   │       ├── mock_parser.py      # pymupdf4llm-based mock
│   │       └── mineru_parser.py    # Stub for future MinerU
│   ├── cleaning/
│   │   ├── pyproject.toml
│   │   └── cleaning/
│   │       ├── __init__.py
│   │       └── splitter.py         # split_into_sections()
│   ├── splitters/
│   │   ├── pyproject.toml
│   │   └── splitters/
│   │       ├── __init__.py
│   │       ├── base.py             # BaseChunker, ChunkData
│   │       └── hybrid_heading.py   # hybrid_heading_recursive
│   ├── llm/
│   │   ├── pyproject.toml
│   │   └── llm/
│   │       ├── __init__.py
│   │       ├── client.py           # LLMClient (OpenAI-compatible)
│   │       └── usage.py            # Usage logging callback
│   └── storage/
│       ├── pyproject.toml
│       └── storage/
│           ├── __init__.py
│           └── minio_client.py     # upload, download, presigned_url, delete, ensure_bucket
├── infra/
│   └── docker/
│       ├── docker-compose.yml
│       ├── docker-compose.dev.yml
│       ├── Dockerfile.api
│       ├── Dockerfile.web
│       ├── init-minio.sh
│       └── .env.example
├── scripts/
│   └── init_seed.py
├── pyproject.toml                  # Root uv workspace
├── CLAUDE.md
└── README.md
```

---

## 2. Database Models

### 2.1 All Tables

All tables use UUID primary keys and `timestamptz` timestamps.

#### Core

**users**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK, default uuid4 |
| username | VARCHAR(50) | unique, not null |
| email | VARCHAR(255) | unique, not null |
| password_hash | VARCHAR(255) | bcrypt |
| role | ENUM(admin, reviewer, editor, viewer) | not null |
| is_active | BOOLEAN | default true |
| created_at | TIMESTAMPTZ | default now |
| updated_at | TIMESTAMPTZ | on update |

**projects**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| name | VARCHAR(200) | not null |
| description | TEXT | nullable |
| created_by | UUID | FK users |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

**project_members**
| Column | Type | Notes |
|--------|------|-------|
| project_id | UUID | FK projects, composite PK |
| user_id | UUID | FK users, composite PK |
| role | ENUM(admin, reviewer, editor, viewer) | project-level role |
| joined_at | TIMESTAMPTZ | |

#### Configuration Tables

All 5 config tables share this base pattern:

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| project_id | UUID | FK projects |
| name | VARCHAR(200) | not null |
| version | INTEGER | default 1, auto-increment on update |
| is_default | BOOLEAN | default false (one per project per type) |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

**model_configs** — additional columns:
| Column | Type |
|--------|------|
| provider | VARCHAR(50) |
| base_url | VARCHAR(500) |
| api_key_encrypted | VARCHAR(500) |
| model_name | VARCHAR(200) |
| temperature | FLOAT |
| max_tokens | INTEGER |
| extra_params | JSONB |

**parser_profiles** — additional columns:
| Column | Type |
|--------|------|
| parser_name | VARCHAR(50) (mineru / paddleocr / mock) |
| parser_options | JSONB |

**chunk_profiles** — additional columns:
| Column | Type |
|--------|------|
| strategy | VARCHAR(50) (hybrid_heading_recursive) |
| max_tokens | INTEGER (default 512) |
| overlap_tokens | INTEGER (default 50) |
| options | JSONB |

**export_profiles** — additional columns:
| Column | Type |
|--------|------|
| format | ENUM(sft_jsonl, qa_json, messages, alpaca, sharegpt, benchmark_json) |
| template_options | JSONB |

**task_policies** — additional columns:
| Column | Type |
|--------|------|
| task_type | VARCHAR(50) |
| max_retries | INTEGER (default 3) |
| timeout_seconds | INTEGER (default 300) |
| concurrency_limit | INTEGER (default 5) |

#### Document Pipeline

**documents**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| project_id | UUID | FK projects |
| filename | VARCHAR(500) | |
| file_size | BIGINT | bytes |
| sha256 | VARCHAR(64) | unique per project |
| minio_key | VARCHAR(500) | |
| status | ENUM(uploaded, parsing, parsed, cleaning, cleaned, chunking, chunked, generating, generated) | |
| page_count | INTEGER | nullable |
| uploaded_by | UUID | FK users |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint: `(project_id, sha256)`

**parse_jobs**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| document_id | UUID | FK documents |
| parser_profile_id | UUID | FK parser_profiles |
| status | ENUM(queued, processing, completed, failed) | |
| raw_markdown_key | VARCHAR(500) | MinIO key |
| structured_json_key | VARCHAR(500) | MinIO key |
| page_mapping | JSONB | |
| error_message | TEXT | |
| started_at | TIMESTAMPTZ | |
| completed_at | TIMESTAMPTZ | |
| created_at | TIMESTAMPTZ | |

**cleaning_jobs**
| Column | Type |
|--------|------|
| id | UUID |
| document_id | UUID FK documents |
| parse_job_id | UUID FK parse_jobs |
| status | ENUM(queued, processing, completed, failed) |
| started_by | UUID FK users |
| created_at | TIMESTAMPTZ |
| completed_at | TIMESTAMPTZ |

**sections**
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| cleaning_job_id | UUID | FK cleaning_jobs |
| document_id | UUID | FK documents |
| ordinal | INTEGER | ordering within document |
| heading_path | VARCHAR(500) | e.g. "第3章 > 3.1 叶片设计" |
| source_pages | JSONB | array of page numbers |
| raw_markdown | TEXT | from parse job |
| cleaned_markdown | TEXT | edited version |
| status | ENUM(draft, in_cleaning, review_pending, accepted, rejected) | |
| cleaned_by | UUID | FK users, nullable |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

**section_leases**
| Column | Type |
|--------|------|
| id | UUID |
| section_id | UUID FK sections |
| user_id | UUID FK users |
| acquired_at | TIMESTAMPTZ |
| expires_at | TIMESTAMPTZ |
| released_at | TIMESTAMPTZ (nullable) |

**section_comments**
| Column | Type |
|--------|------|
| id | UUID |
| section_id | UUID FK sections |
| user_id | UUID FK users |
| comment_type | ENUM(parse_issue, ocr_issue, layout_issue, general) |
| content | TEXT |
| created_at | TIMESTAMPTZ |

**section_revisions**
| Column | Type |
|--------|------|
| id | UUID |
| section_id | UUID FK sections |
| revised_by | UUID FK users |
| cleaned_markdown | TEXT |
| revision_note | VARCHAR(500) |
| created_at | TIMESTAMPTZ |

**chunks**
| Column | Type |
|--------|------|
| id | UUID |
| section_id | UUID FK sections |
| document_id | UUID FK documents |
| ordinal | INTEGER |
| heading_path | VARCHAR(500) |
| content | TEXT |
| source_pages | JSONB |
| token_count | INTEGER |
| status | ENUM(ready, generating, generated) |
| created_at | TIMESTAMPTZ |
| updated_at | TIMESTAMPTZ |

**prompt_templates**
| Column | Type |
|--------|------|
| id | UUID |
| project_id | UUID FK projects |
| task_type | ENUM(knowledge_extraction, qa_generation, benchmark_case) |
| name | VARCHAR(200) |
| version | INTEGER (auto-increment) |
| system_prompt | TEXT |
| user_prompt_template | TEXT |
| input_schema | JSONB |
| output_schema | JSONB |
| is_default | BOOLEAN |
| created_at | TIMESTAMPTZ |
| updated_at | TIMESTAMPTZ |

**prompt_template_versions**
| Column | Type |
|--------|------|
| id | UUID |
| template_id | UUID FK prompt_templates |
| version | INTEGER |
| system_prompt | TEXT |
| user_prompt_template | TEXT |
| input_schema | JSONB |
| output_schema | JSONB |
| created_at | TIMESTAMPTZ |

On each `PATCH /prompt-templates/{id}`, a snapshot of the current state is saved to `prompt_template_versions` before applying the update and incrementing the version number.

**generation_runs**
| Column | Type |
|--------|------|
| id | UUID |
| chunk_id | UUID FK chunks |
| prompt_template_id | UUID FK prompt_templates |
| model_config_id | UUID FK model_configs |
| context_mode | VARCHAR(20) (single_chunk) |
| input_prompt | TEXT |
| raw_output | TEXT |
| status | ENUM(queued, processing, completed, failed) |
| error_message | TEXT |
| created_at | TIMESTAMPTZ |
| completed_at | TIMESTAMPTZ |

**candidates**
| Column | Type |
|--------|------|
| id | UUID |
| generation_run_id | UUID FK generation_runs |
| chunk_id | UUID FK chunks |
| content | JSONB |
| candidate_type | VARCHAR(50) |
| status | ENUM(ai_generated, human_edited, review_pending, approved, rejected) |
| reviewed_by | UUID FK users (nullable) |
| review_verdict | ENUM(supported, partially_supported, unsupported, out_of_scope) (nullable) |
| review_evidence_spans | JSONB |
| reject_reason | TEXT |
| created_at | TIMESTAMPTZ |
| updated_at | TIMESTAMPTZ |

**candidate_comments**
| Column | Type |
|--------|------|
| id | UUID |
| candidate_id | UUID FK candidates |
| user_id | UUID FK users |
| content | TEXT |
| created_at | TIMESTAMPTZ |

**curated_items**
| Column | Type |
|--------|------|
| id | UUID |
| project_id | UUID FK projects |
| candidate_id | UUID FK candidates |
| content | JSONB |
| item_type | VARCHAR(50) |
| status | ENUM(draft, approved, exported, deprecated) |
| promoted_by | UUID FK users |
| created_at | TIMESTAMPTZ |
| updated_at | TIMESTAMPTZ |

**curated_revisions**
| Column | Type |
|--------|------|
| id | UUID |
| curated_item_id | UUID FK curated_items |
| revised_by | UUID FK users |
| content | JSONB |
| revision_note | VARCHAR(500) |
| created_at | TIMESTAMPTZ |

**evidence_links**
| Column | Type |
|--------|------|
| id | UUID |
| curated_item_id | UUID FK curated_items |
| document_id | UUID FK documents |
| chunk_id | UUID FK chunks |
| source_pages | JSONB |
| heading_path | VARCHAR(500) |
| quote_text | TEXT |

#### Dataset / Export

**datasets**
| Column | Type |
|--------|------|
| id | UUID |
| project_id | UUID FK projects |
| name | VARCHAR(200) |
| description | TEXT |
| status | ENUM(draft, finalized) |
| created_by | UUID FK users |
| created_at | TIMESTAMPTZ |
| updated_at | TIMESTAMPTZ |

**dataset_items**
| Column | Type |
|--------|------|
| id | UUID |
| dataset_id | UUID FK datasets |
| curated_item_id | UUID FK curated_items |
| ordinal | INTEGER |

Constraint: curated_item must have status=approved.

**benchmarks**
| Column | Type |
|--------|------|
| id | UUID |
| project_id | UUID FK projects |
| name | VARCHAR(200) |
| description | TEXT |
| status | ENUM(draft, finalized) |
| created_by | UUID FK users |
| created_at | TIMESTAMPTZ |
| updated_at | TIMESTAMPTZ |

**benchmark_cases**
| Column | Type |
|--------|------|
| id | UUID |
| benchmark_id | UUID FK benchmarks |
| curated_item_id | UUID FK curated_items |
| ordinal | INTEGER |

Constraint: curated_item must have status=approved AND review_verdict on source candidate must be `supported` (evidence_grounded).

**exports**
| Column | Type |
|--------|------|
| id | UUID |
| project_id | UUID FK projects |
| dataset_id | UUID FK datasets (nullable) |
| benchmark_id | UUID FK benchmarks (nullable) |
| export_profile_id | UUID FK export_profiles |
| minio_key | VARCHAR(500) |
| format | VARCHAR(50) |
| item_count | INTEGER |
| snapshot_manifest_id | UUID FK snapshot_manifests |
| created_by | UUID FK users |
| created_at | TIMESTAMPTZ |

**snapshot_manifests**
| Column | Type |
|--------|------|
| id | UUID |
| manifest | JSONB |
| created_at | TIMESTAMPTZ |

Note: `exports.snapshot_manifest_id` references `snapshot_manifests.id`. The manifest is created first during export, then the export row references it. No circular FK.

The `manifest` JSONB contains frozen references to: parse_job version, cleaning_job, chunk_profile version, prompt_template versions, model_config versions, curated_item versions with their content hashes.

#### Infrastructure

**tasks**
| Column | Type |
|--------|------|
| id | UUID |
| project_id | UUID FK projects |
| task_type | VARCHAR(50) (parse, clean, chunk, generate, generate_batch, export) |
| entity_type | VARCHAR(50) (document, section, chunk, dataset, benchmark) |
| entity_id | UUID |
| parent_task_id | UUID FK tasks (nullable, for batch subtasks) |
| status | ENUM(queued, processing, completed, failed, cancelled) |
| progress | INTEGER (0-100) |
| error_message | TEXT |
| created_by | UUID FK users |
| created_at | TIMESTAMPTZ |
| started_at | TIMESTAMPTZ |
| completed_at | TIMESTAMPTZ |

**llm_usage_logs**
| Column | Type |
|--------|------|
| id | UUID |
| project_id | UUID FK projects |
| task_id | UUID FK tasks (nullable) |
| model_config_id | UUID FK model_configs |
| prompt_template_id | UUID FK prompt_templates (nullable) |
| input_tokens | INTEGER |
| output_tokens | INTEGER |
| latency_ms | INTEGER |
| status | VARCHAR(20) (success, error) |
| error_message | TEXT |
| created_at | TIMESTAMPTZ |

### 2.2 State Machines

**Document**: `uploaded → parsing → parsed → cleaning → cleaned → chunking → chunked → generating → generated`
**Section**: `draft → in_cleaning → review_pending → accepted / rejected`
**Chunk**: `ready → generating → generated`
**Candidate**: `ai_generated → human_edited → review_pending → approved / rejected`
**CuratedItem**: `draft → approved → exported → deprecated`
**Task**: `queued → processing → completed / failed / cancelled`

### 2.3 Key Constraints

- Cascade deletes: project deletion cascades to all child entities
- SHA256 unique per project: `UNIQUE(project_id, sha256)` on documents
- `dataset_items` only accept curated_items with status `approved`
- `benchmark_cases` only accept curated_items whose source candidate has `review_verdict = supported`
- One `is_default` per config type per project (enforced in application logic)

---

## 3. Authentication & Authorization

### 3.1 Auth Flow

1. **Register** (`POST /api/auth/register`): admin-only. Creates user with bcrypt hash.
2. **Login** (`POST /api/auth/login`): returns JWT access token (30min) + refresh token (7d). Refresh token as httpOnly cookie.
3. **Refresh** (`POST /api/auth/refresh`): exchanges valid refresh token for new pair.
4. **JWT payload**: `{ sub: user_id, role: global_role, exp, iat }`

### 3.2 Role Permissions

| Action | admin | reviewer | editor | viewer |
|--------|-------|----------|--------|--------|
| Create/manage users | Y | | | |
| Create/delete projects | Y | | | |
| Manage project members | Y (+ project owner) | | | |
| Config profile CRUD | Y | Y | Y | |
| Upload documents | Y | Y | Y | |
| Trigger parse/chunk/generate | Y | Y | Y | |
| Edit sections (with lease) | Y | Y | Y | |
| Submit sections for review | Y | Y | Y | |
| Review sections | Y | Y | | |
| Review candidates | Y | Y | | |
| Promote to CuratedItem | Y | Y | | |
| Edit CuratedItem | Y | Y | Y | |
| Manage datasets/benchmarks | Y | Y | Y | |
| Export | Y | Y | Y | |
| View everything | Y | Y | Y | Y |
| Force-release leases | Y | Y | | |

### 3.3 Implementation

- `get_current_user` dependency: decodes JWT from `Authorization: Bearer <token>`, raises 401 if invalid.
- `require_role(min_role)` dependency: checks role hierarchy `admin > reviewer > editor > viewer`, raises 403.
- `require_project_member(min_role)` dependency: checks project_members table, raises 403. Global admin bypasses.

---

## 4. API Endpoints

All endpoints prefixed with `/api/`.

### 4.1 Auth (`/api/auth/`)
- `POST /register` — admin-only, create user
- `POST /login` — returns access + refresh tokens
- `POST /refresh` — refresh token exchange
- `GET /me` — current user info

### 4.2 Users (`/api/users/`) — admin only
- `GET /` — list users
- `GET /{uid}` — get user
- `PATCH /{uid}` — update user
- `DELETE /{uid}` — deactivate user

### 4.3 Projects (`/api/projects/`)
- `POST /` — create project (admin)
- `GET /` — list projects (filtered by membership)
- `GET /{pid}` — get project
- `PATCH /{pid}` — update project
- `DELETE /{pid}` — delete project (admin)
- `POST /{pid}/members` — add member
- `GET /{pid}/members` — list members
- `DELETE /{pid}/members/{uid}` — remove member
- `POST /{pid}/clone-config-from/{source_pid}` — clone all configs

### 4.4 Config Profiles

5 routers with identical CRUD pattern. Example for model_configs:

`/api/projects/{pid}/model-configs/`
- `POST /` — create
- `GET /` — list
- `GET /{id}` — get
- `PATCH /{id}` — update (auto version++)
- `DELETE /{id}` — delete
- `POST /{id}/set-default` — set as project default

ModelConfig additionally:
- `POST /{id}/test` — connectivity test (call LLM with "ping")

Same pattern for: `parser-profiles`, `chunk-profiles`, `export-profiles`, `task-policies`.

### 4.5 Documents (`/api/projects/{pid}/documents/`)
- `POST /upload` — magic bytes check + SHA256 dedup + MinIO upload
- `GET /` — list (paginated, filterable by status)
- `GET /{did}` — get document detail
- `DELETE /{did}` — delete document
- `POST /{did}/parse` — trigger async parse (parser_profile_id in body)
- `GET /{did}/parse-jobs` — list parse jobs
- `GET /parse-jobs/{jid}` — get parse job detail
- `POST /{did}/cleaning/start` — trigger section splitting
- `GET /{did}/sections` — list sections
- `POST /{did}/chunk` — trigger async chunking (chunk_profile_id in body)
- `GET /{did}/chunks` — list chunks (paginated, filterable)
- `POST /{did}/generate-batch` — batch generate candidates

### 4.6 Sections (`/api/sections/`)
- `GET /{sid}` — get section
- `PATCH /{sid}` — update cleaned_markdown
- `POST /{sid}/submit` — submit for review
- `POST /{sid}/review` — accept or reject
- `POST /{sid}/lease/acquire` — acquire edit lease
- `POST /{sid}/lease/heartbeat` — extend lease
- `POST /{sid}/lease/release` — release lease
- `POST /{sid}/comments` — add comment
- `GET /{sid}/comments` — list comments
- `GET /{sid}/revisions` — list revisions

### 4.7 Chunks (`/api/chunks/`)
- `GET /{cid}` — get chunk
- `PATCH /{cid}` — update chunk
- `POST /{cid}/generate` — single chunk generation (template_id + model_config_id)

### 4.8 Candidates (`/api/candidates/`)
- `GET /{cid}` — get candidate
- `PATCH /{cid}` — edit candidate content
- `POST /{cid}/review` — submit review verdict + evidence spans
- `POST /{cid}/comments` — add comment
- `GET /{cid}/comments` — list comments
- `POST /{cid}/promote-to-curated` — create CuratedItem from approved candidate

### 4.9 Prompt Templates (`/api/projects/{pid}/prompt-templates/`)
- `POST /` — create template
- `GET /` — list (filterable by `?task_type=`)
- `GET /{id}` — get template
- `PATCH /{id}` — update (auto version++)
- `POST /{id}/duplicate` — clone template
- `POST /{id}/test-run` — select chunk, call LLM, return result
- `GET /{id}/versions` — list version history

### 4.10 CuratedItems (`/api/projects/{pid}/curated-items/`)
- `GET /` — list (filterable by type, status, source_type)
- `GET /{id}` — get detail
- `PATCH /{id}` — edit (creates revision)
- `GET /{id}/revisions` — list revisions
- `POST /{id}/add-to-dataset` — add to dataset (dataset_id in body)
- `POST /{id}/add-to-benchmark` — add to benchmark (benchmark_id in body)

### 4.11 Datasets (`/api/projects/{pid}/datasets/`)
- `POST /` — create
- `GET /` — list
- `GET /{did}` — get detail
- `PATCH /{did}` — update
- `DELETE /{did}` — delete
- `GET /{did}/items` — list items
- `POST /{did}/items` — add curated item
- `DELETE /{did}/items/{item_id}` — remove item
- `POST /{did}/export` — trigger export (export_profile_id in body)

### 4.12 Benchmarks (`/api/projects/{pid}/benchmarks/`)
- `POST /` — create
- `GET /` — list
- `GET /{bid}` — get detail
- `PATCH /{bid}` — update
- `DELETE /{bid}` — delete
- `GET /{bid}/cases` — list cases
- `POST /{bid}/cases` — add curated item
- `DELETE /{bid}/cases/{case_id}` — remove case
- `POST /{bid}/export` — trigger export

### 4.13 Exports (`/api/projects/{pid}/exports/`)
- `GET /` — list exports
- `GET /{eid}` — get export detail
- `GET /{eid}/manifest` — get snapshot manifest
- `GET /{eid}/download` — download exported file (presigned URL redirect)

### 4.14 Tasks (`/api/projects/{pid}/tasks/`)
- `GET /` — list tasks (paginated, filterable by type/status)
- `GET /{tid}` — get task detail
- `POST /{tid}/cancel` — cancel task
- `POST /{tid}/retry` — retry failed task

### 4.15 Monitoring (`/api/projects/{pid}/monitoring/`)
- `GET /summary` — total tokens, total cost estimate, task counts
- `GET /by-task-type` — usage grouped by task type
- `GET /by-model` — usage grouped by model
- `GET /daily-trend` — daily token usage over time
- `GET /by-template` — usage grouped by prompt template

### 4.16 WebSocket
- `WS /ws/projects/{pid}/tasks` — JWT auth via query param, broadcasts task state changes

### 4.17 Pagination Convention

All list endpoints accept `?page=1&page_size=20` and return:
```json
{
  "items": [...],
  "total": 123,
  "page": 1,
  "page_size": 20
}
```

---

## 5. Libraries

### 5.1 libs/domain/

Shared types:
- **Enums**: `UserRole`, `DocumentStatus`, `SectionStatus`, `ChunkStatus`, `CandidateStatus`, `CuratedItemStatus`, `TaskStatus`, `TaskType`, `CommentType`, `ReviewVerdict`, `ExportFormat`, `ContextMode`, `PromptTaskType`
- **Base schemas**: `BaseSchema` (Pydantic with `from_attributes=True`), `PaginatedResponse[T]` (generic), `ErrorResponse`
- No business logic.

### 5.2 libs/storage/

MinIO S3 wrapper:
- `StorageClient(endpoint, access_key, secret_key)`
- `upload_file(bucket, key, data) → str`
- `download_file(bucket, key) → bytes`
- `get_presigned_url(bucket, key, expires=3600) → str`
- `delete_file(bucket, key)`
- `ensure_bucket(bucket)`
- Two buckets: `documents` (raw PDFs), `outputs` (parse results, exports)

### 5.3 libs/parsing/

PDF parser with pluggable backends:
- `BaseParser.parse(pdf_path) → ParseResult`
- `ParseResult`: `raw_markdown: str`, `structured_json: dict`, `page_mapping: list[dict]`
- `MockParser`: uses `pymupdf4llm` for real PDF→markdown conversion with heading detection
- `MineruParser`: stub class, raises NotImplementedError with message to configure MinerU

### 5.4 libs/cleaning/

Section splitting:
- `split_into_sections(raw_markdown, document_id) → list[SectionData]`
- Splits on H1 headings; falls back to H2, then single section
- Produces: `heading_path`, `source_pages`, `ordinal`, `raw_markdown` per section

### 5.5 libs/splitters/

Chunking strategies:
- `BaseChunker.chunk(section_markdown, heading_path, config) → list[ChunkData]`
- `HybridHeadingRecursiveChunker`: split by sub-headings → recursively split paragraphs if over max_tokens → overlap between chunks
- `ChunkData`: `content`, `heading_path`, `source_pages`, `token_count`
- Token counting: `tiktoken` with `cl100k_base`

### 5.6 libs/llm/

OpenAI-compatible client:
- `LLMClient(base_url, api_key, model_name, temperature, max_tokens)`
- `async chat_completion(messages, response_format=None) → LLMResponse`
- `LLMResponse`: `content`, `input_tokens`, `output_tokens`, `latency_ms`
- Auto usage logging via callback
- 3x retry with exponential backoff on 429/5xx
- Optional JSON mode with Pydantic validation

---

## 6. Frontend

### 6.1 Page Structure

```
src/app/
├── layout.tsx                          # Root: sidebar + auth provider + WS provider
├── login/page.tsx                      # Login (public)
├── projects/
│   ├── page.tsx                        # Project list + create dialog
│   └── [id]/
│       ├── layout.tsx                  # Project layout: tabs navigation
│       ├── page.tsx                    # Redirects to documents
│       ├── documents/
│       │   ├── page.tsx                # Document list + drag-drop upload
│       │   └── [did]/
│       │       ├── page.tsx            # Document detail + parse/clean triggers
│       │       ├── clean/page.tsx      # 4-column cleaning workbench
│       │       └── chunks/
│       │           ├── page.tsx        # Chunk list
│       │           └── [cid]/page.tsx  # Chunk detail + generation panel
│       ├── templates/
│       │   ├── page.tsx                # Template list grouped by task_type
│       │   └── [tid]/page.tsx          # Template editor + test-run
│       ├── candidates/page.tsx         # Candidate list + review panel
│       ├── curated/
│       │   ├── page.tsx                # CuratedItem list
│       │   └── [id]/page.tsx           # Detail + revisions + evidence
│       ├── datasets/
│       │   ├── page.tsx                # Dataset list + create
│       │   └── [did]/page.tsx          # Items + export
│       ├── benchmarks/
│       │   ├── page.tsx                # Benchmark list + create
│       │   └── [bid]/page.tsx          # Cases + export
│       ├── exports/page.tsx            # Export history + manifest viewer
│       ├── tasks/page.tsx              # Task center
│       ├── monitoring/page.tsx         # LLM usage charts
│       └── settings/page.tsx           # 5-tab config profiles
```

### 6.2 Key Components

- `Sidebar`: project list, current project, user menu with logout
- `ProjectTabs`: Documents / Templates / Candidates / Curated / Datasets / Benchmarks / Exports / Tasks / Monitoring / Settings
- `DataTable`: reusable paginated table with column sorting and filters (shadcn Table)
- `StatusBadge`: colored badge per entity status
- `TaskFloatingPanel`: bottom-right badge (running task count), expands to task list with WebSocket updates
- `FileUploader`: drag-and-drop PDF upload with progress bar
- `ConfirmDialog`: reusable confirmation modal
- `Pagination`: page controls

### 6.3 Cleaning Workbench

4-column resizable layout (CSS grid + drag handles):
1. **PDF Viewer** (left): `react-pdf` rendering, page navigation, section page highlighting
2. **Raw Markdown** (center-left): read-only, syntax-highlighted `<pre>`
3. **Markdown Editor** (center-right): CodeMirror 6 (`@codemirror/lang-markdown`)
4. **Preview + Actions** (right): `react-markdown` live preview + comment list + review buttons

Section sidebar: collapsible list with status filter chips, click-to-navigate.
Lease UI: "Locked by [user]" banner, edit disabled when no lease, 30s heartbeat interval.

### 6.4 Template Editor

Split pane:
- Left: system prompt + user prompt template fields (CodeMirror)
- Right: chunk selector dropdown + test-run button + LLM response display

### 6.5 Candidate Review Panel

- Source chunk content display
- Evidence highlighting (if spans provided)
- Verdict selector: supported / partially_supported / unsupported / out_of_scope
- Evidence span input (text fields for quote ranges)
- Reject reason textarea
- "Promote to Curated" button (visible only when status=approved)

### 6.6 API Client (`src/lib/api.ts`)

- Fetch wrapper: auto-injects `Authorization: Bearer` header
- On 401: calls `/api/auth/refresh`, retries original request once
- JWT stored in localStorage (access), httpOnly cookie (refresh)
- All responses typed to match Pydantic schemas

### 6.7 WebSocket (`src/lib/ws.ts`)

- Single connection per project: `ws://host/ws/projects/{pid}/tasks`
- Auto-reconnect with exponential backoff (1s, 2s, 4s, max 30s)
- React context provider for subscription from any component
- Events: `task.created`, `task.progress`, `task.completed`, `task.failed`

### 6.8 UI Conventions

- All UI text in Chinese
- shadcn/ui components throughout
- Responsive: cleaning workbench collapses to 2 columns below 1280px
- No dark mode in R1

---

## 7. Workers & Background Tasks

### 7.1 Worker Functions

| Worker | Trigger Endpoint | Steps |
|--------|-----------------|-------|
| `parse_document` | `POST /documents/{did}/parse` | Download PDF from MinIO → call parser → upload markdown + JSON to MinIO → update document status to `parsed` |
| `clean_start` | `POST /documents/{did}/cleaning/start` | Load parse job markdown → `libs/cleaning/` split → bulk insert sections → update document to `cleaning` |
| `chunk_document` | `POST /documents/{did}/chunk` | Load accepted sections → `libs/splitters/` chunk → bulk insert chunks → update document to `chunked` |
| `generate_single` | `POST /chunks/{cid}/generate` | Build prompt (template + chunk) → call LLM → parse output → create generation_run + candidate → log usage |
| `generate_batch` | `POST /documents/{did}/generate-batch` | Create parent task → iterate chunks → spawn subtasks → track aggregate progress |
| `export_dataset` | `POST /datasets/{did}/export` | Load curated items → format per export profile → build snapshot manifest → upload to MinIO |
| `export_benchmark` | `POST /benchmarks/{bid}/export` | Same as dataset, benchmark_json format |

### 7.2 Worker Pattern

Each worker:
1. Receives `task_id` as argument
2. Sets task status to `processing`
3. Performs work, updating `progress` (0-100) periodically
4. On success: sets status to `completed`
5. On failure: sets status to `failed`, records `error_message`
6. After each status change: publishes to Redis pub/sub `project:{pid}:tasks`

### 7.3 WebSocket Architecture

```
Worker → updates task in DB → publishes to Redis channel "project:{pid}:tasks"
                                        ↓
FastAPI WS endpoint subscribes to Redis channel
                                        ↓
Broadcasts to all connected WebSocket clients for that project
```

Message format:
```json
{
  "event": "task.progress",
  "task_id": "uuid",
  "task_type": "parse",
  "status": "processing",
  "progress": 45,
  "entity_type": "document",
  "entity_id": "uuid",
  "timestamp": "2026-04-04T12:00:00Z"
}
```

Connection management:
- Server dict: `project_id → set[WebSocket]`
- On connect: validate JWT from `?token=` query param
- Redis sub created per project on first client, removed on last disconnect

### 7.4 Section Lease Expiry

- `acquire`: set Redis key `lease:section:{sid}` with TTL=120s + write DB row
- `heartbeat`: refresh Redis TTL + update `expires_at`
- `release`: delete Redis key + set `released_at`
- Periodic cleanup (every 30s background task): query expired leases, mark released

---

## 8. Docker Compose

### 8.1 Services

```yaml
services:
  postgres:    # PostgreSQL 16-alpine, volume: pgdata
  redis:       # Redis 7-alpine
  minio:       # MinIO, volume: miniodata, console on :9001
  minio-init:  # mc client, creates 'documents' + 'outputs' buckets
  api:         # FastAPI (uvicorn), port 8000
  web:         # Next.js, port 3000
```

### 8.2 Dev Override (`docker-compose.dev.yml`)

- Source mounts for hot reload
- API: `uvicorn app.main:app --reload`
- Web: `npm run dev`
- Exposes PG (5432), Redis (6379), MinIO (9000/9001) to host

### 8.3 Init Seed Script

`scripts/init_seed.py` (run manually or via entrypoint):
- Creates admin user: `admin` / `admin123`
- Creates default project: "压气机知识抽取"
- Seeds 3 prompt templates: knowledge_extraction, qa_generation, benchmark_case
- Seeds default profiles for parser, chunk, export, task policy

---

## 9. Implementation Strategy

Layer-first approach with subagent parallelism:

**Layer 1 — Infrastructure**
- Git init + uv workspace + pyproject.toml files
- Docker Compose (dev + prod)
- FastAPI skeleton (main.py, config, database, health check)
- Next.js skeleton (layout, auth provider, routing shell)
- All Alembic migrations (single batch for 26 tables)
- libs/ package scaffolds

**Layer 2 — Backend (parallelizable)**
- All SQLAlchemy models
- All Pydantic schemas
- All services + routers (independent modules in parallel)
- All workers
- WebSocket endpoint
- libs/ implementations (storage, parsing, cleaning, splitters, llm)

**Layer 3 — Frontend (parallelizable)**
- Shared components (sidebar, data-table, etc.)
- All pages (independent pages in parallel)
- API client + WebSocket client
- Cleaning workbench (complex, dedicated focus)

**Layer 4 — Integration**
- Seed script
- End-to-end wiring verification
- Docker Compose final configuration
