from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from parsing.base import BaseParser, ParseResult

DEFAULT_BASE_URL = "http://127.0.0.1:9010"


class MineruLocalServiceParser(BaseParser):
    """MinerU parser backed by a privately deployed official mineru-api service."""

    def parse(self, pdf_data: bytes) -> ParseResult:
        base_url = self._base_url()
        timeout = self._positive_float("timeout_seconds", 30.0)
        poll_interval = self._nonnegative_float("poll_interval_seconds", 1.0)
        max_wait = self._positive_float("max_wait_seconds", 1800.0)
        backend = str(self.options.get("backend", "vlm-auto-engine")).strip() or "vlm-auto-engine"

        payload, content_type = self._multipart_request(pdf_data, backend)
        task = self._request_json(
            f"{base_url}/tasks",
            data=payload,
            headers={"Content-Type": content_type, "Accept": "application/json"},
            timeout=timeout,
        )
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("MinerU 本地服务未返回 task_id")

        status_result = self._poll_task(base_url, task_id, timeout, poll_interval, max_wait)
        result = self._request_json(
            f"{base_url}/tasks/{task_id}/result",
            data=None,
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        return self._parse_result(result, status_result, task_id, backend)

    def _base_url(self) -> str:
        base_url = str(self.options.get("base_url", DEFAULT_BASE_URL)).strip().rstrip("/")
        if not base_url:
            raise ValueError("MinerU 本地服务解析器需要配置服务地址 (base_url)")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("MinerU 本地服务地址必须以 http:// 或 https:// 开头")
        return base_url

    def _multipart_request(self, pdf_data: bytes, backend: str) -> tuple[bytes, str]:
        boundary = f"----mineru-local-service-{uuid.uuid4().hex}"
        fields = {
            "backend": backend,
            "lang_list": str(self.options.get("language", "ch")),
            "parse_method": str(self.options.get("parse_method", "auto")),
            "formula_enable": self._bool_text("formula_enable", True),
            "table_enable": self._bool_text("table_enable", True),
            "image_analysis": self._bool_text("image_analysis", False),
            "return_md": "true",
            "return_middle_json": "true",
            "return_content_list": "true",
            "return_model_output": "false",
            "return_images": "false",
            "response_format_zip": "false",
        }
        server_url = str(self.options.get("server_url", "")).strip()
        if server_url:
            fields["server_url"] = server_url

        parts: list[bytes] = []
        for name, value in fields.items():
            parts.extend(
                [
                    f"--{boundary}\r\n".encode("ascii"),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )
        parts.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                b'Content-Disposition: form-data; name="files"; filename="document.pdf"\r\n',
                b"Content-Type: application/pdf\r\n\r\n",
                pdf_data,
                b"\r\n",
                f"--{boundary}--\r\n".encode("ascii"),
            ]
        )
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"

    def _poll_task(
        self,
        base_url: str,
        task_id: str,
        timeout: float,
        poll_interval: float,
        max_wait: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max_wait
        while True:
            status_result = self._request_json(
                f"{base_url}/tasks/{task_id}",
                data=None,
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
            task_status = str(status_result.get("status", "")).lower()
            if task_status == "completed":
                return status_result
            if task_status == "failed":
                raise ValueError(f"MinerU 本地服务解析失败: {status_result.get('error', '未知错误')}")
            if time.monotonic() >= deadline:
                raise TimeoutError("等待 MinerU 本地服务解析结果超时")
            time.sleep(poll_interval)

    def _parse_result(
        self,
        result: dict[str, Any],
        status_result: dict[str, Any],
        task_id: str,
        configured_backend: str,
    ) -> ParseResult:
        results = result.get("results")
        if not isinstance(results, dict) or not results:
            raise ValueError("MinerU 本地服务响应缺少 results")
        file_result = next(iter(results.values()))
        if not isinstance(file_result, dict):
            raise ValueError("MinerU 本地服务文件结果格式错误")

        markdown = file_result.get("md_content")
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError("MinerU 本地服务响应缺少 Markdown 内容")
        middle_json = self._decode_json_field(file_result.get("middle_json"))
        content_list = self._decode_json_field(file_result.get("content_list"))
        page_mapping = self._page_mapping(middle_json)
        pages = self._pages_from_content_list(content_list)
        page_count = len(page_mapping)
        if page_count == 0 and isinstance(content_list, list):
            page_indices = [
                item["page_idx"]
                for item in content_list
                if isinstance(item, dict) and isinstance(item.get("page_idx"), int)
            ]
            if page_indices:
                page_count = max(page_indices) + 1
                page_mapping = [{"page_number": page_number} for page_number in range(1, page_count + 1)]

        return ParseResult(
            raw_markdown=markdown.rstrip() + "\n",
            structured_json={
                "parser": "mineru_local_service",
                "provider": "mineru_private_api",
                "backend": result.get("backend", status_result.get("backend", configured_backend)),
                "service_version": result.get("version", status_result.get("version")),
                "service_task_id": task_id,
                "page_count": page_count,
                "middle_json": middle_json,
                "content_list": content_list,
                "pages": pages,
            },
            page_mapping=page_mapping,
        )

    def _page_mapping(self, middle_json: object) -> list[dict[str, Any]]:
        pages = middle_json.get("pdf_info") if isinstance(middle_json, dict) else None
        if not isinstance(pages, list):
            return []
        mapping: list[dict[str, Any]] = []
        for index, page in enumerate(pages):
            if not isinstance(page, dict):
                continue
            page_number = page.get("page_idx", index)
            item: dict[str, Any] = {"page_number": int(page_number) + 1}
            size = page.get("page_size")
            if isinstance(size, list) and len(size) >= 2:
                item["width"] = size[0]
                item["height"] = size[1]
            mapping.append(item)
        return mapping

    def _pages_from_content_list(self, content_list: object) -> list[dict[str, Any]]:
        if not isinstance(content_list, list):
            return []

        grouped: dict[int, list[str]] = {}
        for item in content_list:
            if not isinstance(item, dict):
                continue
            page_idx = item.get("page_idx", item.get("page_index"))
            if isinstance(page_idx, int):
                page_number = page_idx + 1
            elif isinstance(item.get("page_number"), int):
                page_number = item["page_number"]
            else:
                continue
            text = self._content_item_markdown(item)
            if text:
                grouped.setdefault(page_number, []).append(text)

        return [
            {
                "page_number": page_number,
                "markdown": "\n\n".join(parts).strip(),
            }
            for page_number, parts in sorted(grouped.items())
        ]

    def _content_item_markdown(self, item: dict[str, Any]) -> str:
        for key in ("text", "content", "md", "markdown", "table_body", "latex"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _request_json(self, url: str, data: bytes | None, headers: dict[str, str], timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            raise ValueError(f"MinerU 本地服务 HTTP 错误 ({exc.code}): {body}") from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"无法连接 MinerU 本地服务: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("MinerU 本地服务返回了无法解析的 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("MinerU 本地服务返回格式错误")
        return payload

    def _decode_json_field(self, value: object) -> object:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    def _bool_text(self, name: str, default: bool) -> str:
        value = self.options.get(name, default)
        if isinstance(value, bool):
            return str(value).lower()
        return str(value).strip().lower() if value is not None else str(default).lower()

    def _positive_float(self, name: str, default: float) -> float:
        value = self._nonnegative_float(name, default)
        if value == 0:
            raise ValueError(f"MinerU 本地服务参数 {name} 必须大于 0")
        return value

    def _nonnegative_float(self, name: str, default: float) -> float:
        raw = self.options.get(name, default)
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"MinerU 本地服务参数 {name} 必须为数字") from exc
        if value < 0:
            raise ValueError(f"MinerU 本地服务参数 {name} 不能小于 0")
        return value
