from __future__ import annotations

import base64
import json
from typing import Any

from parsing.base import BaseParser, ParseResult
from parsing.transport import InvalidJson, get_transport

DEFAULT_ENDPOINT = "http://127.0.0.1:9020/layout-parsing"


class PaddleOCRLocalServiceParser(BaseParser):
    """PaddleOCR-VL parser backed by a privately deployed PaddleX service.

    使用服务端 registry 派生的精确服务地址（managed-local）；不携带远程 provider Token。
    所有出站调用经统一安全 transport。
    """

    def parse(self, pdf_data: bytes) -> ParseResult:
        endpoint = self._resolve_endpoint()
        payload: dict[str, Any] = {
            "file": base64.b64encode(pdf_data).decode("ascii"),
            "fileType": 0,
            "useDocOrientationClassify": self.options.get("use_doc_orientation_classify", False),
            "useDocUnwarping": self.options.get("use_doc_unwarping", False),
            "useTextlineOrientation": self.options.get("use_textline_orientation", False),
            "useLayoutDetection": self._use_layout_detection(),
            "visualize": self.options.get("visualize", False),
        }
        if "max_new_tokens" in self.options:
            payload["maxNewTokens"] = self._positive_int("max_new_tokens")

        transport = get_transport()
        ml = self._managed_local()
        try:
            result, _ = transport.request_json(
                endpoint,
                method="POST",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                scheme=("https", "http"),
                allowed_port=ml.get("port"),
                allow_private_host=True,
                pinned_ips=tuple(ml.get("pinned_ips") or ()),
                read_timeout=self._timeout(),
            )
        except InvalidJson as exc:
            raise ValueError("PaddleOCR 本地服务返回了无法解析的 JSON") from exc

        pages = self._result_pages(result)
        markdown_parts: list[str] = []
        compact_pages: list[dict[str, Any]] = []
        page_mapping: list[dict[str, int]] = []
        for index, page in enumerate(pages):
            markdown = page.get("markdown")
            text = markdown.get("text") if isinstance(markdown, dict) else None
            if not isinstance(text, str):
                raise ValueError(f"PaddleOCR 本地服务第 {index + 1} 页响应缺少 Markdown 文本")
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
                "parser": "paddleocr_local_service",
                "provider": "paddleocr_private_paddlex_api",
                "page_count": len(pages),
                "data_info": result.get("result", {}).get("dataInfo"),
                "pages": compact_pages,
            },
            page_mapping=page_mapping,
        )

    def _security_context(self) -> dict[str, Any]:
        return self.options.get("_security") or {}

    def _managed_local(self) -> dict[str, Any]:
        return self._security_context().get("managed_local") or {}

    def _resolve_endpoint(self) -> str:
        """endpoint 由服务端 registry 派生（managed-local 精确地址）。"""
        security = self._security_context()
        base_url = str(security.get("base_url", DEFAULT_ENDPOINT)).strip().rstrip("/")
        if not base_url:
            raise ValueError("PaddleOCR 本地服务解析器需要配置服务端服务地址 (base_url)")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("PaddleOCR 本地服务地址必须以 http:// 或 https:// 开头")
        from urllib.parse import urlsplit

        parsed = urlsplit(base_url)
        path = parsed.path.rstrip("/")
        if path in {"", "/layout-parsing"}:
            return f"{parsed.scheme}://{parsed.netloc}/layout-parsing"
        raise ValueError("PaddleOCR 本地服务地址必须是服务根地址或以 /layout-parsing 结尾的端点")

    def _timeout(self) -> float:
        raw = self.options.get("parse_timeout_seconds", self.options.get("timeout_seconds", 1800))
        try:
            timeout = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("PaddleOCR 本地服务参数 parse_timeout_seconds 必须为数字") from exc
        if timeout <= 0:
            raise ValueError("PaddleOCR 本地服务参数 parse_timeout_seconds 必须大于 0")
        return timeout

    def _use_layout_detection(self) -> bool:
        value = self.options.get("use_layout_detection", not self._is_enabled(self.options.get("whole_page_smoke", False)))
        return self._is_enabled(value)

    def _positive_int(self, name: str) -> int:
        try:
            value = int(self.options[name])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"PaddleOCR 本地服务参数 {name} 必须为整数") from exc
        if value <= 0:
            raise ValueError(f"PaddleOCR 本地服务参数 {name} 必须大于 0")
        return value

    def _result_pages(self, result: dict) -> list[dict]:
        if result.get("errorCode") not in (None, 0):
            raise ValueError(f"PaddleOCR 本地服务错误: {result.get('errorMsg', '未知错误')}")
        body = result.get("result")
        pages = body.get("layoutParsingResults") if isinstance(body, dict) else None
        if not isinstance(pages, list) or not pages:
            raise ValueError("PaddleOCR 本地服务响应缺少 result.layoutParsingResults")
        if not all(isinstance(page, dict) for page in pages):
            raise ValueError("PaddleOCR 本地服务页级响应格式错误")
        return pages

    def _is_enabled(self, value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"false", "0", "no", "off", ""}
