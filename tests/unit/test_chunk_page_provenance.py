"""物理页码沿清洗版本冻结区间传递到 Chunk。"""

import uuid
from types import SimpleNamespace

from app.services.clean_version_service import merge_sections_with_source_intervals
from app.workers.chunk_worker import (
    _load_source_intervals,
    _locate_chunk_source_ranges,
    _resolve_source_provenance,
)
from splitters import ChunkData


def test_merge_freezes_section_character_ranges_and_trusted_pages():
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    merged, intervals = merge_sections_with_source_intervals([
        SimpleNamespace(
            id=first_id, raw_markdown="第一页", cleaned_markdown="第一页", source_pages=[1],
        ),
        SimpleNamespace(
            id=second_id, raw_markdown="第二页", cleaned_markdown="第二页", source_pages=[2],
        ),
    ])

    assert merged == "第一页\n\n第二页\n"
    assert intervals == [
        {
            "section_id": str(first_id), "start_char": 0, "end_char": 3,
            "source_pages": [1], "page_mapping_status": "trusted",
        },
        {
            "section_id": str(second_id), "start_char": 5, "end_char": 8,
            "source_pages": [2], "page_mapping_status": "trusted",
        },
    ]


def test_chunk_pages_follow_frozen_ranges_including_overlap_not_page_text():
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    markdown = "Page 888\n\n第二页正文\n"
    intervals = _load_source_intervals([
        {
            "section_id": str(first_id), "start_char": 0, "end_char": 8,
            "source_pages": [1], "page_mapping_status": "trusted",
        },
        {
            "section_id": str(second_id), "start_char": 10, "end_char": 15,
            "source_pages": [2], "page_mapping_status": "trusted",
        },
    ])
    chunks = [
        ChunkData(ordinal=0, heading_path="", content="Page 888", source_content="Page 888"),
        ChunkData(
            ordinal=1,
            heading_path="",
            content="...\n\nPage 888\n\n第二页正文",
            source_content="第二页正文",
        ),
    ]

    ranges = _locate_chunk_source_ranges(markdown, chunks)
    first_section, first_pages = _resolve_source_provenance(ranges[0][1], intervals)
    second_section, second_pages = _resolve_source_provenance(ranges[1][1], intervals)

    assert first_section == first_id
    assert first_pages == [1]
    assert second_section == first_id
    assert second_pages == [1, 2]


def test_unknown_source_interval_keeps_chunk_pages_empty():
    section_id = uuid.uuid4()
    intervals = _load_source_intervals([{
        "section_id": str(section_id), "start_char": 0, "end_char": 4,
        "source_pages": [], "page_mapping_status": "unknown",
    }])

    section, pages = _resolve_source_provenance([(0, 4)], intervals)

    assert section == section_id
    assert pages == []
