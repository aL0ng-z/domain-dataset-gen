import re
from dataclasses import dataclass


@dataclass
class SectionData:
    ordinal: int
    heading_path: str
    raw_markdown: str
    source_pages: list[int]


def split_into_sections(raw_markdown: str) -> list[SectionData]:
    """Split markdown into sections by top-level headings.
    Tries H1 first, falls back to H2, then treats entire doc as one section.
    """
    sections = _split_by_heading_level(raw_markdown, level=1)
    if len(sections) <= 1:
        sections = _split_by_heading_level(raw_markdown, level=2)
    if len(sections) == 0:
        sections = [SectionData(ordinal=0, heading_path="", raw_markdown=raw_markdown, source_pages=[])]
    return sections


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
