"""Unit-тесты api без БД: сортировка, фильтры, права, формат ошибок, метрики, настройки, прокси."""

from __future__ import annotations

import httpx
import pytest
from svckit.errors import ApiError

from app import model_catalog, queries
from app.model_settings import ModelSettingsPatch, apply_patch, diff_settings
from tests.conftest import ADMIN, ANALYST, NOW, FakePool


def assert_error(resp, status: int, code: str):
    assert resp.status_code == status, resp.text
    err = resp.json()["error"]
    assert set(resp.json()) == {"error"}
    assert err["code"] == code
    assert err["message"]
    assert err["request_id"] == resp.headers["x-request-id"]
    return err


# --- сортировка и фильтры -----------------------------------------------------------

SORTS = ["id", "email", "balance", "total_replenished", "risk_score", "expected_loss"]


@pytest.mark.parametrize("sort", SORTS)
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_order_by_whitelist(sort, order):
    sql = queries.order_by(sort, order)
    assert sql.startswith(queries.SORT_COLUMNS[sort])
    assert order.upper() in sql
    if sort == "risk_score":
        assert "NULLS LAST" in sql
    if sort != "id":
        assert sql.endswith("customer_id ASC")


def test_unknown_always_last_regardless_of_order():
    assert queries.order_by("risk_score", "asc") == "risk_score ASC NULLS LAST, customer_id ASC"
    assert queries.order_by("risk_score", "desc") == "risk_score DESC NULLS LAST, customer_id ASC"


def test_sort_is_not_injectable():
    with pytest.raises(KeyError):
        queries.order_by("risk_score; DROP TABLE x", "asc")


async def test_bad_sort_param_is_422(client):
    err = assert_error(await client.get("/api/v1/customers?sort=password", headers=ANALYST), 422, "validation_error")
    assert err["details"][0]["field"] == "sort"
    assert_error(await client.get("/api/v1/customers?order=up", headers=ANALYST), 422, "validation_error")
    assert_error(await client.get("/api/v1/customers?page_size=201", headers=ANALYST), 422, "validation_error")
    assert_error(await client.get("/api/v1/customers?page=0", headers=ANALYST), 422, "validation_error")


def test_parse_levels():
    assert queries.parse_levels("medium, HIGH,medium") == ["high", "medium"]
    assert queries.parse_levels("") == []
    assert queries.parse_levels(None) == []
    with pytest.raises(ApiError) as exc:
        queries.parse_levels("high,critical")
    assert exc.value.status == 422


def test_where_clause_search_by_id_or_email():
    sql, args = queries.where_clause(["high"], "42")
    assert "risk_level = ANY($1::text[])" in sql
    assert "customer_id = $3 OR email ILIKE $2" in sql
    assert args == [["high"], "%42%", 42]


def test_where_clause_escapes_like():
    sql, args = queries.where_clause([], "a_b%")
    assert args == ["%a\\_b\\%%"]
    assert sql == " WHERE email ILIKE $1"


# --- права ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/customers", None),
        ("GET", "/api/v1/customers/1", None),
        ("GET", "/api/v1/analytics/summary", None),
        ("GET", "/api/v1/model/settings", None),
        ("GET", "/api/v1/model/settings/history", None),
        ("PATCH", "/api/v1/model/settings", {"horizon_days": 10}),
        ("POST", "/api/v1/model/settings/reset", None),
        ("GET", "/api/v1/simulator", None),
        ("PUT", "/api/v1/simulator", {"running": True}),
    ],
)
async def test_requires_user(client, method, path, body):
    assert_error(await client.request(method, path, json=body), 401, "unauthorized")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/api/v1/model/settings", {"horizon_days": 10}),
        ("POST", "/api/v1/model/settings/reset", None),
        ("PUT", "/api/v1/simulator", {"running": True}),
    ],
)
async def test_admin_only(client, method, path, body):
    assert_error(await client.request(method, path, json=body, headers=ANALYST), 403, "forbidden")


