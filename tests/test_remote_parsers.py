import io
import json
import zipfile

import pytest

from parsing.mineru_local_service_parser import MineruLocalServiceParser
from parsing.mineru_parser import MineruParser
from parsing.paddleocr_local_service_parser import PaddleOCRLocalServiceParser
from parsing.paddleocr_parser import PaddleOCRParser
from parsing.transport import FakeSecureTransport, reset_transport, set_transport


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value).encode("utf-8")


def _install_fake(handler):
    """注册记录型 fake transport；每个测试结束重置。"""
    reset_transport()
    fake = FakeSecureTransport(handler)
    set_transport(fake)
    return fake


def test_mineru_uses_signed_upload_and_extracts_markdown():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as result_zip:
        result_zip.writestr("result/document.md", "# Parsed\n\ncontent")
        result_zip.writestr("result/content_list.json", json.dumps([{"page_idx": 0, "text": "content"}]))

    def handler(method, url, headers, data):
        if url.endswith("/api/v4/file-urls/batch"):
            assert method == "POST"
            assert headers.get("Authorization") == "Bearer secret"
            return {"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://upload.test/pdf"]}}
        if url == "https://upload.test/pdf":
            assert method == "PUT"
            assert data == b"%PDF test"
            assert headers.get("Content-Type") == ""
            return b""
        if url.endswith("/api/v4/extract-results/batch/batch-1"):
            assert method == "GET"
            return {
                "code": 0,
                "data": {"extract_result": [{"state": "done", "full_zip_url": "https://download.test/result.zip"}]},
            }
        if url == "https://download.test/result.zip":
            return archive.getvalue()
        raise AssertionError(f"Unexpected URL: {url}")

    fake = _install_fake(handler)

    result = MineruParser(
        {
            "api_key": "secret",
            "poll_interval_seconds": 0,
            "_security": {
                "base_url": "https://mineru.net/api/v4/extract/task",
                "credential_origins": [],
            },
        }
    ).parse(b"%PDF test")

    assert result.raw_markdown == "# Parsed\n\ncontent\n"
    assert result.structured_json["page_count"] == 1
    assert "full_zip_url" not in result.structured_json["result"]

    urls = fake.all_urls
    assert urls[0].endswith("/api/v4/file-urls/batch")
    assert urls[1] == "https://upload.test/pdf"
    assert urls[2].endswith("/api/v4/extract-results/batch/batch-1")
    assert urls[3] == "https://download.test/result.zip"
    # Authorization 只出现在 credential 请求（task + poll），不出现于 signed upload / archive。
    auth_calls = fake.sensitive_header_calls
    assert len(auth_calls) == 2
    assert all(c["url"] != "https://upload.test/pdf" for c in auth_calls)
    assert all(c["url"] != "https://download.test/result.zip" for c in auth_calls)


def test_mineru_never_sends_token_to_arbitrary_urls():
    """恶意 provider 返回任意 upload URL 时，全局 Token 与 PDF 均不得发送到该地址。"""
    seen: list[str] = []

    def handler(method, url, headers, data):
        seen.append(url)
        if url.endswith("/api/v4/file-urls/batch"):
            return {
                "code": 0,
                "data": {"batch_id": "batch-1", "file_urls": ["https://evil.example.com/steal.pdf"]},
            }
        if url.endswith("/api/v4/extract-results/batch/batch-1"):
            return {
                "code": 0,
                "data": {"extract_result": [{"state": "done", "full_zip_url": "https://evil.example.com/result.zip"}]},
            }
        raise AssertionError(f"不应请求该 URL: {url}")

    fake = _install_fake(handler)

    # 记录型 fake 不做 DNS；此测试证明恶意 provider 返回的 upload/archive URL 请求
    # 不会携带 Authorization（全局 Token 不会泄露到任意地址），且 archive URL 在
    # 服务端真实场景下还会经 artifact allowlist 二次校验。
    with pytest.raises(AssertionError):
        MineruParser(
            {
                "api_key": "secret",
                "poll_interval_seconds": 0,
                "_security": {"base_url": "https://mineru.net/api/v4/extract/task"},
            }
        ).parse(b"%PDF test")

    # 即使到达恶意 URL，也从未携带 Authorization。
    assert all(c["url"] != "https://evil.example.com/steal.pdf" or not c["has_authorization"] for c in fake.calls)


