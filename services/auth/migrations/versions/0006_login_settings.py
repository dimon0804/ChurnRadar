"""auth: способы входа и регистрация через SSO (настройки администратора)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Одна строка на всю установку. Первую запись заводит сам auth при старте: роль по умолчанию
    # для SSO берётся из SSO_DEFAULT_ROLE, чтобы обновление не поменяло поведение молча.
    # Флаги oidc/saml работают поверх env: провайдер, не настроенный в окружении, выключен всегда.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth.login_settings (
            id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            password_enabled boolean NOT NULL DEFAULT true,
            oidc_enabled boolean NOT NULL DEFAULT true,
            saml_enabled boolean NOT NULL DEFAULT true,
            sso_auto_provision boolean NOT NULL DEFAULT true,
            sso_default_role text NOT NULL DEFAULT 'none'
                CHECK (sso_default_role IN ('none', 'analyst', 'marketer')),
            allowed_email_domains text[] NOT NULL DEFAULT '{}',
            updated_at timestamptz NOT NULL DEFAULT now(),
            updated_by text
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth.login_settings")
