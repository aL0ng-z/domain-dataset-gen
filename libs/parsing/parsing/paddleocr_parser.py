from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any

from parsing.base import BaseParser, ParseResult


class PaddleOCRParser(BaseParser):
    """PaddleOCR official document parsing API wrapper."""

    def parse(self, pdf_data: bytes) -> ParseResult:
        endpoint = self._resolve_endpoint()
        api_key = str(self.options.get("api_key", "")).strip()
        if not api_key:
            raise ValueError("PaddleOCR API 解析需要在服务端配置 PADDLEOCR_API_TOKEN")

        payload: dict[str, Any] = {
            "file": base64.b64encode(pdf_data).decode("ascii"),
            "fileType": 0,
            "useDocOrientationClassify": self.options.get("use_doc_orientation_classify", False),
            "useDocUnwarping": self.options.get("use_doc_unwarping", False),
            "useTextlineOrientation": self.options.get("use_textline_orientation", False),
            "visualize": self.options.get("visualize", False),
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"{self.options.get('auth_scheme', 'token')} {api_key}",
        }
        timeout = self._timeout()
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            raise ValueError(f"PaddleOCR API HTTP 错误 ({exc.code}): {body}") from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"无法连接 PaddleOCR 服务: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("PaddleOCR API 返回了无法解析的 JSON") from exc

        pages = self._result_pages(result)
        markdown_parts: list[str] = []
        compact_pages: list[dict[str, Any]] = []
        page_mapping: list[dict[str, int]] = []
        for index, page in enumerate(pages):
            markdown = page.get("markdown")
            text = markdown.get("text") if isinstance(markdown, dict) else None
            if not isinstance(text, str):
                raise ValueError(f"PaddleOCR 第 {index + 1} 页响应缺少 Markdown 文本")
            page_markdown = text.strip()
            markdown_start = sum(len(part) + 2 for part in markdown_parts)
            markdown_parts.append(page_markdown)
            compact_pages.append(
                {
                    "page_number": index + 1,
                    "markdown": page_markdown,
                    "pruned_result": page.get("prunedResult"),
                }
            )
            page_mapping.append({
                "page_number": index + 1,
                "markdown_start": markdown_start,
                "markdown_end": markdown_start + len(page_markdown),
            })

        return ParseResult(
            raw_markdown="\n\n".join(markdown_parts).strip() + "\n",
            structured_json={
                "parser": "paddleocr",
                "provider": "paddleocr_official_api",
                "page_count": len(pages),
                "data_info": result.get("result", {}).get("dataInfo"),
                "pages": compact_pages,
            },
            page_mapping=page_mapping,
        )

    def _resolve_endpoint(self) -> str:
        endpoint = str(self.options.get("base_url", "")).rstrip("/")
        if not endpoint:
            raise ValueError("PaddleOCR 解析器需要配置 API 地址 (base_url)")
        if not endpoint.endswith("/layout-parsing"):
            raise ValueError("PaddleOCR API 地址必须是以 /layout-parsing 结尾的完整端点")
        return endpoint

    def _timeout(self) -> float:
        try:
            timeout = float(self.options.get("timeout_seconds", 600))
        except (TypeError, ValueError) as exc:
            raise ValueError("PaddleOCR 参数 timeout_seconds 必须为数字") from exc
        if timeout <= 0:
            raise ValueError("PaddleOCR 参数 timeout_seconds 必须大于 0")
        return timeout

    def _result_pages(self, result: dict) -> list[dict]:
        if result.get("errorCode") not in (None, 0):
            raise ValueError(f"PaddleOCR API 错误: {result.get('errorMsg', '未知错误')}")
        body = result.get("result")
        pages = body.get("layoutParsingResults") if isinstance(body, dict) else None
        if not isinstance(pages, list) or not pages:
            raise ValueError("PaddleOCR API 响应缺少 result.layoutParsingResults")
        if not all(isinstance(page, dict) for page in pages):
            raise ValueError("PaddleOCR API 页级响应格式错误")
        return pages