async def test_dev_user_fallback(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_dev_user", "dev-admin")
    resp = await client.patch("/api/v1/model/settings", json={})
    # пользователь подставлен, дальше упираемся в валидацию пустого патча
    assert_error(resp, 422, "validation_error")


# --- ошибки и служебные пути ---------------------------------------------------------------

async def test_not_found_route(client):
    assert_error(await client.get("/api/v1/nothing", headers=ANALYST), 404, "not_found")


async def test_builtin_docs_disabled(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 404


async def test_swagger_ui_and_spec(client):
    html = await client.get("/api/v1/docs")
    assert html.status_code == 200
    assert "swagger-ui" in html.text and "/api/v1/openapi.yaml" in html.text
    spec = await client.get("/api/v1/openapi.yaml")
    assert spec.status_code == 200
    assert spec.text.startswith("openapi: 3.1.0")
    assert "yaml" in spec.headers["content-type"]


async def test_swagger_works_offline(client):
    html = (await client.get("/api/v1/docs")).text
    assert "http://" not in html and "https://" not in html and "//cdn" not in html
    js = await client.get("/api/v1/docs/swagger-ui-bundle.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "SwaggerUIBundle" in js.text
    css = await client.get("/api/v1/docs/swagger-ui.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert_error(await client.get("/api/v1/docs/secret.py"), 404, "not_found")


async def test_metrics_requires_token(client):
    assert_error(await client.get("/metrics"), 401, "unauthorized")
    assert_error(await client.get("/metrics", headers={"Authorization": "Basic abc"}), 401, "unauthorized")


async def test_metrics_values_in_euro(client, app):
    app.state.pool = FakePool(
        {
            "FROM scoring.summary": {
                "data": {
                    "customers_total": 3,
                    "by_level": {"high": 1, "medium": 1, "low": 0, "unknown": 1},
                    "expected_loss": 12345,
                    "exposure": 50000,
                    "services_at_risk": 4,
                    "services_total": 9,
                    "monthly_revenue_total": 99900,
                    "revenue_at_risk_share": 0.25,
                    "horizon_days": 30,
                    "risk_scores": {
                        "scored": 2,
                        "avg": 57.5,
                        "max": 84.25,
                        "buckets": [{"le": le, "count": (le >= 40) + (le >= 90)} for le in range(10, 101, 10)],
                    },
                },
                "model_version": 7,
                "scored_at": NOW,
            }
        }
    )
    resp = await client.get("/metrics", headers={"Authorization": "Bearer churn-metrics-token"})
    assert resp.status_code == 200
    text = resp.text
    assert 'churn_customers_by_risk_level{level="high"} 1.0' in text
    assert 'churn_customers_by_risk_level{level="unknown"} 1.0' in text
    assert "churn_expected_loss_eur 123.45" in text
    assert "churn_exposure_eur 500.0" in text
    assert "churn_monthly_revenue_eur 999.0" in text
    assert "churn_services_at_risk 4.0" in text
    assert "churn_model_version 7.0" in text
    line = next(ln for ln in text.splitlines() if ln.startswith("churn_last_scored_timestamp_seconds "))
    assert float(line.split()[1]) == NOW.timestamp()
    assert "http_request_duration_seconds_bucket" in text
    assert "churn_revenue_at_risk_ratio 0.25" in text
    assert "churn_risk_score_avg 57.5" in text
    assert "churn_risk_score_max 84.25" in text
    assert "churn_risk_score_customers 2.0" in text
    assert "# TYPE churn_risk_score_bucket gauge" in text
    assert 'churn_risk_score_bucket{le="30"} 0.0' in text
    assert 'churn_risk_score_bucket{le="40"} 1.0' in text
    assert 'churn_risk_score_bucket{le="100"} 2.0' in text
    assert 'churn_risk_score_bucket{le="+Inf"} 2.0' in text


async def test_metrics_without_risk_scores_in_old_summary(client, app):
    """Сводка, посчитанная до появления risk_scores, не ломает /metrics: корзины на месте, нули."""
    app.state.pool = FakePool(
        {"FROM scoring.summary": {"data": {"customers_total": 1}, "model_version": 1, "scored_at": NOW}}
    )
    resp = await client.get("/metrics", headers={"Authorization": "Bearer churn-metrics-token"})
    assert resp.status_code == 200
    assert "churn_risk_score_avg 0.0" in resp.text
    assert 'churn_risk_score_bucket{le="10"} 0.0' in resp.text
    assert 'churn_risk_score_bucket{le="+Inf"} 0.0' in resp.text


async def test_summary_empty_when_not_scored(client, app):
    app.state.pool = FakePool({"FROM scoring.summary": None, "FROM scoring.settings": None})
    resp = await client.get("/api/v1/analytics/summary", headers=ANALYST)
    assert resp.status_code == 200
    body = resp.json()
    assert body["customers_total"] == 0
    assert body["by_level"] == {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    assert body["scored_at"] is None
    assert body["timeline"] == [] and body["top_reasons"] == []
    assert body["horizon_days"] == 30 and body["model_version"] == 0


async def test_customer_not_found(client, app):
    app.state.pool = FakePool({"FROM scoring.customer_scores WHERE customer_id": None})
    assert_error(await client.get("/api/v1/customers/5", headers=ANALYST), 404, "not_found")
    assert_error(await client.get("/api/v1/customers/abc", headers=ANALYST), 422, "validation_error")


# --- настройки модели ---------------------------------------------------------------

def _current():
    api = model_catalog.defaults_api(NOW)
    return model_catalog.storage_from_api(api), api["weights"]


def test_patch_applies_and_diffs(fake_riskcore):
    current, catalog = _current()
    patch = ModelSettingsPatch.model_validate(
        {"horizon_days": 60, "thresholds": {"high": 70}, "weights": [{"code": "churn_history", "weight": 10, "enabled": False}]}
    )
    new = apply_patch(current, catalog, patch)
    assert new["horizon_days"] == 60
    assert new["thresholds"] == {"medium": 35.0, "high": 70.0}
    assert new["weights"]["churn_history"] == {"weight": 10.0, "enabled": False}
    assert current["horizon_days"] == 30  # исходник не мутирован
    paths = [c["path"] for c in diff_settings(current, new)]
    assert paths == ["horizon_days", "thresholds.high", "weights.churn_history.weight", "weights.churn_history.enabled"]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"thresholds": {"medium": 70}}, "thresholds"),
        ({"thresholds": {"medium": 50, "high": 50}}, "thresholds"),
        ({"weights": [{"code": "no_such_feature", "weight": 5}]}, "weights.0.code"),
        ({"weights": [{"code": "prolong_wait", "weight": 101}]}, "weights.0.weight"),
        ({"weights": [{"code": "prolong_wait", "weight": -1}]}, "weights.0.weight"),
        ({"weights": [{"code": "prolong_wait"}, {"code": "prolong_wait"}]}, "weights.1.code"),
    ],
)
def test_patch_validation(fake_riskcore, body, field):
    current, catalog = _current()
    with pytest.raises(ApiError) as exc:
        apply_patch(current, catalog, ModelSettingsPatch.model_validate(body))
    assert exc.value.status == 422
    assert exc.value.details[0]["field"] == field


@pytest.mark.parametrize(
    "body",
    [{}, {"horizon_days": 0}, {"horizon_days": 366}, {"thresholds": {"high": 120}}, {"unknown": 1},
     {"weights": [{"weight": 5}]}, {"weights": [{"code": "prolong_wait", "weight": "NaN"}]}],
)
async def test_patch_schema_errors(client, body):
    assert_error(await client.patch("/api/v1/model/settings", json=body, headers=ADMIN), 422, "validation_error")


# --- прокси симулятора -----------------------------------------------------------------

async def test_simulator_proxy_forwards_user(client, app):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content
        return httpx.Response(200, json={"running": True, "interval_ms": 1000, "events_emitted": 3})

    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://simulator:8000")
    resp = await client.put("/api/v1/simulator", json={"running": True}, headers=ADMIN)
    assert resp.status_code == 200
    assert resp.json()["events_emitted"] == 3
    assert seen["headers"]["x-user-name"] == "admin"
    assert seen["headers"]["x-user-roles"] == "admin"
    assert b'"running"' in seen["body"]


async def test_simulator_unavailable(client, app):
    def handler(request):
        raise httpx.ConnectError("refused")

    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://simulator:8000")
    assert_error(await client.get("/api/v1/simulator", headers=ANALYST), 503, "simulator_unavailable")


# --- пробы ---------------------------------------------------------------------------------

async def test_probes(client, app):
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["checks"] == {"db": False, "nats": True}
    app.state.pool = FakePool({"SELECT 1": 1})
    ready = await client.get("/readyz")
    assert ready.status_code == 200 and ready.json()["status"] == "ok"


# --- карточка клиента ------------------------------------------------------------------------

def _score_row(detail: dict | None) -> dict:
    return {
        "customer_id": 7, "email": "a@b.c", "balance": 0, "bonus_balance": 0, "bonus_state": "locked",
        "withdraw_balance": 0, "total_replenished": 100, "risk_score": 70.0, "risk_level": "high",
        "loyalty": "low", "reasons": ["x"], "services_total": 1, "services_at_risk": 1,
        "monthly_revenue": 499, "expected_loss": 300, "exposure": 499,
        "detail": detail, "model_version": 3, "scored_at": NOW,
    }


async def test_customer_card_passes_next_action(client, app):
    action = {"code": "call", "title": "Позвонить", "priority": 1}
    app.state.pool = FakePool({"FROM scoring.customer_scores": _score_row({"next_action": action})})
    body = (await client.get("/api/v1/customers/7", headers=ANALYST)).json()
    assert body["next_action"] == action
    assert body["model_version"] == 3


async def test_customer_card_without_next_action(client, app):
    app.state.pool = FakePool({"FROM scoring.customer_scores": _score_row(None)})
    body = (await client.get("/api/v1/customers/7", headers=ANALYST)).json()
    assert body["next_action"] is None
    assert body["recommendations"] == []


class _SummaryConn:
    def __init__(self, state: dict) -> None:
        self.state = state

    async def fetchrow(self, sql: str, *args: object) -> dict | None:
        self.state["queries"].append(sql)
        if "data" in sql:
            return {"data": {"customers_total": self.state["total"]}, **self.state["stamp"]}
        return dict(self.state["stamp"])


class _SummaryPool:
    def __init__(self, state: dict) -> None:
        self.state = state

    def acquire(self):  # type: ignore[no-untyped-def]
        state = self.state

        class _Ctx:
            async def __aenter__(self) -> _SummaryConn:
                return _SummaryConn(state)

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


async def test_summary_cache_reloads_after_rescore() -> None:
    state = {"queries": [], "total": 10, "stamp": {"scored_at": NOW, "model_version": 1}}
    pool = _SummaryPool(state)
    cache = queries.SummaryCache(ttl_s=60)

    assert (await cache.get(pool))["customers_total"] == 10  # type: ignore[arg-type]
    state["total"] = 99
    assert (await cache.get(pool))["customers_total"] == 10  # type: ignore[arg-type]

    # пересчёт сменил отметку: кэш не должен отдать прежние цифры, даже в пределах TTL
    state["stamp"] = {"scored_at": NOW, "model_version": 2}
    assert (await cache.get(pool))["customers_total"] == 99  # type: ignore[arg-type]
