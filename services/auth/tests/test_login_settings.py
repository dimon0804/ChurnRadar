"""Способы входа и регистрация через SSO: настройки администратора и защита от «запереть всех»."""

from __future__ import annotations

import httpx
import pytest

from app.accounts import SsoDenied, sso_sign_in
from app.config import Settings
from app.login_settings import LoginSettings, MemoryLoginSettingsRepo, normalize_domains
from app.users import MemoryUserRepo, UserRecord

from .conftest import ORIGIN, login
from .test_oidc import FakeAuthentik, start_flow

H = {"Origin": ORIGIN}
URL = "/api/v1/auth/login-settings"


@pytest.fixture
def settings() -> Settings:
    # OIDC настроен (поддельный Authentik ниже), SAML — нет
    return Settings(oidc_enabled=True, saml_enabled=False, log_level="warning")


@pytest.fixture
def idp(settings):
    return FakeAuthentik(settings)


@pytest.fixture
def upstream_transport(idp):
    return httpx.MockTransport(idp)


@pytest.fixture
def login_settings_repo():
    return MemoryLoginSettingsRepo()


async def oidc_login(client, idp) -> httpx.Response:
    """Полный вход через OIDC; по умолчанию Authentik отдаёт группу churn-admins."""
    _, query = await start_flow(client, idp, next_url="/")
    return await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": query["state"]})


async def patch(client, **body) -> httpx.Response:
    return await client.patch(URL, json=body, headers=H)


def verify(client, cookie: str) -> object:
    return client.get(
        "/internal/verify",
        headers={
            "X-Forwarded-Method": "GET",
            "X-Forwarded-Uri": "/api/v1/customers",
            "Cookie": f"cr_session={cookie}",
        },
    )


# ---- доступ и чтение -----------------------------------------------------------------


async def test_only_admin_sees_and_changes_settings(client):
    assert (await client.get(URL)).status_code == 401
    await login(client, "analyst", "analyst")
    assert (await client.get(URL)).status_code == 403
    assert (await patch(client, sso_auto_provision=False)).status_code == 403
    await login(client, "marketer", "marketer")
    assert (await patch(client, sso_auto_provision=False)).status_code == 403


async def test_defaults_and_provider_flags(client):
    await login(client)
    body = (await client.get(URL)).json()
    assert body["password_enabled"] and body["oidc_enabled"] and body["saml_enabled"]
    assert body["sso_auto_provision"] is True
    assert body["sso_default_role"] == "none"
    assert body["allowed_email_domains"] == []
    assert body["methods"]["oidc"] == {
        "configured": True,
        "available": True,
        "active": True,
        "display_name": "Войти через Authentik (OIDC)",
    }
    # SAML не настроен в окружении: флаг в базе включён, но действующим он не считается
    assert body["methods"]["saml"]["configured"] is False and body["methods"]["saml"]["active"] is False
    assert body["break_glass"] is False
    assert body["session_method"] == "password"
    # вошёл паролем — выключить пароль нельзя, и страница заранее говорит почему
    assert "войдите в панель через SSO" in body["password_disable_blocker"]


