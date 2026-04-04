import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from app.config import settings


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, set[WebSocket]] = {}
        self._subscriptions: dict[str, asyncio.Task] = {}

    async def connect(self, websocket: WebSocket, project_id: str):
        await websocket.accept()
        if project_id not in self.active_connections:
            self.active_connections[project_id] = set()
        self.active_connections[project_id].add(websocket)

        # Start Redis subscription if first connection for this project
        if project_id not in self._subscriptions:
            redis = websocket.app.state.redis
            self._subscriptions[project_id] = asyncio.create_task(
                self._subscribe(redis, project_id)
            )

    def disconnect(self, websocket: WebSocket, project_id: str):
        if project_id in self.active_connections:
            self.active_connections[project_id].discard(websocket)
            if not self.active_connections[project_id]:
                del self.active_connections[project_id]
                # Cancel subscription
                if project_id in self._subscriptions:
                    self._subscriptions[project_id].cancel()
                    del self._subscriptions[project_id]

    async def _subscribe(self, redis, project_id: str):
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"project:{project_id}:tasks")
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    await self._broadcast(project_id, data)
        except asyncio.CancelledError:
            await pubsub.unsubscribe(f"project:{project_id}:tasks")
            await pubsub.close()

    async def _broadcast(self, project_id: str, message: str):
        if project_id not in self.active_connections:
            return
        dead = set()
        for ws in self.active_connections[project_id]:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.active_connections[project_id].discard(ws)


manager = ConnectionManager()


def validate_ws_token(token: str) -> str | None:
    """Validate JWT and return user_id, or None if invalid."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except JWTError:
        return None


async def task_websocket_endpoint(websocket: WebSocket, pid: str):
    # Validate token from query params
    token = websocket.query_params.get("token")
    if not token or validate_ws_token(token) is None:
        await websocket.close(code=4001)
        return

    await manager.connect(websocket, pid)
    try:
        while True:
            # Keep connection alive, handle pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, pid)
