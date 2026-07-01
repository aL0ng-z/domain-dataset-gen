from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from typing import Any

from parsing.base import BaseParser, ParseResult

DEFAULT_TASK_URL = "https://mineru.net/api/v4/extract/task"


class MineruParser(BaseParser):
    """MinerU precision extract API parser for PDF bytes.

    MinerU's token-protected API cannot accept local bytes at the single-task
    endpoint. For an uploaded PDF, use its signed batch-upload flow, poll the
    asynchronous result, then extract Markdown from the returned ZIP archive.
    """

    def parse(self, pdf_data: bytes) -> ParseResult:
        api_key = str(self.options.get("api_key", "")).strip()
        if not api_key:
            raise ValueError("MinerU API 解析需要在服务端配置 MINERU_API_TOKEN")

        timeout = self._as_float("timeout_seconds", 300.0)
        poll_interval = self._as_float("poll_interval_seconds", 2.0)
        max_wait = self._as_float("max_wait_seconds", 900.0)
        urls = self._resolve_urls()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        data_id = uuid.uuid4().hex
        model_version = str(self.options.get("model_version", "vlm"))
        request_payload: dict[str, Any] = {
            "files": [{"name": "document.pdf", "data_id": data_id}],
            "model_version": model_version,
        }
        for field in ("enable_formula", "enable_table", "language"):
            if field in self.options:
                request_payload[field] = self.options[field]

        upload_task = self._request_json(
            urls["upload"],
            data=json.dumps(request_payload).encode("utf-8"),
            headers=headers,
            timeout=timeout,
        )
        task_data = self._require_success(upload_task, "申请 MinerU 上传地址失败")
        batch_id = task_data.get("batch_id")
        file_urls = task_data.get("file_urls")
        if not batch_id or not isinstance(file_urls, list) or not file_urls:
            raise ValueError("MinerU API 未返回 batch_id 或文件上传地址")

        self._upload_pdf(str(file_urls[0]), pdf_data, timeout)
        extract_result = self._poll_result(
            f"{urls['results']}/{batch_id}",
            headers,
            timeout=timeout,
            poll_interval=poll_interval,
            max_wait=max_wait,
        )
        archive_url = extract_result.get("full_zip_url")
        if not archive_url:
            raise ValueError("MinerU 解析完成但未返回结果归档地址")

        archive_bytes = self._request_bytes(str(archive_url), timeout=timeout)
        markdown, archive_json, files = self._extract_archive(archive_bytes)
        pages = self._pages_from_content_list(archive_json)
        page_count = self._find_page_count(extract_result, archive_json)
        result_summary = {
            key: extract_result[key]
            for key in ("state", "data_id", "err_msg")
            if key in extract_result
        }

        return ParseResult(
            raw_markdown=markdown,
            structured_json={
                "parser": "mineru",
                "provider": "mineru_precision_api",
                "model_version": model_version,
                "page_count": page_count,
                "data_id": data_id,
                "archive_files": files,
                "result": result_summary,
                "content": archive_json,
                "pages": pages,
            },
            page_mapping=[{"page_number": page["page_number"]} for page in pages],
        )

    def _resolve_urls(self) -> dict[str, str]:
        task_url = str(self.options.get("base_url", DEFAULT_TASK_URL)).rstrip("/")
        if not task_url:
            task_url = DEFAULT_TASK_URL
        marker = "/api/v4"
        marker_index = task_url.find(marker)
        if marker_index == -1:
            raise ValueError("MinerU API 地址必须包含 /api/v4 路径")
        api_root = task_url[: marker_index + len(marker)]
        return {
            "upload": str(self.options.get("upload_url", f"{api_root}/file-urls/batch")).rstrip("/"),
            "results": str(self.options.get("results_url", f"{api_root}/extract-results/batch")).rstrip("/"),
        }

    def _as_float(self, name: str, default: float) -> float:
        raw = self.options.get(name, default)
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"MinerU 参数 {name} 必须为数字") from exc
        if value < 0:
            raise ValueError(f"MinerU 参数 {name} 不能小于 0")
        return value

    def _request_json(self, url: str, data: bytes | None, headers: dict[str, str], timeout: float) -> dict:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        raw = self._open(req, timeout)
        try:
            result = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("MinerU API 返回了无法解析的 JSON") from exc
        if not isinstance(result, dict):
            raise ValueError("MinerU API 返回格式错误")
        return result

    def _request_bytes(self, url: str, timeout: float) -> bytes:
        return self._open(urllib.request.Request(url, method="GET"), timeout, expose_error_body=False)

    def _upload_pdf(self, url: str, pdf_data: bytes, timeout: float) -> None:
        # Signed OSS upload URLs are generated without a Content-Type value.
        # urllib otherwise injects application/x-www-form-urlencoded for bytes.
        req = urllib.request.Request(url, data=pdf_data, headers={"Content-Type": ""}, method="PUT")
        self._open(req, timeout, expose_error_body=False)

    def _open(self, req: urllib.request.Request, timeout: float, expose_error_body: bool = True) -> bytes:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            if expose_error_body:
                body = exc.read().decode("utf-8", errors="replace")[:1000]
                detail = f": {body}"
            raise ValueError(f"MinerU API HTTP 错误 ({exc.code}){detail}") from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"无法连接 MinerU 服务: {exc.reason}") from exc

    def _require_success(self, response: dict, action: str) -> dict:
        if response.get("code") not in (None, 0):
            raise ValueError(f"{action}: {response.get('msg', '未知错误')}")
        data = response.get("data")
        if not isinstance(data, dict):
            raise ValueError(f"{action}: 响应缺少 data")
        return data

    def _poll_result(
        self,
        url: str,
        headers: dict[str, str],
        *,
        timeout: float,
        poll_interval: float,
        max_wait: float,
    ) -> dict:
        deadline = time.monotonic() + max_wait
        while True:
            response = self._request_json(url, data=None, headers=headers, timeout=timeout)
            data = self._require_success(response, "查询 MinerU 解析结果失败")
            result = data.get("extract_result", data)
            if isinstance(result, list):
                result = result[0] if result else {}
            if not isinstance(result, dict):
                raise ValueError("MinerU 解析结果格式错误")

            state = str(result.get("state", data.get("state", ""))).lower()
            if state == "done":
                return result
            if state == "failed":
                raise ValueError(f"MinerU 解析失败: {result.get('err_msg', result.get('msg', '未知错误'))}")
            if time.monotonic() >= deadline:
                raise TimeoutError("等待 MinerU 解析结果超时")
            time.sleep(poll_interval)

    def _extract_archive(self, archive_bytes: bytes) -> tuple[str, dict | list | None, list[str]]:
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                files = [name for name in archive.namelist() if not name.endswith("/")]
                markdown_files = sorted(name for name in files if name.lower().endswith(".md"))
                if not markdown_files:
                    raise ValueError("MinerU 结果归档中未找到 Markdown 文件")
                markdown = "\n\n".join(
                    archive.read(name).decode("utf-8", errors="replace").strip() for name in markdown_files
                ).strip()
                markdown = f"{markdown}\n" if markdown else ""

                json_files = sorted(name for name in files if name.lower().endswith(".json"))
                archive_json = None
                if json_files:
                    preferred = next((name for name in json_files if "content_list" in name), json_files[0])
                    try:
                        archive_json = json.loads(archive.read(preferred).decode("utf-8"))
                    except json.JSONDecodeError:
                        archive_json = None
        except zipfile.BadZipFile as exc:
            raise ValueError("MinerU 返回的结果归档不是有效 ZIP 文件") from exc
        return markdown, archive_json, files

    def _pages_from_content_list(self, content: dict | list | None) -> list[dict[str, Any]]:
        if not isinstance(content, list):
            return []

        grouped: dict[int, list[str]] = {}
        for item in content:
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

    def _find_page_count(self, result: dict, content: dict | list | None) -> int:
        for key in ("page_count", "page_num", "pages"):
            value = result.get(key)
            if isinstance(value, int):
                return value
        if isinstance(content, dict):
            value = content.get("page_count")
            if isinstance(value, int):
                return value
        if isinstance(content, list):
            zero_based_pages: list[int] = []
            one_based_pages: list[int] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("page_idx"), int):
                    zero_based_pages.append(item["page_idx"])
                elif isinstance(item.get("page_index"), int):
                    zero_based_pages.append(item["page_index"])
                elif isinstance(item.get("page_number"), int):
                    one_based_pages.append(item["page_number"])
            if zero_based_pages:
                return max(zero_based_pages) + 1
            if one_based_pages:
                return max(one_based_pages)
        return 0
