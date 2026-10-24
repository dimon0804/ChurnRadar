"""auth: API-токены для интеграций (машинный доступ внешних систем)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Хранится только SHA-256 токена: сам токен показываем один раз при создании.
    # Токен — 32 случайных байта, подбирать его по хешу бессмысленно, медленный хеш не нужен.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth.api_tokens (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name text NOT NULL CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
            role text NOT NULL CHECK (role IN ('admin', 'marketer', 'analyst')),
            token_hash text NOT NULL UNIQUE,
            prefix text NOT NULL,
            allowed_ips text[] NOT NULL DEFAULT '{}',
            rate_limit_per_min integer CHECK (rate_limit_per_min IS NULL OR rate_limit_per_min > 0),
            expires_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            created_by text NOT NULL,
            revoked_at timestamptz,
            revoked_by text,
            last_used_at timestamptz,
            last_used_ip text
        )
        """
    )
    # одно имя — один действующий токен; отозванные остаются для истории
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS api_tokens_active_name_uq "
        "ON auth.api_tokens (lower(name)) WHERE revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth.api_tokens")
