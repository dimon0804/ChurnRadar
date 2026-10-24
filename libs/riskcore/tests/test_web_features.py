"""Признаки из Яндекс Метрики: срабатывают по агрегатам визитов и молчат без данных."""

from __future__ import annotations

import pytest

from riskcore import feature_catalog, merge_settings, score_customers
from riskcore.registry import get_feature

from .factories import AS_OF, account_ctx, make_account, make_service, make_web

WEB_CODES = ("panel_absence", "web_activity_drop", "cancel_page_views")


def feature(code: str):
    found = get_feature(code)
    assert found is not None
    return found


def test_catalog_lists_web_features_with_defaults():
    catalog = {spec.code: spec for spec in feature_catalog()}
    assert {code: catalog[code].default_weight for code in WEB_CODES} == {
        "panel_absence": 25,
        "web_activity_drop": 20,
        "cancel_page_views": 45,
    }
    for code in WEB_CODES:
        assert catalog[code].level == "account"
        assert catalog[code].group == "behavior"


@pytest.mark.parametrize("code", WEB_CODES)
def test_silent_without_metrica_data(code):
    assert feature(code).compute(account_ctx(services=[make_service()])) is None


def test_panel_absence_ramps_after_two_weeks():
    f = feature("panel_absence")
    assert f.compute(account_ctx(web=make_web(last_visit_days_ago=10))) is None
    assert f.compute(account_ctx(web=make_web(last_visit_days_ago=14))) is None
    mid = f.compute(account_ctx(web=make_web(last_visit_days_ago=29)))
    assert mid is not None and abs(mid.value - 0.5) < 1e-9
    assert "29 дн." in mid.explanation
    full = f.compute(account_ctx(web=make_web(last_visit_days_ago=90)))
    assert full is not None and full.value == 1.0
    assert "напомнить" in f.recommend(full).title


def test_panel_absence_ignores_visits_after_as_of():
    assert feature("panel_absence").compute(account_ctx(web=make_web(last_visit_days_ago=-2))) is None


def test_activity_drop_compares_week_with_usual():
    f = feature("web_activity_drop")
    # обычно (20 − 1) · 7 / 23 ≈ 5.8 визита в неделю, за неделю один: ratio ≈ 0.17
    signal = f.compute(account_ctx(web=make_web(visits_7d=1, visits_30d=20)))
    assert signal is not None
    assert 0.6 < signal.value < 0.7
    assert "1 визит" in signal.explanation and "6 визитов" in signal.explanation
    # половина обычного и выше — не падение
    assert f.compute(account_ctx(web=make_web(visits_7d=3, visits_30d=17))) is None
    zero = f.compute(account_ctx(web=make_web(visits_7d=0, visits_30d=23, last_visit_days_ago=9)))
    assert zero is not None and zero.value == 1.0


def test_activity_drop_needs_history_and_regular_visits():
    f = feature("web_activity_drop")
    assert f.compute(account_ctx(web=make_web(visits_7d=0, visits_30d=20, first_visit_days_ago=20))) is None
    # реже двух визитов в неделю — сравнивать не с чем
    assert f.compute(account_ctx(web=make_web(visits_7d=0, visits_30d=5))) is None


def test_activity_drop_yields_to_panel_absence():
    web = make_web(visits_7d=0, visits_30d=20, last_visit_days_ago=20)
    assert feature("web_activity_drop").compute(account_ctx(web=web)) is None
    assert feature("panel_absence").compute(account_ctx(web=web)) is not None


def test_cancel_page_views_grows_with_count():
    f = feature("cancel_page_views")
    one = f.compute(account_ctx(web=make_web(cancel_visits_30d=1, last_cancel_days_ago=2)))
    assert one is not None and abs(one.value - 0.6) < 1e-9
    assert "1 раз" in one.explanation
    three = f.compute(account_ctx(web=make_web(cancel_visits_30d=3, last_cancel_days_ago=1)))
    assert three is not None and abs(three.value - 1.0) < 1e-9
    assert "3 раза" in three.explanation
    assert "позвонить" in f.recommend(three).title
    assert f.compute(account_ctx(web=make_web(cancel_visits_30d=0))) is None


def test_cancel_page_views_expire_after_window():
    stale = make_web(cancel_visits_30d=2, last_cancel_days_ago=45)
    assert feature("cancel_page_views").compute(account_ctx(web=stale)) is None


def test_score_customers_uses_web_activity():
    account = make_account(id=7)
    service = make_service(id=70, owner_id=7)
    settings = merge_settings(None)
    [plain] = score_customers([account], [service], [], settings, AS_OF)
    web = {7: make_web(account_id=7, cancel_visits_30d=2, last_cancel_days_ago=1)}
    [with_web] = score_customers([account], [service], [], settings, AS_OF, web)
    assert with_web.risk_score is not None and plain.risk_score is not None
    assert with_web.risk_score > plain.risk_score
    codes = {r["code"] for r in with_web.reasons}
    assert "cancel_page_views" in codes
    assert "cancel_page_views" not in {r["code"] for r in plain.reasons}
    # клиенту без строки в словаре признаки сайта не добавляют ничего
    [other] = score_customers([account], [service], [], settings, AS_OF, {8: web[7]})
    assert other.risk_score == plain.risk_score
