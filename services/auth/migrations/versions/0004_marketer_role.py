"""auth: третья роль — marketer (работа с клиентами без настройки системы)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_roles_check")
    op.execute(
        "ALTER TABLE auth.users ADD CONSTRAINT users_roles_check "
        "CHECK (roles <@ ARRAY['admin', 'marketer', 'analyst']::text[])"
    )


def downgrade() -> None:
    # маркетологи при откате становятся аналитиками: так они точно ничего не получат сверх прежнего
    op.execute(
        "UPDATE auth.users SET roles = array_replace(roles, 'marketer', 'analyst') WHERE 'marketer' = ANY(roles)"
    )
    op.execute("UPDATE auth.users SET roles = ARRAY(SELECT DISTINCT unnest(roles)) WHERE array_length(roles, 1) > 1")
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_roles_check")
    op.execute(
        "ALTER TABLE auth.users ADD CONSTRAINT users_roles_check CHECK (roles <@ ARRAY['admin', 'analyst']::text[])"
    )
