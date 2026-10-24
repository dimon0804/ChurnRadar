"""Контекст клиента для проверки признаков сайта по агрегатам из базы."""

from __future__ import annotations

from datetime import datetime, timedelta

from riskcore.context import AccountContext, build_account_context
from riskcore.types import Account, Service, WebActivity


def ctx_for(web: WebActivity, as_of: datetime) -> AccountContext:
    account = Account(web.account_id, "x@example.test", 1000, 0, 1000, 0, "locked")
    service = Service(1, "vps", web.account_id, "month", True, as_of - timedelta(days=60),
                      as_of + timedelta(days=20), "active", 1000)
    return build_account_context(account, [service], [], as_of, 30, web)
