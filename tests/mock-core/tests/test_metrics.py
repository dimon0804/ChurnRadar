import re

from conftest import METRICS_TOKEN


def metric_value(text: str, name: str, labels: str = "") -> float:
    pattern = rf"^{re.escape(name)}{re.escape(labels)} (\S+)$"
    match = re.search(pattern, text, re.MULTILINE)
    assert match, f"{name}{labels} not found"
    return float(match.group(1))


def test_metrics_requires_token(client):
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": METRICS_TOKEN}).status_code == 401
    resp = client.get("/metrics", headers={"Authorization": "Basic " + METRICS_TOKEN})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_metrics_content(client):
    summary = client.get("/api/v1/analytics/summary").json()
    resp = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    text = resp.text
    for level in ("high", "medium", "low", "unknown"):
        value = metric_value(text, "churn_customers_by_risk_level", f'{{level="{level}"}}')
        assert value == summary["by_level"][level]
    assert metric_value(text, "churn_expected_loss_eur") == summary["expected_loss"] / 100
    assert metric_value(text, "churn_exposure_eur") == summary["exposure"] / 100
    assert metric_value(text, "churn_services_at_risk") == summary["services_at_risk"]
    assert metric_value(text, "churn_model_version") == summary["model_version"]
    assert metric_value(text, "churn_last_scored_timestamp_seconds") > 1.7e9
    assert "churn_scoring_duration_seconds_bucket" in text
    assert "churn_scoring_runs_total" in text
    request_lines = [line for line in text.splitlines() if line.startswith("http_requests_total{")]
    assert any(
        'route="/api/v1/analytics/summary"' in line and 'status="200"' in line and 'service="mock-core"' in line
        for line in request_lines
    )
