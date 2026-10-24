"""auth: SAML users next to password and OIDC ones

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_provider_check")
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_check1")
    op.execute(
        "ALTER TABLE auth.users ADD CONSTRAINT users_provider_check CHECK (provider IN ('password', 'oidc', 'saml'))"
    )
    op.execute(
        "ALTER TABLE auth.users ADD CONSTRAINT users_sso_subject_check "
        "CHECK (provider = 'password' OR subject IS NOT NULL)"
    )


def downgrade() -> None:
    op.execute("DELETE FROM auth.users WHERE provider = 'saml'")
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_sso_subject_check")
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_provider_check")
    op.execute("ALTER TABLE auth.users ADD CONSTRAINT users_provider_check CHECK (provider IN ('password', 'oidc'))")
    op.execute("ALTER TABLE auth.users ADD CONSTRAINT users_check1 CHECK (provider <> 'oidc' OR subject IS NOT NULL)")
