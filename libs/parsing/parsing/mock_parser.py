import pymupdf4llm

from parsing.base import BaseParser, ParseResult


class MockParser(BaseParser):
    def parse(self, pdf_path: str) -> ParseResult:
        md_text = pymupdf4llm.to_markdown(pdf_path, show_progress=False)

        import pymupdf
        doc = pymupdf.open(pdf_path)
        page_mapping = []
        for i, page in enumerate(doc):
            page_mapping.append({
                "page_number": i + 1,
                "width": page.rect.width,
                "height": page.rect.height,
            })
        page_count = len(doc)
        doc.close()

        return ParseResult(
            raw_markdown=md_text,
            structured_json={"page_count": page_count},
            page_mapping=page_mapping,
        )
