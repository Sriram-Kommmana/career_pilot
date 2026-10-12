"""add resume extraction status and error columns

Revision ID: 73513623a4d8
Revises: 065da2315c77
Create Date: 2026-10-12 12:25:12.543191
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "73513623a4d8"
down_revision: str | None = "065da2315c77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A server_default is given even though the model only declares a Python-side
    # default: a NOT NULL column added to a table that may already have rows needs
    # something to backfill them with, and raw inserts/seeds that bypass the ORM
    # still get a sensible value this way.
    op.add_column(
        "resumes",
        sa.Column(
            "extraction_status", sa.String(length=20), nullable=False, server_default="pending"
        ),
    )
    op.add_column("resumes", sa.Column("extraction_error", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("resumes", "extraction_error")
    op.drop_column("resumes", "extraction_status")
