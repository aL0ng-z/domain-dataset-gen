"""确定性 OpenAPI 导出脚本。

用法（仓库根目录，DatasetGen 环境）：
  python scripts/export_openapi.py              # 覆盖写入 apps/api/openapi.json
  python scripts/export_openapi.py --check      # 仅比较，不一致时退出码 1（CI 漂移门禁）

设计目标：
- 不连接数据库/Redis/MinIO，不请求外部服务：直接从应用对象构建 OpenAPI。
- 输出规范化排序后的 JSON，保证重复生成字节一致。
- 是前端 `api:generate` 命令与 CI 漂移检查的共同来源。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# 保证在未安装 editable 包时也能导入本地库（与 conftest 保持一致的路径集合）。
for _p in ("apps/api", "libs/domain", "libs/storage", "libs/parsing", "libs/cleaning", "libs/splitters", "libs/llm"):
    _path = str(REPO_ROOT / _p)
    if _path not in sys.path:
        sys.path.insert(0, _path)

OUTPUT = REPO_ROOT / "apps" / "api" / "openapi.json"


def build_openapi() -> dict:
    from app.main import app

    return app.openapi()


def serialize(spec: dict) -> str:
    return json.dumps(spec, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def main() -> int:
    spec = build_openapi()
    rendered = serialize(spec)
    check_only = "--check" in sys.argv

    if check_only:
        if not OUTPUT.exists():
            print(f"[export_openapi] 缺少快照 {OUTPUT}；请先运行 python scripts/export_openapi.py", file=sys.stderr)
            return 1
        current = OUTPUT.read_text(encoding="utf-8")
        if current != rendered:
            print("[export_openapi] OpenAPI 漂移：重新生成后 git diff 非空，请同步提交。", file=sys.stderr)
            return 1
        print("[export_openapi] OpenAPI 快照一致。")
        return 0

    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"[export_openapi] 已写入 {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
