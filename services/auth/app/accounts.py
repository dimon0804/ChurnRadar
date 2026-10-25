"""Who may enter through SSO, and what happens to the user record on that login."""

from __future__ import annotations

import re

from .login_settings import LoginSettings
from .users import UserRecord, UserRepo

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")


def clean_email(value: str | None) -> str | None:
    """Normalised email or None when it does not look like one (IdPs send all sorts)."""
    if not value:
        return None
    value = value.strip()
    return value if len(value) <= 254 and EMAIL_RE.match(value) else None


class SsoDenied(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code


async def sso_sign_in(
    repo: UserRepo,
    provider: str,
    subject: str,
    username: str,
    display_name: str,
    email: str | None,
    roles: list[str],
    policy: LoginSettings | None = None,
) -> UserRecord:
    """JIT provisioning for OIDC/SAML.

    * already known (provider + subject): profile refreshed, role follows IdP groups unless an admin
      set it by hand; a user blocked in the panel stays out even if the IdP lets him in;
    * pre-created by an admin as "SSO only": the first login claims that record (by username)
      and keeps the role the admin chose;
    * unknown: created on the spot, role from IdP groups; no matching group — the default role
      from the login settings, or no access. With auto-provisioning off nobody new gets in, and
      the allowed email domains (if set) limit who may be created.
    """
    policy = policy or LoginSettings()
    email = email or None
    roles = roles or policy.default_roles
    user = await repo.find_sso_user(provider, subject)
    if user is not None:
        if not user.is_active:
            raise SsoDenied("blocked", "user is blocked in the panel")
        changes: dict = {"username": username, "display_name": display_name}
        if email:
            changes["email"] = email
        if not user.role_override:
            if not roles:
                raise SsoDenied("forbidden", "no panel role in IdP groups")
            changes["roles"] = roles
        updated = await repo.update_user(user.id, **changes)
        await repo.touch_login(user.id)
        return updated or user

    # заготовку забираем только по логину: email из IdP часто не подтверждён (Authentik так и пишет
    # email_verified=false), и сопоставление по нему отдало бы чужую учётку
    pending = await repo.find_pending_sso_user(username, None)
    if pending is not None:
        if not pending.is_active:
            raise SsoDenied("blocked", "user is blocked in the panel")
        claimed = await repo.update_user(
            pending.id,
            provider=provider,
            subject=subject,
            display_name=display_name or pending.display_name,
            email=pending.email or email,
            role_override=True,
        )
        await repo.touch_login(pending.id)
        return claimed or pending

    if not policy.sso_auto_provision:
        raise SsoDenied("not_provisioned", "auto-provisioning is off and the user was not created in advance")
    if not policy.domain_allowed(email):
        raise SsoDenied("domain_not_allowed", "email domain is not in the allowed list")
    if not roles:
        raise SsoDenied("forbidden", "no panel role in IdP groups")
    created = await repo.create_user(
        UserRecord(
            id="",
            username=username,
            display_name=display_name,
            roles=roles,
            provider=provider,
            subject=subject,
            email=email,
        )
    )
    await repo.touch_login(created.id)
    return created
