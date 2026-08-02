"""T02 PDF 授权：query token 拒绝、Bearer+同项目 viewer 才返回、授权先于 MinIO。

覆盖任务卡 §11 验收标准 6、§5.2：
- query token 被拒绝（401），不触发 MinIO 下载；
- 正确 Bearer + 同项目 viewer 才返回 PDF（application/pdf）；
- did 不属于 pid -> 404，且授权校验完成前不访问 MinIO；
- 非成员 viewer -> 403，不访问 MinIO；
- Cache-Control: private, no-store。
"""


import pytest
from httpx import AsyncClient

PASSWORD = "password-123"


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    res = await client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class _StorageRecorder:
    """记录 download_file 调用；默认返回合法 PDF 字节。

    生产 StorageClient.download_file 是同步方法（被 asyncio.to_thread 调用），
    因此这里的 mock 也必须是同步函数，否则 to_thread 返回协程导致渲染失败。
    """

    def __init__(self):
        self.download_calls: list[tuple[str, str]] = []
        self._data = b"%PDF-1.4 fake content"

    def download_file(self, bucket: str, key: str) -> bytes:
        self.download_calls.append((bucket, key))
        return self._data

    def upload_file(self, *args, **kwargs):
        raise AssertionError("PDF 授权测试不应触发上传")


@pytest.mark.integration
async def test_pdf_query_token_rejected(client: AsyncClient, full_resources, monkeypatch):
    """query token 一律拒绝（401），且不访问 MinIO。"""
    from app.routers import documents as doc_router

    recorder = _StorageRecorder()
    monkeypatch.setattr(doc_router, "get_storage_client", lambda *a, **k: recorder)

    tokens = await _login(client, "viewer_user")
    r = full_resources["projects"]["a"]
    url = f"/api/projects/{r['pid']}/documents/{r['document'].id}/file"

    # query token（合法 access token 也不行，必须走 Bearer）
    res = await client.get(f"{url}?token={tokens['access_token']}")
    assert res.status_code == 401, res.text
    assert recorder.download_calls == [], "query token 不得触发 MinIO 下载"


@pytest.mark.integration
async def test_pdf_requires_bearer_and_viewer(client: AsyncClient, full_resources, monkeypatch):
    """正确 Bearer + 同项目 viewer 才返回 PDF；非成员 403；均不访问 MinIO。"""
    from app.routers import documents as doc_router

    recorder = _StorageRecorder()
    monkeypatch.setattr(doc_router, "get_storage_client", lambda *a, **k: recorder)

    tokens = await _login(client, "viewer_user")
    r = full_resources["projects"]["a"]
    url = f"/api/projects/{r['pid']}/documents/{r['document'].id}/file"

    res = await client.get(url, headers=_bearer(tokens["access_token"]))
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/pdf")
    assert "no-store" in res.headers.get("cache-control", "").lower()
    assert recorder.download_calls, "同项目 viewer 应触发一次 MinIO 下载"


@pytest.mark.integration
async def test_pdf_cross_project_document_404_no_minio(client: AsyncClient, full_resources, monkeypatch):
    """pid=A + 文档 B -> 404，且不访问 MinIO（授权先于存储）。"""
    from app.routers import documents as doc_router

    recorder = _StorageRecorder()
    monkeypatch.setattr(doc_router, "get_storage_client", lambda *a, **k: recorder)

    tokens = await _login(client, "viewer_user")
    a = full_resources["projects"]["a"]
    b = full_resources["projects"]["b"]
    url = f"/api/projects/{a['pid']}/documents/{b['document'].id}/file"

    res = await client.get(url, headers=_bearer(tokens["access_token"]))
    assert res.status_code == 404, res.text
    assert recorder.download_calls == [], "跨项目文档不得触发 MinIO 下载"


@pytest.mark.integration
async def test_pdf_non_member_403_no_minio(client: AsyncClient, full_resources, make_user, make_project, db_session, monkeypatch):
    """非成员访问项目 PDF -> 403，不访问 MinIO。"""
    from app.routers import documents as doc_router

    recorder = _StorageRecorder()
    monkeypatch.setattr(doc_router, "get_storage_client", lambda *a, **k: recorder)

    user = await make_user.create("pdf_outsider", "viewer")
    await make_project.create("PDF 外部项目", user.id)
    await db_session.commit()
    tokens = await _login(client, "pdf_outsider")
    r = full_resources["projects"]["a"]
    url = f"/api/projects/{r['pid']}/documents/{r['document'].id}/file"

    res = await client.get(url, headers=_bearer(tokens["access_token"]))
    assert res.status_code == 403, res.text
    assert recorder.download_calls == [], "非成员不得触发 MinIO 下载"
