from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.errors import register_exception_handlers
from app.openapi import build_custom_openapi
from app.routers import (
    auth,
    benchmarks,
    candidates,
    chunk_sets,
    chunks,
    cleaned_versions,
    curated_items,
    datasets,
    documents,
    exports,
    monitoring,
    projects,
    prompt_templates,
    sections,
    tasks,
    users,
)
from app.routers.config import (
    chunk_profile_router,
    export_profile_router,
    model_config_router,
    parser_profile_router,
    task_policy_router,
)
from app.ws.task_ws import task_websocket_endpoint


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    yield
    # Shutdown
    await app.state.redis.close()


app = FastAPI(
    title="Dataset Gen API",
    description="Compressor Knowledge Extraction Platform",
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(projects.router)
app.include_router(model_config_router)
app.include_router(parser_profile_router)
app.include_router(chunk_profile_router)
app.include_router(export_profile_router)
app.include_router(task_policy_router)
app.include_router(documents.router)
app.include_router(sections.router)
app.include_router(tasks.router)
app.include_router(monitoring.router)
app.include_router(chunks.router)
app.include_router(chunk_sets.router)
app.include_router(prompt_templates.router)
app.include_router(candidates.router)
app.include_router(curated_items.router)
app.include_router(datasets.router)
app.include_router(benchmarks.router)
app.include_router(exports.router)
app.include_router(cleaned_versions.router)

app.openapi = lambda: build_custom_openapi(app)


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


app.websocket("/ws/projects/{pid}/tasks")(task_websocket_endpoint)
