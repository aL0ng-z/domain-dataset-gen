import base64
import json
import urllib.request
import urllib.error

from parsing.base import BaseParser, ParseResult


class PaddleOCRParser(BaseParser):
    """API-based parser using PaddleOCR / PP-StructureV3 service.

    Required options:
        base_url: PaddleOCR API endpoint (e.g. http://localhost:8011)
        api_key: API key for authentication (if required)
    """

    def parse(self, pdf_data: bytes) -> ParseResult:
        base_url = self.options.get("base_url", "").rstrip("/")
        api_key = self.options.get("api_key", "")

        if not base_url:
            raise ValueError("PaddleOCR 解析器需要配置 API 地址 (base_url)")

        # Encode PDF as base64 for API transmission
        pdf_b64 = base64.b64encode(pdf_data).decode("utf-8")

        payload = json.dumps({
            "file": pdf_b64,
            "file_type": "pdf",
            "use_doc_orientation_classify": True,
            "use_doc_unwarping": True,
            "use_textline_orientation": True,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(
            f"{base_url}/api/v1/extract",
            data=payload,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise ValueError(f"PaddleOCR API 错误 ({e.code}): {body}")
        except urllib.error.URLError as e:
            raise ValueError(f"无法连接 PaddleOCR 服务: {e.reason}")

        # Extract markdown from response
        raw_markdown = result.get("markdown", result.get("content", ""))
        page_count = result.get("page_count", 0)
        page_mapping = result.get("page_mapping", [])

        return ParseResult(
            raw_markdown=raw_markdown,
            structured_json={"page_count": page_count, "parser": "paddleocr"},
            page_mapping=page_mapping,
        )
