"""OpenAPI 合同测试：验证导出快照与运行时 schema 一致，且满足 T04 验收断言。

验收覆盖（对应任务卡第 11 节）：
1. 连续两次生成 OpenAPI 字节一致，生成后工作树无漂移（由 --check 覆盖，本文件复验）。
2. 前端已调用路由 operationId 无重复/缺失；无未参数化分页响应。
3. Candidate/CuratedItem 的 content 在 schema 中为 object（运行时验证见 test_core_response_shapes.py）。
6. 用旧请求字段/错误 JSON 类型调用端点返回 422（运行时验证见 test_core_response_shapes.py）。
7. ErrorResponse/ValidationErrorResponse 已注入 OpenAPI，code 为有限联合。
9. OpenAPI 中不存在裸 PaginatedResponse 引用。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_JSON = REPO_ROOT / "apps" / "api" / "openapi.json"
# 前端已调用的路由（来自 api.* 调用基线表）——这些路由的 operationId 必须稳定且唯一。
FRONTEND_ROUTES = [
    ("GET", "/api/projects/"),
    ("POST", "/api/projects/"),
    ("GET", "/api/candidates"),
    ("GET", "/api/candidates/{cid}"),
    ("POST", "/api/candidates/{cid}/review"),
    ("POST", "/api/candidates/{cid}/promote-to-curated"),
    ("GET", "/api/projects/{pid}/curated-items/"),
    ("GET", "/api/projects/{pid}/curated-items/{iid}"),
    ("PATCH", "/api/projects/{pid}/curated-items/{iid}"),
    ("GET", "/api/projects/{pid}/datasets/"),
    ("GET", "/api/projects/{pid}/datasets/{did}"),
    ("GET", "/api/projects/{pid}/datasets/{did}/items"),
    ("POST", "/api/projects/{pid}/datasets/{did}/export"),
    ("GET", "/api/projects/{pid}/benchmarks/"),
    ("GET", "/api/projects/{pid}/benchmarks/{bid}"),
    ("GET", "/api/projects/{pid}/benchmarks/{bid}/cases"),
    ("POST", "/api/projects/{pid}/benchmarks/{bid}/export"),
    ("GET", "/api/projects/{pid}/documents/"),
    ("GET", "/api/projects/{pid}/documents/{did}"),
    ("GET", "/api/projects/{pid}/documents/{did}/parse-jobs"),
    ("GET", "/api/projects/{pid}/documents/{did}/cleaning-jobs"),
    ("POST", "/api/projects/{pid}/documents/{did}/cleaning/start"),
    ("GET", "/api/projects/{pid}/parser-profiles/"),
    ("GET", "/api/projects/{pid}/chunk-profiles/"),
    ("GET", "/api/projects/{pid}/export-profiles/"),
    ("GET", "/api/projects/{pid}/model-configs/"),
    ("GET", "/api/projects/{pid}/prompt-templates/"),
    ("GET", "/api/projects/{pid}/tasks/"),
    ("GET", "/api/chunks/{cid}"),
    ("POST", "/api/chunks/{cid}/generate"),
    ("POST", "/api/sections/{sid}/lease/acquire"),
    ("POST", "/api/sections/{sid}/lease/heartbeat"),
    ("POST", "/api/sections/{sid}/lease/release"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/login"),
]


def _load_openapi() -> dict:
    if not OPENAPI_JSON.exists():
        pytest.fail(f"缺少 OpenAPI 快照 {OPENAPI_JSON}；请先运行 python scripts/export_openapi.py")
    return json.loads(OPENAPI_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def openapi_spec() -> dict:
    return _load_openapi()


def _all_operations(spec: dict):
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if method.lower() in ("get", "post", "put", "patch", "delete"):
                yield path, method.lower(), op


class TestOpenApiSnapshotDeterministic:
    def test_generation_is_deterministic(self):
        """连续两次生成 OpenAPI 字节一致（通过 export_openapi.py --check 复验）。"""
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "export_openapi.py"), "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"OpenAPI 漂移：{result.stderr}"


class TestOperationIds:
    def test_frontend_routes_have_unique_stable_operation_ids(self, openapi_spec):
        seen: dict[str, str] = {}
        for path, method, op in _all_operations(openapi_spec):
            op_id = op.get("operationId")
            assert op_id, f"{method.upper()} {path} 缺少 operationId"
            assert (method, path) not in seen or seen[(method, path)] == op_id
            seen[(method, path)] = op_id

        # 前端已调用路由必须存在且 operationId 稳定（格式 <resource>_<action>）。
        ids: dict[str, str] = {}
        for p, ms in openapi_spec["paths"].items():
            for m, op in ms.items():
                if m.lower() in ("get", "post", "put", "patch", "delete"):
                    ids[p] = op.get("operationId")
        for method, path in FRONTEND_ROUTES:
            assert path in openapi_spec["paths"], f"前端调用的路由 {path} 不在 OpenAPI 中"
            op_id = ids.get(path)
            assert op_id, f"{method} {path} 缺少 operationId"
            assert "_" in op_id, f"{method} {path} 的 operationId {op_id!r} 不符合 <resource>_<action> 格式"

    def test_operation_ids_are_unique(self, openapi_spec):
        seen = set()
        for path, method, op in _all_operations(openapi_spec):
            op_id = op["operationId"]
            assert op_id not in seen, f"重复 operationId: {op_id}（{method} {path}）"
            seen.add(op_id)


class TestPaginationContract:
    def test_no_unparameterized_paginated_response(self, openapi_spec):
        """不存在未参数化 PaginatedResponse 引用。"""
        raw = json.dumps(openapi_spec)
        assert raw.count('#/components/schemas/PaginatedResponse"') == 0
        # 所有 PaginatedResponse_* 组件必须带参数化的 items。
        for name, schema in openapi_spec["components"]["schemas"].items():
            if name.startswith("PaginatedResponse"):
                assert "items" in schema["properties"], f"{name} 未参数化"

    def test_paginated_routes_declare_parameterized_response(self, openapi_spec):
        """分页集合路由必须声明 PaginatedResponse[X]。"""
        required = [
            "/api/candidates",
            "/api/projects/{pid}/curated-items/",
            "/api/projects/{pid}/datasets/",
            "/api/projects/{pid}/datasets/{did}/items",
            "/api/projects/{pid}/benchmarks/",
            "/api/projects/{pid}/benchmarks/{bid}/cases",
            "/api/projects/{pid}/documents/",
            "/api/projects/{pid}/tasks/",
            "/api/projects/{pid}/exports/",
            "/api/projects/{pid}/model-configs/",
            "/api/projects/{pid}/parser-profiles/",
            "/api/projects/{pid}/chunk-profiles/",
            "/api/projects/{pid}/export-profiles/",
            "/api/projects/{pid}/task-policies/",
            "/api/projects/{pid}/prompt-templates/",
        ]
        for path in required:
            op = openapi_spec["paths"][path]["get"]
            schema = op["responses"]["200"]["content"]["application/json"]["schema"]
            ref = schema.get("$ref", "")
            assert ref.startswith("#/components/schemas/PaginatedResponse_"), f"{path} 未使用参数化分页模型: {ref}"

    def test_paginated_component_has_four_keys(self, openapi_spec):
        for name, schema in openapi_spec["components"]["schemas"].items():
            if name.startswith("PaginatedResponse"):
                props = schema["properties"]
                assert set(props.keys()) == {"items", "total", "page", "page_size"}, f"{name} 缺少四键结构"


class TestErrorEnvelope:
    def test_error_schemas_in_components(self, openapi_spec):
        comps = openapi_spec["components"]["schemas"]
        assert "ErrorResponse" in comps
        assert "ValidationErrorResponse" in comps
        assert "ValidationErrorItem" in comps

    def test_error_code_is_limited_union(self, openapi_spec):
        code = openapi_spec["components"]["schemas"]["ErrorResponse"]["properties"]["code"]
        assert "enum" in code, "ErrorResponse.code 必须为有限联合"
        assert "AUTH_REQUIRED" in code["enum"]
        assert "NOT_FOUND" in code["enum"]
        assert "VALIDATION_ERROR" in code["enum"]

    def test_protected_routes_document_401_403(self, openapi_spec):
        for path, method, op in _all_operations(openapi_spec):
            if op.get("security") and method == "get":
                assert "401" in op["responses"], f"{method} {path} 缺少 401 错误响应"
                assert "403" in op["responses"], f"{method} {path} 缺少 403 错误响应"

    def test_resource_routes_document_404(self, openapi_spec):
        for path, method, op in _all_operations(openapi_spec):
            if any(p in path for p in ("{cid}", "{did}", "{bid}", "{iid}", "{sid}")) and method in ("get", "patch", "delete"):
                assert "404" in op["responses"], f"{method} {path} 缺少 404 错误响应"

    def test_all_operations_document_422(self, openapi_spec):
        for path, method, op in _all_operations(openapi_spec):
            assert "422" in op["responses"], f"{method} {path} 缺少 422 校验错误响应"


class TestJsonFields:
    def test_candidate_content_is_object(self, openapi_spec):
        content = openapi_spec["components"]["schemas"]["CandidateResponse"]["properties"]["content"]
        assert content.get("type") == "object", f"Candidate.content 应为 object，实际 {content}"
        assert content.get("type") != "string"

    def test_curated_content_is_object(self, openapi_spec):
        for name in ("CuratedItemResponse", "CuratedRevisionResponse"):
            content = openapi_spec["components"]["schemas"][name]["properties"]["content"]
            assert content.get("type") == "object", f"{name}.content 应为 object，实际 {content}"
            assert content.get("type") != "string"

    def test_review_evidence_spans_is_object_or_null(self, openapi_spec):
        spans = openapi_spec["components"]["schemas"]["CandidateResponse"]["properties"]["review_evidence_spans"]
        any_of_types = {t.get("type") for t in spans.get("anyOf", [])}
        assert any_of_types <= {"object", "null"}, f"review_evidence_spans 应为 object|null，实际 {spans}"
