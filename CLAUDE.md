# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Compressor Knowledge Extraction Platform** — a web-based platform for extracting domain knowledge from PDF textbooks/manuals on compressor (压气机) design, producing fine-tuning datasets and evaluation benchmarks. Designed for university research labs (3-20 users), not commercial SaaS.

The engineering plan is in `compressor-knowledge-platform-engineering-plan.md` (V2).

### Core Pipeline

```
Upload PDF → Parse (MinerU) → Clean/Verify (Section-level) → Chunk → LLM Generate Candidates → Human Review → CuratedItem → Dataset/Benchmark Export
```

### Key Domain Objects

```
Document → Section → Chunk → Candidate → CuratedItem → Dataset / Benchmark
```

- **Section**: cleaning/collaboration unit, split by top-level headings
- **Chunk**: fine-grained text block for LLM processing and evidence tracing
- **Candidate**: LLM-generated draft (not final)
- **CuratedItem**: human-approved formal knowledge asset; the only source for Dataset/Benchmark exports

## Tech Stack

- **Frontend**: Next.js (App Router) + TypeScript + Tailwind CSS + shadcn/ui — in `apps/web/`
- **Backend**: FastAPI + Pydantic + SQLAlchemy 2.x + Alembic — in `apps/api/`
- **Database**: PostgreSQL
- **Cache/Tasks**: Redis + FastAPI BackgroundTasks (MVP); Celery for Phase 2
- **File Storage**: MinIO (S3-compatible)
- **Document Parsing**: MinerU (primary); PaddleOCR/PP-StructureV3 (Phase 2 fallback)
- **LLM**: OpenAI-compatible gateway (vLLM or external providers)

## Planned Repository Structure

```
apps/web/          # Next.js frontend
apps/api/          # FastAPI backend
workers/           # Async task runners (parse, cleaning, generation, export)
libs/domain/       # DTOs, schemas, domain objects
libs/parsing/      # MinerU / PaddleOCR wrappers
libs/cleaning/     # Section splitting, markdown rendering, LLM check
libs/splitters/    # Chunking strategies
libs/llm/          # LLM provider adapter
libs/storage/      # MinIO / S3 wrapper
infra/docker/      # Docker Compose deployment
infra/migrations/  # Alembic migrations
```

## Architecture Decisions

- **Two-phase collaboration**: Section-level leases for cleaning; Chunk-level leases for knowledge generation. No real-time co-editing (CRDT) in MVP.
- **Candidate vs CuratedItem separation**: LLM outputs are candidates only. Formal knowledge assets require human promotion to CuratedItem.
- **Section-aware chunking**: Every chunk carries a `section_id` for traceability back to the cleaning phase.
- **Context modes for LLM generation**: `single_chunk` (local knowledge) and `section_context` (adjacent chunks + heading path) to reduce missing preconditions.
- **Immutable export snapshots**: Exports freeze all upstream versions (parse job, cleaning job, splitter config, prompt template, curated item versions).
- **Evidence-based review**: Reviewers judge `supported` / `partially_supported` / `unsupported` / `out_of_scope` with evidence spans, not just approve/reject.

## Development Phases

- **Phase 1 (MVP Skeleton)**: Minimal end-to-end chain — upload, parse, clean, chunk, single-template generation, simple export
- **Phase 2 (Lab-usable)**: Multi-user collaboration (leases, comments, review), CuratedItem layer, WebSocket status push, immutable snapshots
- **Phase 3 (Enhancements)**: PaddleOCR fallback, LLM cleaning suggestions, claim checks, semantic dedup, multi-model support

## Development Log

每次完成开发任务（阶段性里程碑，非微小步骤）后，必须更新项目根目录的 `dev-log.md`，内容包括：
- 项目总览状态表（R1/R2/R3/R4）
- 当前 Release 的逐模块详细进度
- 已知问题
- 下一步待办事项

## Key Conventions

- All content is in Chinese (压气机/compressor domain); the platform UI should support Chinese
- The system is designed for single-machine Docker Compose deployment
- Database uses UUID primary keys and `timestamptz` timestamps throughout
- State machines govern Document, Section, Chunk, Candidate, and CuratedItem lifecycles (see plan sections 3.2)
- API follows RESTful conventions under `/api/` prefix (see plan section 13 for full endpoint list)
