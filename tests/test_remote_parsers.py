import io
import json
import zipfile

from parsing.mineru_local_service_parser import MineruLocalServiceParser
from parsing.mineru_parser import MineruParser
from parsing.paddleocr_local_service_parser import PaddleOCRLocalServiceParser
from parsing.paddleocr_parser import PaddleOCRParser


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self) -> bytes:
        return self.payload


def _json_response(value: dict) -> FakeResponse:
    return FakeResponse(json.dumps(value).encode("utf-8"))


def test_mineru_uses_signed_upload_and_extracts_markdown(monkeypatch):
    requests = []
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as result_zip:
        result_zip.writestr("result/document.md", "# Parsed\n\ncontent")
        result_zip.writestr("result/content_list.json", json.dumps([{"page_idx": 0, "text": "content"}]))

    def fake_urlopen(request, timeout):
        requests.append(request)
        url = request.full_url
        if url.endswith("/api/v4/file-urls/batch"):
            return _json_response(
                {"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://upload.test/pdf"]}}
            )
        if url == "https://upload.test/pdf":
            assert request.get_method() == "PUT"
            assert request.data == b"%PDF test"
            assert request.headers["Content-type"] == ""
            return FakeResponse(b"")
        if url.endswith("/api/v4/extract-results/batch/batch-1"):
            return _json_response(
                {
                    "code": 0,
                    "data": {"extract_result": [{"state": "done", "full_zip_url": "https://download.test/result.zip"}]},
                }
            )
        if url == "https://download.test/result.zip":
            return FakeResponse(archive.getvalue())
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = MineruParser(
        {
            "base_url": "https://mineru.net/api/v4/extract/task",
            "api_key": "secret",
            "poll_interval_seconds": 0,
        }
    ).parse(b"%PDF test")

    assert result.raw_markdown == "# Parsed\n\ncontent\n"
    assert result.structured_json["page_count"] == 1
    assert "full_zip_url" not in result.structured_json["result"]
    assert requests[0].headers["Authorization"] == "Bearer secret"
    assert requests[0].get_method() == "POST"
    assert requests[2].get_method() == "GET"


def test_paddleocr_calls_layout_parsing_and_combines_pages(monkeypatch):
    observed = {}

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["headers"] = request.headers
        observed["payload"] = json.loads(request.data.decode("utf-8"))
        return _json_response(
            {
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
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = PaddleOCRParser(
        {
            "base_url": "https://service.aistudio-app.com/layout-parsing",
            "api_key": "secret",
        }
    ).parse(b"%PDF test")

    assert observed["url"].endswith("/layout-parsing")
    assert observed["headers"]["Authorization"] == "token secret"
    assert observed["payload"]["fileType"] == 0
    assert observed["payload"]["visualize"] is False
    assert result.raw_markdown == "# Page 1\n\nPage 2\n"
    assert result.structured_json["page_count"] == 2
    assert result.structured_json["pages"][0]["markdown"] == "# Page 1"


def test_paddleocr_local_service_calls_layout_parsing_without_token(monkeypatch):
    observed = {}

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["headers"] = request.headers
        observed["payload"] = json.loads(request.data.decode("utf-8"))
        return _json_response(
            {
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
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = PaddleOCRLocalServiceParser({"base_url": "http://127.0.0.1:9020"}).parse(b"%PDF test")

    assert observed["url"] == "http://127.0.0.1:9020/layout-parsing"
    assert "Authorization" not in observed["headers"]
    assert observed["payload"]["useLayoutDetection"] is True
    assert observed["payload"]["visualize"] is False
    assert result.raw_markdown == "Local PaddleOCR\n"
    assert result.structured_json["parser"] == "paddleocr_local_service"
    assert result.structured_json["provider"] == "paddleocr_private_paddlex_api"


def test_mineru_local_service_submits_task_and_extracts_result(monkeypatch):
    requests = []
    status_calls = 0

    def fake_urlopen(request, timeout):
        nonlocal status_calls
        requests.append(request)
        url = request.full_url
        if url == "http://127.0.0.1:9010/tasks" and request.get_method() == "POST":
            body = request.data.decode("utf-8", errors="ignore")
            assert 'name="backend"' in body
            assert "vlm-auto-engine" in body
            assert 'name="files"; filename="document.pdf"' in body
            assert "Authorization" not in request.headers
            return _json_response({"task_id": "local-task-1", "status": "pending"})
        if url == "http://127.0.0.1:9010/tasks/local-task-1":
            status_calls += 1
            return _json_response(
                {"task_id": "local-task-1", "status": "completed" if status_calls > 1 else "processing"}
            )
        if url == "http://127.0.0.1:9010/tasks/local-task-1/result":
            return _json_response(
                {
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
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = MineruLocalServiceParser({"base_url": "http://127.0.0.1:9010", "poll_interval_seconds": 0}).parse(
        b"%PDF test"
    )

    assert result.raw_markdown == "# Local Service\n\nParsed\n"
    assert result.structured_json["parser"] == "mineru_local_service"
    assert result.structured_json["page_count"] == 1
    assert result.page_mapping == [{"page_number": 1, "width": 612, "height": 792}]
    assert status_calls == 2
