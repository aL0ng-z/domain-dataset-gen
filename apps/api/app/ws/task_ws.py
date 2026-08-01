import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.jwt import TokenType, resolve_user_id
from app.models.user import User
from domain.enums import UserRole

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _ws_auth_session():
    """创建 WS 授权专用的短生命周期数据库会话。

    不使用全局 ``app.database.async_session_factory``：其 engine 绑定创建时的事件
    循环，而 WS 处理器可能在 TestClient 等独立线程的循环中运行，跨循环使用连接池
    会触发 asyncpg ``another operation is in progress``。这里每次调用新建/销毁
    NullPool engine，完全循环安全；WS 连接与广播均非常驻热路径，开销可接受。
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            yield session
    finally:
        await engine.dispose()


class ConnectionManager:
    def __init__(self):
        # project_id -> {websocket: user_id}；记录连接归属，广播前复核用户状态。
        self.active_connections: dict[str, dict[WebSocket, str]] = {}
        self._subscriptions: dict[str, asyncio.Task] = {}

    async def connect(self, websocket: WebSocket, project_id: str, user_id: str):
        await websocket.accept()
        if project_id not in self.active_connections:
            self.active_connections[project_id] = {}
        self.active_connections[project_id][websocket] = user_id

        # Start Redis subscription if first connection for this project
        if project_id not in self._subscriptions:
            redis = websocket.app.state.redis
            self._subscriptions[project_id] = asyncio.create_task(
                self._subscribe(redis, project_id)
            )

    def disconnect(self, websocket: WebSocket, project_id: str):
        if project_id in self.active_connections:
            self.active_connections[project_id].pop(websocket, None)
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

    async def _verify_ws_access(self, user_id: str, project_id: str) -> bool:
        """广播前复核：用户仍启用，且仍为该项目 viewer 及以上成员。

        任一不满足返回 False；调用方先关闭再发送，避免撤权后继续收到事件
        （任务卡 §5.3）。数据库查询仅用于复核，不缓存（撤销需立即生效）。
        """
        from app.authz import check_project_member

        try:
            uid = uuid.UUID(user_id)
            pjid = uuid.UUID(project_id)
        except ValueError:
            return False
        try:
            async with _ws_auth_session() as session:
                user = (
                    await session.execute(select(User).where(User.id == uid))
                ).scalar_one_or_none()
                if user is None or not user.is_active:
                    return False
                await check_project_member(session, pjid, user, UserRole.viewer)
            return True
        except HTTPException:
            return False
        except Exception:
            return False

    async def _broadcast(self, project_id: str, message: str):
        if project_id not in self.active_connections:
            return
        # 撤权/停用的连接：先关闭再发送（任务卡 §5.3、§11 验收标准 8）。
        revoked = []
        live = {}
        for ws, user_id in list(self.active_connections[project_id].items()):
            if await self._verify_ws_access(user_id, project_id):
                live[ws] = user_id
            else:
                revoked.append(ws)

        self.active_connections[project_id] = live

        # 先关闭被撤权的连接。
        for ws in revoked:
            with contextlib.suppress(Exception):
                await ws.close(code=4403)

        if not live:
            return

        dead = set()
        for ws in live:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.active_connections[project_id].pop(ws, None)


manager = ConnectionManager()


# WebSocket 未认证 / 无权限统一关闭码（任务卡 §5.1）。
WS_UNAUTHORIZED_CLOSE_CODE = 4401
WS_FORBIDDEN_CLOSE_CODE = 4403


def validate_ws_token(token: str) -> str | None:
    """校验 access token 并返回 user_id，无效返回 None。

    仅接受 type=access 的令牌；收到 refresh token 同样拒绝。
    """
    try:
        user_id = resolve_user_id(token, TokenType.ACCESS)
        return str(user_id)
    except JWTError:
        return None


async def _authorize_ws(websocket: WebSocket, pid: str) -> tuple[str | None, int]:
    """accept 前完成 access-token、启用用户与项目 viewer 校验。

    返回 (user_id, close_code)：
    - 通过：user_id 非 None；
    - 认证失败（token 无效/用户不存在/已停用）-> close_code=4401；
    - 权限不足（非成员/角色不足/项目不存在）-> close_code=4403。
    任何失败都不会创建 Redis pubsub 订阅（订阅在 accept 后才启动）。
    """
    token = websocket.query_params.get("token")
    raw_user_id = None if not token else validate_ws_token(token)
    if raw_user_id is None:
        return None, WS_UNAUTHORIZED_CLOSE_CODE

    from app.authz import check_project_member

    try:
        user_id = uuid.UUID(raw_user_id)
        project_id = uuid.UUID(pid)
    except ValueError:
        return None, WS_UNAUTHORIZED_CLOSE_CODE

    try:
        async with _ws_auth_session() as session:
            user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if user is None or not user.is_active:
                logger.warning("WS 授权失败：用户不存在或已停用 %s", raw_user_id)
                return None, WS_UNAUTHORIZED_CLOSE_CODE
            # 项目 viewer 及以上成员；非成员 / 角色不足 / 项目不存在均关闭。
            await check_project_member(session, project_id, user, UserRole.viewer)
    except HTTPException as e:
        logger.warning("WS 授权失败：项目成员校验拒绝 %s pid=%s detail=%s", raw_user_id, pid, e.detail)
        return None, WS_FORBIDDEN_CLOSE_CODE
    except Exception as e:
        logger.warning("WS 授权失败：内部异常 %s pid=%s: %r", raw_user_id, pid, e)
        return None, WS_UNAUTHORIZED_CLOSE_CODE
    return str(user_id), 0


async def task_websocket_endpoint(websocket: WebSocket, pid: str):
    # accept 前完成 access-token、启用用户与项目 viewer 校验（任务卡 §5.3）。
    token = websocket.query_params.get("token")
    if not token or validate_ws_token(token) is None:
        await websocket.close(code=WS_UNAUTHORIZED_CLOSE_CODE)
        return

    user_id, close_code = await _authorize_ws(websocket, pid)
    if user_id is None:
        await websocket.close(code=close_code)
        return

    await manager.connect(websocket, pid, user_id)
    try:
        while True:
            # Keep connection alive, handle pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, pid)