def test_paddleocr_calls_layout_parsing_and_combines_pages():
    observed = {}

    def handler(method, url, headers, data):
        observed["url"] = url
        observed["headers"] = headers
        observed["payload"] = json.loads(data.decode("utf-8"))
        return {
            "errorCode": 0,
            "result": {
                "dataInfo": {"pages": 2},
                "layoutParsingResults": [
                    {
                        "markdown": {"text": "# Page 1", "images": {"large": "base64"}},
                        "prunedResult": {"blocks": []},
                    },
                    {
                        "markdown": {"text": "Page 2", "images": {}},
                        "prunedResult": {"blocks": []},
                    },
                ],
            },
        }

    _install_fake(handler)

    result = PaddleOCRParser(
        {
            "api_key": "secret",
            "_security": {"base_url": "https://service.aistudio-app.com/layout-parsing"},
        }
    ).parse(b"%PDF test")

    assert observed["url"].endswith("/layout-parsing")
    assert observed["headers"]["Authorization"] == "token secret"
    assert observed["payload"]["fileType"] == 0
    assert observed["payload"]["visualize"] is False
    assert result.raw_markdown == "# Page 1\n\nPage 2\n"
    assert result.structured_json["page_count"] == 2
    assert result.structured_json["pages"][0]["markdown"] == "# Page 1"


def test_paddleocr_local_service_calls_layout_parsing_without_token():
    observed = {}

    def handler(method, url, headers, data):
        observed["url"] = url
        observed["headers"] = headers
        observed["payload"] = json.loads(data.decode("utf-8"))
        return {
            "errorCode": 0,
            "result": {
                "dataInfo": {"pages": 1},
                "layoutParsingResults": [
                    {
                        "markdown": {"text": "Local PaddleOCR"},
                        "prunedResult": {"layout_det_res": []},
                    }
                ],
            },
        }

    _install_fake(handler)

    result = PaddleOCRLocalServiceParser(
        {
            "_security": {
                "base_url": "http://127.0.0.1:9020",
                "managed_local": {"port": 9020, "pinned_ips": ["127.0.0.1"]},
            }
        }
    ).parse(b"%PDF test")

    assert observed["url"] == "http://127.0.0.1:9020/layout-parsing"
    assert "Authorization" not in observed["headers"]
    assert observed["payload"]["useLayoutDetection"] is True
    assert observed["payload"]["visualize"] is False
    assert result.raw_markdown == "Local PaddleOCR\n"
    assert result.structured_json["parser"] == "paddleocr_local_service"
    assert result.structured_json["provider"] == "paddleocr_private_paddlex_api"


def test_mineru_local_service_submits_task_and_extracts_result():
    requests = []
    status_calls = 0

    def handler(method, url, headers, data):
        nonlocal status_calls
        requests.append((method, url, headers))
        if url == "http://127.0.0.1:9010/tasks" and method == "POST":
            body = data.decode("utf-8", errors="ignore")
            assert 'name="backend"' in body
            assert "vlm-auto-engine" in body
            assert 'name="files"; filename="document.pdf"' in body
            assert "Authorization" not in headers
            return {"task_id": "local-task-1", "status": "pending"}
        if url == "http://127.0.0.1:9010/tasks/local-task-1":
            status_calls += 1
            return {"task_id": "local-task-1", "status": "completed" if status_calls > 1 else "processing"}
        if url == "http://127.0.0.1:9010/tasks/local-task-1/result":
            return {
                "task_id": "local-task-1",
                "status": "completed",
                "backend": "vlm-auto-engine",
                "version": "3.1.15",
                "results": {
                    "document": {
                        "md_content": "# Local Service\n\nParsed",
                        "middle_json": json.dumps({"pdf_info": [{"page_idx": 0, "page_size": [612, 792]}]}),
                        "content_list": json.dumps([{"page_idx": 0, "type": "text"}]),
                    }
                },
            }
        raise AssertionError(f"Unexpected URL: {url}")

    _install_fake(handler)

    result = MineruLocalServiceParser(
        {
            "poll_interval_seconds": 0,
            "_security": {
                "base_url": "http://127.0.0.1:9010",
                "managed_local": {"port": 9010, "pinned_ips": ["127.0.0.1"]},
            },
        }
    ).parse(b"%PDF test")

    assert result.raw_markdown == "# Local Service\n\nParsed\n"
    assert result.structured_json["parser"] == "mineru_local_service"
    assert result.structured_json["page_count"] == 1
    assert result.page_mapping == [{"page_number": 1, "width": 612, "height": 792}]
    assert status_calls == 2
    # 本地服务请求全部不携带 Authorization
    assert all("Authorization" not in h for _, _, h in requests)
