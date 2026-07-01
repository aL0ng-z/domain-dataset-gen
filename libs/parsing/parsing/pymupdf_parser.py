import os
import tempfile

import pymupdf
import pymupdf4llm

from parsing.base import BaseParser, ParseResult


class PymupdfParser(BaseParser):
    """Local PDF parser using pymupdf4llm. No API needed."""

    def parse(self, pdf_data: bytes) -> ParseResult:
        # pymupdf4llm.to_markdown needs a file path, write temp file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_data)
            tmp_path = tmp.name

        try:
            page_chunks = pymupdf4llm.to_markdown(tmp_path, show_progress=False, page_chunks=True)
            doc = pymupdf.open(tmp_path)
            page_mapping = []
            pages = []
            markdown_parts = []
            has_page_chunks = isinstance(page_chunks, list)
            for i, page in enumerate(doc):
                chunk = page_chunks[i] if has_page_chunks and i < len(page_chunks) else {}
                page_markdown = chunk.get("text") if isinstance(chunk, dict) else ""
                if not isinstance(page_markdown, str):
                    page_markdown = ""
                page_markdown = page_markdown.strip()
                markdown_start = sum(len(part) + 2 for part in markdown_parts)

                mapping = {
                    "page_number": i + 1,
                    "width": page.rect.width,
                    "height": page.rect.height,
                }
                if has_page_chunks:
                    markdown_parts.append(page_markdown)
                    mapping["markdown_start"] = markdown_start
                    mapping["markdown_end"] = markdown_start + len(page_markdown)
                    pages.append({
                        "page_number": i + 1,
                        "markdown": page_markdown,
                    })
                page_mapping.append(mapping)
            page_count = len(doc)
            doc.close()
            if has_page_chunks:
                md_text = "\n\n".join(markdown_parts).strip() + "\n"
            else:
                md_text = str(pymupdf4llm.to_markdown(tmp_path, show_progress=False)).strip() + "\n"
        finally:
            os.unlink(tmp_path)

        return ParseResult(
            raw_markdown=md_text,
            structured_json={"page_count": page_count, "pages": pages},
            page_mapping=page_mapping,
        )
