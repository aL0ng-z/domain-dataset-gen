"""WebSocket 广播不得在异步授权复核期间丢失新连接。"""

import asyncio

import pytest

from app.ws.task_ws import ConnectionManager


class _Socket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, message: str) -> None:
        self.messages.append(message)

    async def close(self, code: int) -> None:
        del code


@pytest.mark.asyncio
async def test_broadcast_keeps_connection_added_while_access_check_waits():
    manager = ConnectionManager()
    project_id = "project"
    existing, late = _Socket(), _Socket()
    manager.active_connections[project_id] = {existing: "existing-user"}
    checking = asyncio.Event()
    release = asyncio.Event()

    async def verify(_user_id: str, _project_id: str) -> bool:
        checking.set()
        await release.wait()
        return True

    manager._verify_ws_access = verify  # type: ignore[method-assign]
    broadcast = asyncio.create_task(manager._broadcast(project_id, '{"event":"task.completed"}'))
    await checking.wait()
    manager.active_connections[project_id][late] = "late-user"
    release.set()
    await broadcast

    assert existing.messages == ['{"event":"task.completed"}']
    assert late in manager.active_connections[project_id]
