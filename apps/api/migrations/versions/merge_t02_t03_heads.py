"""merge: 收敛 T02/T03 双 head 迁移分支

仓库历史中 T02（52eb455d64d3）与 T03（t03_parse_job_snapshot）均基于
58918ea257fd 独立分叉并被分别合入 master，导致迁移图长期存在两个 head，
`alembic upgrade head` 无法收敛。本迁移仅合并两条分支，不执行任何 DDL，
使迁移图恢复单一 head，并保证 `upgrade head -> downgrade -1 -> upgrade head`
往返仍各只回退一步（本迁移的 downgrade 在 smoke 测试中不会被触发）。

Revision ID: merge_t02_t03_heads
Revises: 52eb455d64d3, t03_parse_job_snapshot
Create Date: 2026-08-02
"""
from collections.abc import Sequence

from alembic import op

revision: str = "merge_t02_t03_heads"
down_revision: str | Sequence[str] | None = ("52eb455d64d3", "t03_parse_job_snapshot")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 仅合并分支，无数据变更。
    pass


def downgrade() -> None:
    # 分支合并不可逆为单分支；此处无需撤销任何 DDL。
    pass
