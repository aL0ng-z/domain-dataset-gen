from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from app.routers import auth, users, projects
from app.routers.config import (
    model_config_router, parser_profile_router, chunk_profile_router,
    export_profile_router, task_policy_router,
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(projects.router)
app.include_router(model_config_router)
app.include_router(parser_profile_router)
app.include_router(chunk_profile_router)
app.include_router(export_profile_router)
app.include_router(task_policy_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


app.websocket("/ws/projects/{pid}/tasks")(task_websocket_endpoint)
