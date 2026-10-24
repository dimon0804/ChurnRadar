"""auth: user management — email, pending SSO users, manual roles, audit log

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS email text")
    op.execute("ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()")
    # роль, выставленную админом вручную, вход через SSO не перетирает группами IdP
    op.execute("ALTER TABLE auth.users ADD COLUMN IF NOT EXISTS role_override boolean NOT NULL DEFAULT false")
    op.execute(
        "ALTER TABLE auth.users ADD CONSTRAINT users_email_check "
        "CHECK (email IS NULL OR (length(email) <= 254 AND email LIKE '%_@_%'))"
    )

    # 'sso' — заведён админом «только для SSO» и ещё ни разу не входил: subject появится при первом входе
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_provider_check")
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_sso_subject_check")
    op.execute(
        "ALTER TABLE auth.users ADD CONSTRAINT users_provider_check "
        "CHECK (provider IN ('password', 'oidc', 'saml', 'sso'))"
    )
    op.execute(
        "ALTER TABLE auth.users ADD CONSTRAINT users_sso_subject_check "
        "CHECK (provider IN ('password', 'sso') OR subject IS NOT NULL)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS users_pending_sso_username_uq "
        "ON auth.users (lower(username)) WHERE provider = 'sso'"
    )
    op.execute("CREATE INDEX IF NOT EXISTS users_email_idx ON auth.users (lower(email))")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth.audit_log (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            actor text NOT NULL,
            action text NOT NULL,
            target text,
            details jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS audit_log_created_at_idx ON auth.audit_log (created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth.audit_log")
    op.execute("DROP INDEX IF EXISTS auth.users_email_idx")
    op.execute("DROP INDEX IF EXISTS auth.users_pending_sso_username_uq")
    op.execute("DELETE FROM auth.users WHERE provider = 'sso'")
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_sso_subject_check")
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_provider_check")
    op.execute(
        "ALTER TABLE auth.users ADD CONSTRAINT users_provider_check CHECK (provider IN ('password', 'oidc', 'saml'))"
    )
    op.execute(
        "ALTER TABLE auth.users ADD CONSTRAINT users_sso_subject_check "
        "CHECK (provider = 'password' OR subject IS NOT NULL)"
    )
    op.execute("ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS users_email_check")
    op.execute("ALTER TABLE auth.users DROP COLUMN IF EXISTS role_override")
    op.execute("ALTER TABLE auth.users DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE auth.users DROP COLUMN IF EXISTS email")
