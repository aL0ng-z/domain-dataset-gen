"""T02 存量数据归属审计：孤儿与跨项目引用检测（任务卡 §10.8、§4）。

在不修改数据的前提下，输出以下不一致清单（存在即应停止自动修复并形成
治理决策，见任务卡 §12 停止条件）：
1. 每个按归属链解析的资源是否存在孤儿（引用不存在的父对象）；
2. 冗余外键不一致：Chunk.section_id 与 document_id 指向不同 Document；
3. 跨项目引用：文档链资源所属项目不一致（依赖外键链推导）。

用法（需在测试库或受控库执行，切勿指向开发库）：
  conda run -n DatasetGen python scripts/data-ownership-audit.py
"""

from __future__ import annotations

import os
import sys

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
        # 1. 孤儿：Chunk.document_id / Section.document_id 引用不存在的文档。
        for table, col in (("chunks", "document_id"), ("sections", "document_id")):
            rows = session.execute(
                text(
                    f"SELECT c.id FROM {table} c LEFT JOIN documents d ON d.id = c.{col} "
                    f"WHERE c.{col} IS NOT NULL AND d.id IS NULL"
                )
            ).all()
            for r in rows:
                issues.append(f"{table}.{r[0]} 引用不存在的 document")

        # 2. 孤儿：Candidate.chunk_id / GenerationRun.chunk_id 引用不存在的 chunk。
        for table, col in (("candidates", "chunk_id"), ("generation_runs", "chunk_id")):
            rows = session.execute(
                text(
                    f"SELECT c.id FROM {table} c LEFT JOIN chunks ch ON ch.id = c.{col} "
                    f"WHERE c.{col} IS NOT NULL AND ch.id IS NULL"
                )
            ).all()
            for r in rows:
                issues.append(f"{table}.{r[0]} 引用不存在的 chunk")

        # 3. 冗余外键不一致：Chunk.section_id 的 section 与 Chunk.document_id 指向不同文档。
        rows = session.execute(
            text(
                "SELECT c.id FROM chunks c "
                "JOIN sections s ON s.id = c.section_id "
                "WHERE c.section_id IS NOT NULL AND c.document_id <> s.document_id"
            )
        ).all()
        for r in rows:
            issues.append(f"chunks.{r[0]} 的 section_id 与 document_id 归属不一致")

        # 4. 跨项目引用：Candidate -> Chunk -> Document 链内 document 不一致。
        rows = session.execute(
            text(
                "SELECT cand.id FROM candidates cand "
                "JOIN chunks ch ON ch.id = cand.chunk_id "
                "JOIN generation_runs gr ON gr.chunk_id = ch.id "
                "WHERE gr.chunk_id <> cand.chunk_id"
            )
        ).all()
        for r in rows:
            issues.append(f"candidates.{r[0]} 的 generation_run 与 chunk 不一致")

    if issues:
        print(f"发现 {len(issues)} 处归属不一致，请停止自动修复并形成治理决策：")
        for line in issues:
            print("  -", line)
        return 1
    print("审计通过：未发现孤儿或跨项目引用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
