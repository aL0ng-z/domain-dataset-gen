import re
from dataclasses import dataclass
from typing import Any


@dataclass
class SectionData:
    ordinal: int
    heading_path: str
    raw_markdown: str
    source_pages: list[int]


def split_into_sections(
    raw_markdown: str,
    *,
    page_mapping: list[dict[str, Any]] | None = None,
    structured_json: dict[str, Any] | None = None,
) -> list[SectionData]:
    """Split markdown into cleaning sections.

    Page-level parser output is preferred because the cleaning workbench is used
    to compare each parsed page against the original PDF page. Heading splitting
    remains as a legacy fallback for parse jobs that do not carry page content.
    """
    page_sections = _split_by_pages(raw_markdown, page_mapping, structured_json)
    if page_sections:
        return page_sections

    sections = _split_by_heading_level(raw_markdown, level=1)
    if len(sections) <= 1:
        sections = _split_by_heading_level(raw_markdown, level=2)
    if len(sections) == 0:
        sections = [SectionData(ordinal=0, heading_path="", raw_markdown=raw_markdown, source_pages=[])]
    return sections


def _split_by_pages(
    raw_markdown: str,
    page_mapping: list[dict[str, Any]] | None,
    structured_json: dict[str, Any] | None,
) -> list[SectionData]:
    sections = _sections_from_structured_pages(structured_json)
    if sections:
        return sections

    sections = _sections_from_markdown_ranges(raw_markdown, page_mapping)
    if sections:
        return sections

    return _sections_from_page_markers(raw_markdown)


def _sections_from_structured_pages(structured_json: dict[str, Any] | None) -> list[SectionData]:
    pages = structured_json.get("pages") if isinstance(structured_json, dict) else None
    if not isinstance(pages, list):
        return []

    sections: list[SectionData] = []
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        markdown = _page_markdown(page)
        if markdown is None:
            continue
        page_number = _page_number(page, index)
        sections.append(
            SectionData(
                ordinal=len(sections),
                heading_path=f"第 {page_number} 页",
                raw_markdown=markdown.strip(),
                source_pages=[page_number],
            )
        )
    return sections


def _sections_from_markdown_ranges(
    raw_markdown: str,
    page_mapping: list[dict[str, Any]] | None,
) -> list[SectionData]:
    if not isinstance(page_mapping, list):
        return []

    sections: list[SectionData] = []
    for index, item in enumerate(page_mapping):
        if not isinstance(item, dict):
            continue
        start = item.get("markdown_start")
        end = item.get("markdown_end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start < 0 or end < start or start > len(raw_markdown):
            continue
        page_number = _page_number(item, index)
        sections.append(
            SectionData(
                ordinal=len(sections),
                heading_path=f"第 {page_number} 页",
                raw_markdown=raw_markdown[start:min(end, len(raw_markdown))].strip(),
                source_pages=[page_number],
            )
        )
    return sections


def _sections_from_page_markers(raw_markdown: str) -> list[SectionData]:
    start_marker = re.compile(
        r"^\s*(?:<!--\s*(?:page|Page)\s*[:= ]\s*(\d+)\s*-->|#{1,6}\s*(?:第\s*)?(\d+)\s*页)\s*$",
        re.MULTILINE,
    )
    matches = list(start_marker.finditer(raw_markdown))
    if matches:
        sections: list[SectionData] = []
        for index, match in enumerate(matches):
            page_number = int(match.group(1) or match.group(2) or index + 1)
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_markdown)
            sections.append(
                SectionData(
                    ordinal=index,
                    heading_path=f"第 {page_number} 页",
                    raw_markdown=raw_markdown[start:end].strip(),
                    source_pages=[page_number],
                )
            )
        return sections

    end_marker = re.compile(r"^\s*---\s*end of page=(\d+)\s*---\s*$", re.MULTILINE)
    matches = list(end_marker.finditer(raw_markdown))
    if not matches:
        return []

    sections = []
    start = 0
    for index, match in enumerate(matches):
        raw_page = int(match.group(1))
        page_number = raw_page + 1 if raw_page == index else raw_page
        sections.append(
            SectionData(
                ordinal=index,
                heading_path=f"第 {page_number} 页",
                raw_markdown=raw_markdown[start:match.start()].strip(),
                source_pages=[page_number],
            )
        )
        start = match.end()
    tail = raw_markdown[start:].strip()
    if tail:
        page_number = len(sections) + 1
        sections.append(
            SectionData(
                ordinal=len(sections),
                heading_path=f"第 {page_number} 页",
                raw_markdown=tail,
                source_pages=[page_number],
            )
        )
    return sections


def _page_markdown(page: dict[str, Any]) -> str | None:
    markdown = page.get("markdown")
    if isinstance(markdown, str):
        return markdown
    if isinstance(markdown, dict):
        text = markdown.get("text")
        if isinstance(text, str):
            return text
    for key in ("raw_markdown", "md", "text", "content"):
        value = page.get(key)
        if isinstance(value, str):
            return value
    return None


def _page_number(page: dict[str, Any], index: int) -> int:
    for key in ("page_number", "page"):
        value = page.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    for key in ("page_idx", "page_index"):
        value = page.get(key)
        if isinstance(value, int):
            return value + 1
        if isinstance(value, str) and value.isdigit():
            return int(value) + 1
    return index + 1


def _split_by_heading_level(raw_markdown: str, level: int) -> list[SectionData]:
    pattern = re.compile(rf"^(#{{{level}}}) +(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(raw_markdown))

    if not matches:
        return []

    sections = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_markdown)
        heading_text = match.group(2).strip()
        section_text = raw_markdown[start:end].strip()
        page_nums = _extract_page_numbers(section_text)

        sections.append(SectionData(
            ordinal=i,
            heading_path=heading_text,
            raw_markdown=section_text,
            source_pages=page_nums,
        ))

    # Handle content before first heading
    preamble = raw_markdown[: matches[0].start()].strip()
    if preamble:
        sections.insert(
            0,
            SectionData(
                ordinal=-1,
                heading_path="前言",
                raw_markdown=preamble,
                source_pages=_extract_page_numbers(preamble),
            ),
        )

    # Renumber ordinals
    for i, section in enumerate(sections):
        section.ordinal = i

    return sections


def _extract_page_numbers(text: str) -> list[int]:
    page_pattern = re.compile(r"(?:page|Page|PAGE)\s*(\d+)", re.IGNORECASE)
    pages = sorted(set(int(m.group(1)) for m in page_pattern.finditer(text)))
    return pages
