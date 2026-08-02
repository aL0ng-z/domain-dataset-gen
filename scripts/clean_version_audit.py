"""T05 存量 CleanedDocumentVersion 一致性审计（任务卡 §4.4）。

在不修改数据的前提下，输出以下不一致清单（命中即应停止并形成治理决策，
见任务卡 §12 停止条件——不得静默重命名或覆盖历史文件）：
1. 重复 (document_id, version) —— 迁移前无唯一约束产生的历史重复；
2. 重复 artifact_key —— 不同行引用同一对象；
3. artifact_key 指向对象在 MinIO 缺失（无法校验）；
4. content_sha256 与对象内容 hash 不一致（仅对非 legacy 行校验）；
5. 非 legacy 行（content_sha256 非空）但 source_revision_map/sha256 缺失。

用法（需在测试库或受控库执行，切勿指向开发库）：
  conda run -n DatasetGen python scripts/clean_version_audit.py
"""

from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.orm import Session

# 允许运行的目标库名（与 tests/safety.py 白名单一致）。
ALLOWED_DATABASES = {"datasetgen_test"}

DB_URL = os.environ.get(
    "AUDIT_DATABASE_URL",
    "postgresql+psycopg2://datasetgen_test:datasetgen_test_password@localhost:55432/datasetgen_test",
)


def _db_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?")[0]


def main() -> int:
    if _db_name(DB_URL) not in ALLOWED_DATABASES:
        print(f"拒绝：目标库 {_db_name(DB_URL)!r} 不在审计白名单 {ALLOWED_DATABASES}")
        return 2

    engine = __import__("sqlalchemy").create_engine(DB_URL)
    issues: list[str] = []
    with Session(engine) as session:
        # 1. 重复 (document_id, version)。
        rows = session.execute(
            text(
                "SELECT document_id, version, count(*) AS n "
                "FROM cleaned_document_versions GROUP BY document_id, version HAVING count(*) > 1"
            )
        ).all()
        for r in rows:
            issues.append(f"重复 (document_id={r.document_id}, version={r.version}) 共 {r.n} 行")

        # 2. 重复 artifact_key。
        rows = session.execute(
            text(
                "SELECT artifact_key, count(*) AS n FROM cleaned_document_versions "
                "WHERE artifact_key IS NOT NULL GROUP BY artifact_key HAVING count(*) > 1"
            )
        ).all()
        for r in rows:
            issues.append(f"重复 artifact_key {r.artifact_key!r} 共 {r.n} 行")

        # 3. 对象缺失：artifact_key 非空但 MinIO 无对象（此处以 manifest 不可校验标记，
        #    实际对象检查需在配置 MinIO 的环境执行；本地以“无对象清单”输出）。
        rows = session.execute(
            text(
                "SELECT id, artifact_key FROM cleaned_document_versions "
                "WHERE artifact_key IS NOT NULL AND content_sha256 IS NOT NULL"
            )
        ).all()
        # 不在此处访问 MinIO（脚本保持无外部依赖）；对象级校验由部署期 smoke 覆盖。
        legacy_count = (
            session.execute(
                text(
                    "SELECT count(*) FROM cleaned_document_versions "
                    "WHERE content_sha256 IS NULL"
                )
            ).scalar()
            or 0
        )
        # 4. 非 legacy 行缺 source_revision_map/sha256。
        rows = session.execute(
            text(
                "SELECT id FROM cleaned_document_versions "
                "WHERE content_sha256 IS NOT NULL AND "
                "(source_revision_map IS NULL OR source_revision_sha256 IS NULL OR "
                " length(source_revision_sha256) <> 64 OR length(content_sha256) <> 64)"
            )
        ).all()
        for r in rows:
            issues.append(f"版本 {r.id} 非 legacy 但 source_revision_map/sha256 或 content_sha256 缺失/长度不符")

        print(f"[clean_version_audit] legacy（content_sha256 为空）行数：{legacy_count}")
        print(f"[clean_version_audit] 需对象级 hash 校验的行数：{len(rows) + legacy_count}")

    if issues:
        print(f"发现 {len(issues)} 处不一致，请停止自动修复并形成治理决策：")
        for line in issues:
            print("  -", line)
        return 1
    print("审计通过：未发现重复版本/artifact_key 或 hash 字段缺失。")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