async def test_csrf_required(client):
    await login(client)
    resp = await client.patch(URL, json={"sso_auto_provision": False}, headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "csrf_rejected"


async def test_domains_are_validated_and_normalized(client):
    await login(client)
    bad = await patch(client, allowed_email_domains=["not a domain"])
    assert bad.status_code == 422
    assert "не похоже на домен" in bad.json()["error"]["message"]
    ok = await patch(client, allowed_email_domains=["@Example.COM", "corp.example.org.", "example.com"])
    assert ok.status_code == 200
    assert ok.json()["allowed_email_domains"] == ["example.com", "corp.example.org"]
    assert (await patch(client, sso_default_role="admin")).status_code == 422
    assert (await patch(client, password_enabled="no")).status_code == 422
    assert (await patch(client, unknown_field=True)).status_code == 422


def test_normalize_domains_limits():
    assert normalize_domains(["", " a.ru "]) == ["a.ru"]
    with pytest.raises(ValueError):
        normalize_domains([f"d{i}.ru" for i in range(51)])


# ---- защита от блокировки ------------------------------------------------------------


async def test_cannot_disable_every_method(client, repo):
    await login(client)
    resp = await patch(client, password_enabled=False, oidc_enabled=False)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_login_methods"
    assert "хотя бы один" in resp.json()["error"]["message"]
    # отказ виден в журнале, настройки не тронуты
    assert repo.audit[-1].action == "auth.settings.update"
    assert repo.audit[-1].details["error"] == "no_login_methods"
    assert (await client.get(URL)).json()["password_enabled"] is True


async def test_unconfigured_provider_cannot_be_switched(client):
    await login(client)
    resp = await patch(client, saml_enabled=False)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "provider_not_configured"
    assert "SAML_" in resp.json()["error"]["message"]


async def test_password_admin_cannot_disable_password(client):
    await login(client)
    resp = await patch(client, password_enabled=False)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "sso_required"
    assert "войдите в панель через SSO" in resp.json()["error"]["message"]
    assert (await client.get("/api/v1/auth/providers")).json()["password"] == {"enabled": True}


async def test_sso_admin_disables_password_and_password_sessions_drop(client, idp, repo):
    # маркетолог сидит в панели по паролю
    await login(client, "marketer", "marketer")
    marketer_cookie = client.cookies.get("cr_session")
    client.cookies.clear()

    await oidc_login(client, idp)
    assert (await client.get("/api/v1/auth/me")).json()["auth_method"] == "oidc"
    assert (await client.get(URL)).json()["password_disable_blocker"] is None

    resp = await patch(client, password_enabled=False)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["password_enabled"] is False
    assert body["revoked_sessions"] == 1
    assert body["updated_by"] == "marketer"  # так зовут админа в поддельном Authentik

    # своя сессия (OIDC) жива, парольная — сброшена
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    assert (await verify(client, marketer_cookie)).status_code == 401

    # вход паролем закрыт на сервере, а не только в интерфейсе
    client.cookies.clear()
    denied = await login(client, "admin", "admin")
    assert denied.status_code == 403
    assert denied.json()["error"] == {
        "code": "password_login_disabled",
        "message": "Вход по паролю отключён администратором, войдите через SSO",
        "request_id": denied.json()["error"]["request_id"],
    }
    providers = (await client.get("/api/v1/auth/providers")).json()
    assert providers["password"] == {"enabled": False}
    assert providers["oidc"]["enabled"] is True

    # в журнале: кто и что поменял
    entry = next(e for e in reversed(repo.audit) if e.action == "auth.settings.update")
    assert entry.actor == "marketer"
    assert entry.details["changes"] == {"password_enabled": {"from": True, "to": False}}
    assert entry.details["revoked_sessions"] == 1


async def test_sso_admin_cannot_drop_his_own_method_with_password(client, idp):
    await oidc_login(client, idp)
    await patch(client, password_enabled=False)
    # пароль уже выключен: выключить ещё и OIDC — значит остаться без входа
    resp = await patch(client, oidc_enabled=False)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_login_methods"
    # вернуть пароль можно всегда
    back = await patch(client, password_enabled=True)
    assert back.status_code == 200 and back.json()["password_enabled"] is True


async def test_old_password_session_is_rejected_even_without_index(client, idp, redis):
    await login(client, "analyst", "analyst")
    analyst_cookie = client.cookies.get("cr_session")
    # сессии, выданные до обновления, не попали в индекс по способу входа
    await redis.delete("cr:msess:password")
    client.cookies.clear()
    await oidc_login(client, idp)
    assert (await patch(client, password_enabled=False)).status_code == 200
    resp = await verify(client, analyst_cookie)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "login_method_disabled"


async def test_disabling_oidc_hides_button_and_blocks_flow(client, idp):
    await oidc_login(client, idp)
    oidc_cookie = client.cookies.get("cr_session")
    client.cookies.clear()
    await login(client)
    resp = await patch(client, oidc_enabled=False)
    assert resp.status_code == 200
    assert resp.json()["revoked_sessions"] == 1
    assert (await verify(client, oidc_cookie)).status_code == 401
    assert (await client.get("/api/v1/auth/providers")).json()["oidc"] == {"enabled": False}
    start = await client.get("/api/v1/auth/oidc/login")
    assert start.status_code == 302 and start.headers["location"] == "/login?error=method_disabled"
    cb = await client.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": "x"})
    assert cb.headers["location"] == "/login?error=method_disabled"


async def test_last_sso_admin_is_protected_while_password_is_off(client, idp, repo, settings):
    await oidc_login(client, idp)
    assert (await patch(client, password_enabled=False)).status_code == 200
    sso_admin = next(u for u in repo.users.values() if u.provider == "oidc")
    # ещё один администратор через SAML есть, но SAML не настроен: войти он не сможет и не в счёт
    await repo.create_user(UserRecord("", "boss", "Босс", ["admin"], "saml", subject="s-1"))
    settings.auth_break_glass_password_login = True
    client.cookies.clear()
    assert (await login(client)).status_code == 200  # аварийный ключ
    resp = await client.patch(f"/api/v1/auth/users/{sso_admin.id}", json={"role": "analyst"}, headers=H)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "last_sso_admin"


# ---- аварийный ключ ------------------------------------------------------------------


@pytest.fixture
def password_off(login_settings_repo):
    # до старта приложения: так выглядит установка, где пароль уже выключен
    login_settings_repo.value = LoginSettings(password_enabled=False)


@pytest.fixture
def break_glass(settings, password_off):
    settings.auth_break_glass_password_login = True
    return settings


async def test_break_glass_lets_only_admins_in(break_glass, client, repo):
    providers = (await client.get("/api/v1/auth/providers")).json()
    assert providers["password"] == {"enabled": True, "admins_only": True}
    assert (await login(client, "analyst", "analyst")).status_code == 403
    assert (await login(client, "analyst", "wrong")).status_code == 401
    ok = await login(client)
    assert ok.status_code == 200
    assert any(e.action == "auth.break_glass_login" for e in repo.audit)
    # админ может вернуть пароль всем
    resp = await patch(client, password_enabled=True)
    assert resp.status_code == 200
    assert (await login(client, "analyst", "analyst")).status_code == 200


async def test_without_break_glass_password_is_closed_for_admin_too(password_off, client):
    resp = await login(client)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "password_login_disabled"


# ---- автосоздание через SSO -----------------------------------------------------------


async def configure(client, **body) -> None:
    """Админ (по паролю) меняет настройки и выходит: дальше идёт вход через SSO."""
    await login(client)
    resp = await patch(client, **body)
    assert resp.status_code == 200, resp.text
    client.cookies.clear()


async def test_auto_provision_off_refuses_unknown_user(client, idp, repo):
    await configure(client, sso_auto_provision=False)
    cb = await oidc_login(client, idp)
    assert cb.headers["location"] == "/login?error=not_provisioned"
    assert not any(u.provider == "oidc" for u in repo.users.values())
    # заведённого заранее пускаем
    await repo.create_user(UserRecord("", "marketer", "Мария", ["marketer"], "sso", role_override=True))
    cb = await oidc_login(client, idp)
    assert cb.headers["location"] == "/"
    assert (await client.get("/api/v1/auth/me")).json()["roles"] == ["marketer"]


async def test_allowed_domains_limit_auto_provision(client, idp):
    await configure(client, allowed_email_domains=["example.com"])
    idp.claims = {"groups": ["churn-admins"], "email": "m@other.org"}
    assert (await oidc_login(client, idp)).headers["location"] == "/login?error=domain_not_allowed"
    idp.claims = {"groups": ["churn-admins"]}
    assert (await oidc_login(client, idp)).headers["location"] == "/login?error=domain_not_allowed"
    idp.claims = {"groups": ["churn-admins"], "email": "m@Example.com"}
    assert (await oidc_login(client, idp)).headers["location"] == "/"


async def test_default_role_for_users_without_groups(client, idp):
    await configure(client, sso_default_role="analyst")
    idp.claims = {"groups": ["staff"]}
    assert (await oidc_login(client, idp)).headers["location"] == "/"
    assert (await client.get("/api/v1/auth/me")).json()["roles"] == ["analyst"]


async def test_sso_sign_in_policy_unit():
    repo = MemoryUserRepo()
    closed = LoginSettings(sso_auto_provision=False)
    with pytest.raises(SsoDenied) as exc:
        await sso_sign_in(repo, "oidc", "s1", "ivan", "Иван", None, ["analyst"], closed)
    assert exc.value.code == "not_provisioned"
    # уже известного пользователя автосоздание не касается
    known = await repo.create_user(UserRecord("", "ivan", "Иван", ["analyst"], "oidc", subject="s1"))
    user = await sso_sign_in(repo, "oidc", "s1", "ivan", "Иван", None, ["analyst"], closed)
    assert user.id == known.id
    # и домены тоже: они только для новых
    picky = LoginSettings(allowed_email_domains=["corp.ru"])
    assert (await sso_sign_in(repo, "oidc", "s1", "ivan", "Иван", "i@gmail.com", ["analyst"], picky)).id == known.id
    with pytest.raises(SsoDenied) as exc:
        await sso_sign_in(repo, "oidc", "s2", "petr", "Пётр", "p@gmail.com", ["analyst"], picky)
    assert exc.value.code == "domain_not_allowed"
    # без групп и без роли по умолчанию — отказ, с ролью — пускаем ею
    with pytest.raises(SsoDenied) as exc:
        await sso_sign_in(repo, "oidc", "s3", "olga", "Ольга", None, [], LoginSettings())
    assert exc.value.code == "forbidden"
    olga = await sso_sign_in(repo, "oidc", "s3", "olga", "Ольга", None, [], LoginSettings(sso_default_role="marketer"))
    assert olga.roles == ["marketer"]


def test_sso_default_role_seeded_from_env():
    from app.login_settings import initial_settings

    assert initial_settings(Settings(sso_default_role="analyst")).sso_default_role == "analyst"
    assert initial_settings(Settings(sso_default_role="")).sso_default_role == "none"
    # админа автоматически не раздаём
    assert initial_settings(Settings(sso_default_role="admin")).sso_default_role == "none"
