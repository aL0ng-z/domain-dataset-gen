"""T10 存量 composition 审计：加约束前的只读体检（任务卡 §10.1、§4、§12 停止条件）。

在给 dataset_items/benchmark_cases 加唯一约束、固定 revision/approval 列与
composition 字段之前，逐项检查存量 membership，任何命中即触发停止条件，绝不在
migration 中猜测保留哪条或静默删改：

1. 重复 membership：同一容器内 (dataset_id, curated_item_id) 出现多次；
2. 重复 ordinal：同一容器内 (dataset_id, ordinal) 出现多次；
3. 跨项目关联：membership 的 CuratedItem 与容器不属于同一 project；
4. 非 approved item：被编组的 CuratedItem.status != 'approved'（无法确定获批 revision）；
5. Benchmark 非 supported source：固定 source Candidate.review_verdict != 'supported'
   （或无法反查 source Candidate）；
6. 无 EvidenceLink：被编组的 approved item 没有至少一个有效证据链接。

用法（需在受控库执行，白名单与 tests/safety.py 一致）：
  conda run -n DatasetGen python scripts/composition_audit.py
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
        # 1. 重复 membership。
        for tbl, container_col in (("dataset_items", "dataset_id"), ("benchmark_cases", "benchmark_id")):
            rows = session.execute(
                text(
                    f"SELECT {container_col}, curated_item_id, count(*) AS n "
                    f"FROM {tbl} GROUP BY {container_col}, curated_item_id HAVING count(*) > 1"
                )
            ).all()
            for r in rows:
                issues.append(
                    f"{tbl}.({r[0]}, {r[1]}) 重复 membership {r[2]} 条"
                )

        # 2. 重复 ordinal。
        for tbl, container_col in (("dataset_items", "dataset_id"), ("benchmark_cases", "benchmark_id")):
            rows = session.execute(
                text(
                    f"SELECT {container_col}, ordinal, count(*) AS n "
                    f"FROM {tbl} GROUP BY {container_col}, ordinal HAVING count(*) > 1"
                )
            ).all()
            for r in rows:
                issues.append(
                    f"{tbl}.({r[0]}, ordinal={r[1]}) 重复 ordinal {r[2]} 条"
                )

        # 3. 跨项目关联（membership 的 CuratedItem 与容器项目不一致）。
        for tbl, container_col, container_tbl in (
            ("dataset_items", "dataset_id", "datasets"),
            ("benchmark_cases", "benchmark_id", "benchmarks"),
        ):
            rows = session.execute(
                text(
                    f"SELECT m.id, m.{container_col}, m.curated_item_id "
                    f"FROM {tbl} m "
                    f"JOIN {container_tbl} c ON c.id = m.{container_col} "
                    f"JOIN curated_items ci ON ci.id = m.curated_item_id "
                    f"WHERE c.project_id <> ci.project_id"
                )
            ).all()
            for r in rows:
                issues.append(
                    f"{tbl}.{r[0]} 跨项目：容器 {r[1]} 与 CuratedItem {r[2]} 项目不一致"
                )

        # 4. 非 approved item 被编组。
        for tbl, container_col in (("dataset_items", "dataset_id"), ("benchmark_cases", "benchmark_id")):
            rows = session.execute(
                text(
                    f"SELECT m.id, m.curated_item_id, ci.status FROM {tbl} m "
                    f"JOIN curated_items ci ON ci.id = m.curated_item_id "
                    f"WHERE ci.status != 'approved'"
                )
            ).all()
            for r in rows:
                issues.append(f"{tbl}.{r[0]} 编组了非 approved CuratedItem {r[1]}（status={r[2]}）")

        # 5. Benchmark 非 supported source（固定 source Candidate verdict）。
        rows = session.execute(
            text(
                "SELECT m.id, m.curated_item_id FROM benchmark_cases m "
                "JOIN curated_items ci ON ci.id = m.curated_item_id "
                "LEFT JOIN candidates cand ON cand.id = ci.candidate_id "
                "WHERE cand.review_verdict IS DISTINCT FROM 'supported'"
            )
        ).all()
        for r in rows:
            issues.append(
                f"benchmark_cases.{r[0]} 的 source Candidate（item {r[1]}）verdict 非 supported"
            )

        # 6. 无 EvidenceLink 的 approved item。
        rows = session.execute(
            text(
                "SELECT m.id, m.curated_item_id FROM dataset_items m "
                "JOIN curated_items ci ON ci.id = m.curated_item_id "
                "LEFT JOIN evidence_links el ON el.curated_item_id = ci.id "
                "WHERE ci.status = 'approved' AND el.id IS NULL "
                "UNION ALL "
                "SELECT m.id, m.curated_item_id FROM benchmark_cases m "
                "JOIN curated_items ci ON ci.id = m.curated_item_id "
                "LEFT JOIN evidence_links el ON el.curated_item_id = ci.id "
                "WHERE ci.status = 'approved' AND el.id IS NULL"
            )
        ).all()
        for r in rows:
            issues.append(f"membership {r[0]} 编组的 approved CuratedItem {r[1]} 无 EvidenceLink")

    if issues:
        print(f"发现 {len(issues)} 处历史 composition 问题，请停止自动修复并形成治理决策：")
        for line in issues:
            print("  -", line)
        return 1
    print("审计通过：未发现重复/跨项目/非 approved/无证据/非 supported membership。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
