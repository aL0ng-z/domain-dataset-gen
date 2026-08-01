"""T02 静态合同检查：OpenAPI 认证关联 + 无裸 ID 写操作白名单外实例。

覆盖任务卡 §11 验收标准 10：
- OpenAPI 中每个项目资源操作（除公共 login/refresh/health）都关联统一
  Bearer 认证依赖；
- 静态检查不存在路由内裸 ID 写操作白名单外实例（即任意受保护写操作必须
  经 require_project_member / authorize_flat_resource / check_project_member
  授权，而不是只依赖 get_current_user 后用裸 ID 写库）。

纯单元检查，不依赖数据库/Redis/MinIO。
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_APP_DIR = REPO_ROOT / "apps" / "api" / "app"

# 公开端点（无需认证）白名单。
PUBLIC_OPERATIONS = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/refresh"),
    ("GET", "/api/health"),
}

# 受保护但无需项目上下文（仅全局角色）的路由模块：users、projects（不含成员管理）。
# 其余所有写操作都必须出现项目授权依赖。
PROTECTED_GLOBAL_ONLY_MODULES = {"users", "auth", "monitoring"}


def test_all_project_resource_operations_secured():
    """OpenAPI 中每个项目资源操作都关联统一 Bearer 认证依赖。"""
    import os
    import sys

    os.environ.setdefault("TESTING", "0")
    for p in ("apps/api", "libs/domain", "libs/storage", "libs/parsing", "libs/cleaning", "libs/splitters", "libs/llm"):
        sys.path.insert(0, str(REPO_ROOT / p))

    from app.main import app

    schema = app.openapi()
    assert "HTTPBearer" in schema.get("components", {}).get("securitySchemes", {})

    unsecured = []
    for path, methods in schema["paths"].items():
        for method, op_item in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            key = (method.upper(), path)
            if key in PUBLIC_OPERATIONS:
                continue
            if not op_item.get("security"):
                unsecured.append(f"{method.upper()} {path}")
    assert not unsecured, "以下操作未关联统一认证依赖:\n" + "\n".join(unsecured)


def test_no_bare_id_write_without_project_authz():
    """静态检查：受保护写操作不使用仅 get_current_user 的裸 ID 写库模式。

    规则：在 routers 下，凡出现 db 写入（db.add / db.delete / service 写方法）
    且函数同时带有 get_current_user 依赖（而非 require_project_member /
    authorize_flat_resource / check_project_member）的路由处理器，视为可疑。
    具体通过『调用方必须显式 import 项目授权 API』白名单校验。
    """
    import re

    # 项目授权 API 必须被 import 使用。
    authz_imports = (
        r"from app\.authz import",
        r"from app\.dependencies import .*require_project_member",
    )
    # 可疑模式：仅 get_current_user + 裸 ID 写（service.update/delete/add 等）。
    suspicious_pattern = re.compile(
        r"Depends\(get_current_user\)[\s\S]{0,1200}?"
        r"(\.delete\(|\.update\(|\.add\(|create_|cancel_|update_|delete_)"
    )

    violations = []
    routers_dir = API_APP_DIR / "routers"
    for path in sorted(routers_dir.glob("*.py")):
        content = path.read_text(encoding="utf-8")
        # 排除了已引入项目授权 API 的模块。
        if any(imp in content for imp in authz_imports):
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            if "Depends(get_current_user)" in line or "require_role" in line:
                # 该模块有仅登录的依赖，检查其后是否出现裸 ID 写。
                tail = "\n".join(content.splitlines()[lineno - 1: lineno + 60])
                if suspicious_pattern.search(tail):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno} 可能为裸 ID 写操作（无项目授权）"
                    )

    assert not violations, "发现路由内裸 ID 写操作（无项目授权）:\n" + "\n".join(violations)
