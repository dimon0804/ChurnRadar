"""auth schema: users and login audit

Revision ID: 0001
Revises:
Create Date: 2026-10-24
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS auth")
    op.execute(
        """
        CREATE TABLE auth.users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            username text NOT NULL CHECK (length(username) BETWEEN 1 AND 128),
            display_name text NOT NULL,
            password_hash text,
            roles text[] NOT NULL DEFAULT '{analyst}'
                CHECK (roles <@ ARRAY['admin', 'analyst']::text[]),
            provider text NOT NULL DEFAULT 'password' CHECK (provider IN ('password', 'oidc')),
            subject text,
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_login_at timestamptz,
            CHECK (provider <> 'password' OR password_hash IS NOT NULL),
            CHECK (provider <> 'oidc' OR subject IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX users_password_username_uq ON auth.users (lower(username)) WHERE provider = 'password'"
    )
    op.execute(
        "CREATE UNIQUE INDEX users_provider_subject_uq ON auth.users (provider, subject) WHERE subject IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE auth.login_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            username text NOT NULL,
            provider text NOT NULL,
            success boolean NOT NULL,
            ip text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX login_events_created_at_idx ON auth.login_events (created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth.login_events")
    op.execute("DROP TABLE IF EXISTS auth.users")
