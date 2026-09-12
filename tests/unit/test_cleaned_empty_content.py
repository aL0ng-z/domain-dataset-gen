"""用户明确清空的章节不能在合并或历史修订中恢复原文。"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.clean_version_service import CleanVersionService
from app.services.section_service import SectionService


@pytest.mark.parametrize("cleaned, expected", [("", "保留章节\n"), (None, "删除的原文\n\n保留章节\n")])
async def test_merge_preserves_explicit_empty_content(cleaned, expected):
    document_id, job_id = uuid.uuid4(), uuid.uuid4()
    sections = [
        SimpleNamespace(id=uuid.uuid4(), content_revision=2, ordinal=0,
                        raw_markdown="删除的原文", cleaned_markdown=cleaned),
        SimpleNamespace(id=uuid.uuid4(), content_revision=0, ordinal=1,
                        raw_markdown="保留章节", cleaned_markdown="保留章节"),
    ]
    job_result = MagicMock()
    job_result.scalar_one_or_none.return_value = SimpleNamespace(id=job_id)
    sections_result = MagicMock()
    sections_result.scalars.return_value.all.return_value = sections
    doc_result = MagicMock()
    doc_result.scalar_one_or_none.return_value = SimpleNamespace(clean_status="in_cleaning")
    max_result = MagicMock()
    max_result.scalar_one.return_value = 0
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[job_result, sections_result, doc_result, max_result])
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    service = CleanVersionService(db)
    service._storage = MagicMock()
    service._load_locked_sections = AsyncMock(return_value=sections)

    version = await service.create_merged_version(document_id, job_id, uuid.uuid4())

    assert version.merged_markdown == expected
    assert service._storage.upload_file.call_args.args[2].decode("utf-8") == expected


@pytest.mark.parametrize("cleaned, expected", [("", ""), (None, "已删除噪声")])
async def test_next_edit_revision_keeps_empty_previous_content(cleaned, expected):
    section_id = uuid.uuid4()
    section = SimpleNamespace(cleaned_markdown=cleaned, raw_markdown="已删除噪声",
                              content_revision=2, assignment_status="in_progress")
    result = MagicMock()
    result.scalar_one_or_none.return_value = section
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    service = SectionService(db)
    service._require_lease = AsyncMock()

    updated = await service.update_section(section_id, "新正文", uuid.uuid4(), 2, uuid.uuid4())

    revision = db.add.call_args.args[0]
    assert revision.cleaned_markdown == expected
    assert updated.cleaned_markdown == "新正文"
    assert updated.content_revision == 3
