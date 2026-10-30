"""Autosave counter on projects and a pruning index on project_versions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column("autosaves_since_snapshot", sa.Integer(), nullable=False, server_default="0")
        )
    op.create_index(
        "ix_project_versions_project_kind_rev",
        "project_versions",
        ["project_id", "kind", "revision"],
    )


def downgrade() -> None:
    op.drop_index("ix_project_versions_project_kind_rev", "project_versions")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("autosaves_since_snapshot")
