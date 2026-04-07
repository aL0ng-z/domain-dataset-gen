import tempfile
import os

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
            md_text = pymupdf4llm.to_markdown(tmp_path, show_progress=False)

            doc = pymupdf.open(tmp_path)
            page_mapping = []
            for i, page in enumerate(doc):
                page_mapping.append({
                    "page_number": i + 1,
                    "width": page.rect.width,
                    "height": page.rect.height,
                })
            page_count = len(doc)
            doc.close()
        finally:
            os.unlink(tmp_path)

        return ParseResult(
            raw_markdown=md_text,
            structured_json={"page_count": page_count},
            page_mapping=page_mapping,
        )
