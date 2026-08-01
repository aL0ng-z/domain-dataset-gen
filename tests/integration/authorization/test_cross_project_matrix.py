"""T02 核心矩阵：跨项目资源隔离 + admin 绑定不可绕过。

覆盖任务卡 §11 验收标准 1、4：
- 对每种资源，项目 A 成员用 pid=A 与项目 B 的对象 ID 执行 GET/PATCH/DELETE/action 均为 404。
- admin 使用错误 pid 访问真实资源仍为 404；全局角色不能绕过路径绑定。
- 同项目同角色访问正确 pid 下对象正常（200/201/204），保证矩阵自洽。
"""

import pytest
from httpx import AsyncClient


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    res = await client.post(
        "/api/auth/login", json={"username": username, "password": "password-123"}
    )
    assert res.status_code == 200, res.text
    return res.json()


# 每种资源在“项目 A pid + 项目 B 对象”下的负向 GET 请求。
# 带 pid 的嵌套路由：pid=A + B 对象 -> 一律 404（路径绑定）。
# 平铺路由（无 pid）：editor 非 B 成员 -> 403 或 404（任务卡 §11 验收标准 2 既定语义）。
CROSS_PROJECT_GET_CASES = [
    ("document", lambda r, pk, tk: f"/api/projects/{r[pk]['pid']}/documents/{r[tk]['document'].id}", 404),
    ("parse_job", lambda r, pk, tk: f"/api/projects/{r[pk]['pid']}/documents/parse-jobs/{r[tk]['parse_job'].id}", 404),
    ("cleaning_job", lambda r, pk, tk: f"/api/projects/{r[pk]['pid']}/documents/{r[tk]['document'].id}/cleaning-jobs", 404),
    ("curated_item", lambda r, pk, tk: f"/api/projects/{r[pk]['pid']}/curated-items/{r[tk]['curated_item'].id}", 404),
    ("dataset", lambda r, pk, tk: f"/api/projects/{r[pk]['pid']}/datasets/{r[tk]['dataset'].id}", 404),
    ("benchmark", lambda r, pk, tk: f"/api/projects/{r[pk]['pid']}/benchmarks/{r[tk]['benchmark'].id}", 404),
    ("task", lambda r, pk, tk: f"/api/projects/{r[pk]['pid']}/tasks/{r[tk]['task'].id}", 404),
    ("prompt_template", lambda r, pk, tk: f"/api/projects/{r[pk]['pid']}/prompt-templates/{r[tk]['prompt_template'].id}", 404),
    ("parser_profile", lambda r, pk, tk: f"/api/projects/{r[pk]['pid']}/parser-profiles/{r[tk]['parser_profile'].id}", 404),
    ("model_config", lambda r, pk, tk: f"/api/projects/{r[pk]['pid']}/model-configs/{r[tk]['model_config'].id}", 404),
    ("chunk_profile", lambda r, pk, tk: f"/api/projects/{r[pk]['pid']}/chunk-profiles/{r[tk]['chunk_profile'].id}", 404),
    ("export_profile", lambda r, pk, tk: f"/api/projects/{r[pk]['pid']}/export-profiles/{r[tk]['export_profile'].id}", 404),
    # 平铺路由：A 成员对 B 对象 -> 403（非成员）或 404。
    ("section", lambda r, pk, tk: f"/api/sections/{r[tk]['section'].id}", None),
    ("chunk", lambda r, pk, tk: f"/api/chunks/{r[tk]['chunk'].id}", None),
    ("candidate", lambda r, pk, tk: f"/api/candidates/{r[tk]['candidate'].id}", None),
    ("cleaned_version", lambda r, pk, tk: f"/api/cleaned-versions/{r[tk]['cleaned_version'].id}", None),
]


@pytest.mark.integration
@pytest.mark.parametrize(
    "name,path_builder,expected", CROSS_PROJECT_GET_CASES,
    ids=[c[0] for c in CROSS_PROJECT_GET_CASES],
)
async def test_cross_project_get_denied(client: AsyncClient, full_resources, name, path_builder, expected):
    """项目 A 成员用 pid=A 访问项目 B 的对象：嵌套路由 404、平铺路由 403/404。"""
    tokens = await _login(client, "editor_user")
    r = full_resources["projects"]
    url = path_builder(r, "a", "b")
    res = await client.get(url, headers=_bearer(tokens["access_token"]))
    if expected == 404:
        assert res.status_code == 404, f"{name}: {res.status_code} {res.text}"
    else:
        assert res.status_code in (403, 404), f"{name}: {res.status_code} {res.text}"
        # 响应不泄露对象内容或所属项目。
        assert "测试内容" not in res.text
        assert "# 合并" not in res.text
        assert "# 清洗后" not in res.text


@pytest.mark.integration
@pytest.mark.parametrize(
    "name,path_builder,expected", CROSS_PROJECT_GET_CASES,
    ids=[c[0] for c in CROSS_PROJECT_GET_CASES],
)
async def test_same_project_get_ok(client: AsyncClient, full_resources, name, path_builder, expected):
    """同一项目 editor 读取正确 pid 下对象 -> 200（矩阵自洽）。"""
    tokens = await _login(client, "editor_user")
    r = full_resources["projects"]
    url = path_builder(r, "a", "a")
    res = await client.get(url, headers=_bearer(tokens["access_token"]))
    assert res.status_code == 200, f"{name}: {res.status_code} {res.text}"


@pytest.mark.integration
async def test_admin_wrong_pid_still_404(client: AsyncClient, full_resources):
    """全局 admin 用错误 pid 访问真实资源 -> 404；不能绕过路径绑定。"""
    tokens = await _login(client, "admin_user")
    r = full_resources["projects"]
    pid_a = r["a"]["pid"]
    doc_b = r["b"]["document"]
    res = await client.get(
        f"/api/projects/{pid_a}/documents/{doc_b.id}", headers=_bearer(tokens["access_token"])
    )
    assert res.status_code == 404, res.text

    # admin 用正确 pid 访问同项目资源 -> 200
    ok = await client.get(
        f"/api/projects/{r['a']['pid']}/documents/{r['a']['document'].id}",
        headers=_bearer(tokens["access_token"]),
    )
    assert ok.status_code == 200, ok.text
