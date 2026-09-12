"""实际解析器写入页码数组时，清洗工作台章节 API 必须正常序列化。"""

import pytest

from tests.integration.test_task_api import _bearer, _login


@pytest.mark.integration
async def test_section_list_accepts_page_number_array(client, full_resources):
    resource = full_resources["projects"]["a"]
    resource["section"].source_pages = [1, 2]
    await full_resources["db"].commit()
    token = await _login(client, "editor_user")
    response = await client.get(
        f"/api/projects/{resource['pid']}/documents/{resource['document'].id}/sections",
        params={"cleaning_job_id": str(resource["cleaning_job"].id), "page": 1, "page_size": 100},
        headers=_bearer(token["access_token"]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["source_pages"] == [1, 2]
