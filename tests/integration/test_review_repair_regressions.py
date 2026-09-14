"""本轮审查修复的高风险后端回归。"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.config import Settings
from app.models.config import ModelConfig
from app.models.document import Document
from app.routers.documents import MAX_UPLOAD_SIZE, upload_document
from app.services import document_service as document_service_module
from app.services.auth_service import AuthService
from app.services.config_service import ConfigService
from tests.integration.test_export_snapshot import _login, _make_approved_item

pytestmark = pytest.mark.integration


class _RecordingStorage:
    def __init__(self):
        self.deleted: list[tuple[str, str]] = []

    def delete_file(self, bucket: str, key: str) -> None:
        self.deleted.append((bucket, key))


class _OversizedUpload:
    filename = "too-large.pdf"
    size = MAX_UPLOAD_SIZE + 1

    def __init__(self):
        self.closed = False

    async def read(self, _size: int) -> bytes:
        raise AssertionError("已知超限文件不应读取内容")

    async def close(self) -> None:
        self.closed = True


async def test_document_and_parse_delete_keep_objects_when_fk_rejects(client, db_session, org, monkeypatch):
    """RESTRICT 回滚时，原始及解析对象都不能提前删除。"""
    project_id = org["projects"]["a"].id
    approved = await _make_approved_item(client, db_session, project_id)
    chain = approved["res"]
    document_id = chain["doc"].id
    parse_job_id = chain["parse_job"].id
    storage = _RecordingStorage()
    monkeypatch.setattr(document_service_module, "get_storage_client", lambda *args, **kwargs: storage)
    headers = await _login(client, "editor_user")

    document_delete = await client.delete(
        f"/api/projects/{project_id}/documents/{document_id}", headers=headers
    )
    assert document_delete.status_code == 409, document_delete.text
    assert document_delete.json()["code"] == "DOCUMENT_IN_USE"
    assert storage.deleted == []
    assert await db_session.get(Document, document_id) is not None

    parse_delete = await client.delete(
        f"/api/projects/{project_id}/documents/{document_id}/parse-jobs/{parse_job_id}",
        headers=headers,
    )
    assert parse_delete.status_code == 409, parse_delete.text
    assert parse_delete.json()["code"] == "DOCUMENT_IN_USE"
    assert storage.deleted == []


async def test_known_oversized_upload_is_rejected_before_reading():
    upload = _OversizedUpload()
    with pytest.raises(HTTPException) as excinfo:
        await upload_document(
            pid=uuid.uuid4(),
            file=upload,  # type: ignore[arg-type]
            db=SimpleNamespace(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )
    assert excinfo.value.status_code == 413
    assert upload.closed


async def test_register_overlap_and_special_character_database_url(db_session, make_user):
    first = await make_user.create("duplicate_username", "editor")
    second = await make_user.create("other_username", "editor")
    with pytest.raises(ValueError, match="已存在"):
        await AuthService(db_session).register(first.username, second.email, "password-123")

    database_url = Settings(
        postgres_host="db.example.test",
        postgres_port=5432,
        postgres_db="datasetgen",
        postgres_user="datasetgen",
        postgres_password="p@ss:/word",
    ).database_url
    assert database_url.host == "db.example.test"
    assert database_url.password == "p@ss:/word"


async def test_model_default_switches_are_serialized(db_session, org, _test_session_factory):
    project_id = org["projects"]["a"].id
    first = ModelConfig(
        project_id=project_id,
        name="first",
        provider="mock",
        base_url="http://model.example.test/v1",
        api_key_encrypted="key",
        model_name="first",
    )
    second = ModelConfig(
        project_id=project_id,
        name="second",
        provider="mock",
        base_url="http://model.example.test/v1",
        api_key_encrypted="key",
        model_name="second",
    )
    db_session.add_all([first, second])
    await db_session.commit()

    async def set_default(config_id):
        async with _test_session_factory() as session:
            selected = await ConfigService(session, ModelConfig).set_default(project_id, config_id)
            assert selected is not None
            await session.commit()

    await asyncio.gather(set_default(first.id), set_default(second.id))
    defaults = (
        await db_session.execute(
            select(ModelConfig.id).where(
                ModelConfig.project_id == project_id,
                ModelConfig.is_default.is_(True),
            )
        )
    ).scalars().all()
    assert len(defaults) == 1
