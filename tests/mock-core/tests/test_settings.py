import json

from fastapi.testclient import TestClient

from app.main import create_app
from conftest import ADMIN, ANALYST, make_config


def test_default_settings(client):
    s = client.get("/api/v1/model/settings").json()
    assert s["version"] == 1
    assert s["horizon_days"] == 30
    assert s["thresholds"] == {"medium": 35.0, "high": 65.0}
    codes = [w["code"] for w in s["weights"]]
    assert len(codes) == 7
    assert "autoprolong_off_expiring" in codes
    for w in s["weights"]:
        assert w["min"] == 0 and w["max"] == 100
        assert w["weight"] == w["default_weight"]
        assert w["group"] in ("renewal", "finance", "behavior")
        assert w["level"] in ("service", "account")


def test_patch_requires_admin(client):
    for headers in ({}, ANALYST):
        resp = client.patch("/api/v1/model/settings", json={"horizon_days": 10}, headers=headers)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"
    assert client.post("/api/v1/model/settings/reset", headers=ANALYST).status_code == 403
    assert client.get("/api/v1/model/settings").json()["version"] == 1


def test_patch_changes_scores_and_bumps_version(client, recorder):
    before = client.get("/api/v1/customers/7").json()
    resp = client.patch(
        "/api/v1/model/settings",
        json={"weights": [{"code": "failed_payments", "enabled": False}], "thresholds": {"high": 90}},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 2
    assert body["updated_by"] == "admin"
    assert body["thresholds"]["high"] == 90
    after = client.get("/api/v1/customers/7").json()
    assert after["model_version"] == 2
    assert after["risk_score"] < before["risk_score"]
    failed = next(f for f in after["features"] if f["code"] == "failed_payments")
    assert failed["weight"] == 0 and failed["contribution"] == 0

    types = [e["type"] for e in recorder.events]
    assert types == ["settings.changed", "scores.updated"]
    assert recorder.events[0]["payload"] == {"version": 2, "updated_by": "admin"}
    scores = recorder.events[1]["payload"]
    assert scores["full"] is True and scores["changed_customer_ids"] == []
    assert scores["summary"]["customers_total"] == 10


def test_patch_validation(client):
    cases = [
        {},
        {"horizon_days": 0},
        {"horizon_days": 366},
        {"thresholds": {"medium": 80, "high": 60}},
        {"thresholds": {"high": 120}},
        {"weights": [{"code": "nope", "weight": 10}]},
        {"weights": [{"code": "failed_payments", "weight": 150}]},
        {"weights": [{"code": "failed_payments", "weight": -1}]},
        {"unknown_field": 1},
    ]
    for payload in cases:
        resp = client.patch("/api/v1/model/settings", json=payload, headers=ADMIN)
        assert resp.status_code == 422, payload
        assert resp.json()["error"]["code"] == "validation_error"
    assert client.get("/api/v1/model/settings").json()["version"] == 1


def test_history_and_reset(client):
    client.patch("/api/v1/model/settings", json={"horizon_days": 14}, headers=ADMIN)
    client.patch(
        "/api/v1/model/settings", json={"weights": [{"code": "recent_deletions", "weight": 60}]}, headers=ADMIN
    )
    reset = client.post("/api/v1/model/settings/reset", headers=ADMIN)
    assert reset.status_code == 200
    assert reset.json()["version"] == 4
    assert reset.json()["horizon_days"] == 30

    items = client.get("/api/v1/model/settings/history").json()["items"]
    assert [i["version"] for i in items] == [4, 3, 2]
    assert items[2]["changes"] == [{"path": "horizon_days", "old": 30, "new": 14}]
    assert items[1]["changes"] == [{"path": "weights.recent_deletions.weight", "old": 25.0, "new": 60.0}]
    assert {c["path"] for c in items[0]["changes"]} == {"horizon_days", "weights.recent_deletions.weight"}
    assert all(i["changed_by"] == "admin" for i in items)
    assert len(client.get("/api/v1/model/settings/history", params={"limit": 1}).json()["items"]) == 1


def test_horizon_changes_timeline(client):
    client.patch("/api/v1/model/settings", json={"horizon_days": 7}, headers=ADMIN)
    s = client.get("/api/v1/analytics/summary").json()
    assert s["horizon_days"] == 7
    assert len([p for p in s["timeline"] if p["kind"] == "forecast"]) == 7


def test_settings_survive_restart(settings_path):
    with TestClient(create_app(make_config(settings_path))) as c:
        c.patch(
            "/api/v1/model/settings",
            json={"horizon_days": 45, "weights": [{"code": "no_recent_payments", "weight": 70, "enabled": False}]},
            headers=ADMIN,
        )
    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert stored["version"] == 2

    with TestClient(create_app(make_config(settings_path))) as c:
        s = c.get("/api/v1/model/settings").json()
        assert s["version"] == 2
        assert s["horizon_days"] == 45
        assert s["updated_by"] == "admin"
        w = next(w for w in s["weights"] if w["code"] == "no_recent_payments")
        assert w["weight"] == 70 and w["enabled"] is False
        history = c.get("/api/v1/model/settings/history").json()["items"]
        assert history[0]["version"] == 2
        assert c.get("/api/v1/customers").json()["model_version"] == 2


def test_unwritable_settings_path_does_not_break_service(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with TestClient(create_app(make_config(blocker / "settings.json"))) as c:
        resp = c.patch("/api/v1/model/settings", json={"horizon_days": 20}, headers=ADMIN)
        assert resp.status_code == 200
        assert resp.json()["version"] == 2


def test_groups_and_calibration_in_settings(client):
    s = client.get("/api/v1/model/settings").json()
    assert s["calibrated"] is True
    assert s["calibration"] == {"available": True, "matches_settings": True}
    payment = next(g for g in s["groups"] if g["code"] == "payment")
    assert payment["enabled"] is False and payment["cap"] == payment["default_cap"] == 85

    body = client.patch(
        "/api/v1/model/settings",
        json={"groups": [{"code": "payment", "enabled": True, "cap": 70}], "calibrated": False},
        headers=ADMIN,
    ).json()
    payment = next(g for g in body["groups"] if g["code"] == "payment")
    assert payment["enabled"] is True and payment["cap"] == 70
    assert body["calibrated"] is False
    assert body["calibration"]["matches_settings"] is False

    items = client.get("/api/v1/model/settings/history").json()["items"]
    assert {c["path"] for c in items[0]["changes"]} == {"groups.payment.enabled", "groups.payment.cap", "calibrated"}
    assert "settings" not in items[0]

    for bad in ({"groups": [{"code": "nope", "enabled": True}]}, {"groups": [{"code": "payment", "cap": 101}]}):
        assert client.patch("/api/v1/model/settings", json=bad, headers=ADMIN).status_code == 422


def test_rollback_creates_new_version(client, recorder):
    client.patch("/api/v1/model/settings", json={"horizon_days": 14}, headers=ADMIN)
    client.patch("/api/v1/model/settings", json={"thresholds": {"high": 80}}, headers=ADMIN)

    resp = client.post("/api/v1/model/settings/rollback", json={"version": 2}, headers=ADMIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 4
    assert body["horizon_days"] == 14 and body["thresholds"]["high"] == 65
    items = client.get("/api/v1/model/settings/history").json()["items"]
    assert items[0]["version"] == 4
    assert items[0]["changes"] == [{"path": "thresholds.high", "old": 80.0, "new": 65.0}]
    assert [e["type"] for e in recorder.events][-2:] == ["settings.changed", "scores.updated"]

    # к первой версии (значения по умолчанию) тоже можно вернуться
    first = client.post("/api/v1/model/settings/rollback", json={"version": 1}, headers=ADMIN).json()
    assert first["version"] == 5 and first["horizon_days"] == 30
    # откат к тому, что уже действует, версию не двигает
    same = client.post("/api/v1/model/settings/rollback", json={"version": 5}, headers=ADMIN).json()
    assert same["version"] == 5

    assert client.post("/api/v1/model/settings/rollback", json={"version": 99}, headers=ADMIN).status_code == 404
    assert client.post("/api/v1/model/settings/rollback", json={"version": 2}, headers=ANALYST).status_code == 403


def test_rollback_survives_restart(settings_path):
    with TestClient(create_app(make_config(settings_path))) as c:
        c.patch("/api/v1/model/settings", json={"horizon_days": 45}, headers=ADMIN)
        c.patch("/api/v1/model/settings", json={"horizon_days": 60}, headers=ADMIN)
    with TestClient(create_app(make_config(settings_path))) as c:
        body = c.post("/api/v1/model/settings/rollback", json={"version": 2}, headers=ADMIN).json()
        assert body["version"] == 4 and body["horizon_days"] == 45


def test_compare_versions(client):
    client.patch("/api/v1/model/settings", json={"horizon_days": 14}, headers=ADMIN)
    client.patch("/api/v1/model/settings", json={"groups": [{"code": "payment", "enabled": True}]}, headers=ADMIN)

    body = client.get("/api/v1/model/settings/compare", params={"from": 1, "to": 3}, headers=ADMIN).json()
    assert body["from"]["version"] == 1 and body["to"]["version"] == 3
    assert body["to"]["changed_by"] == "admin"
    assert {c["path"] for c in body["changes"]} == {"horizon_days", "groups.payment.enabled"}
    assert body["impact"] is None

    missing = client.get("/api/v1/model/settings/compare", params={"from": 1, "to": 42}, headers=ADMIN)
    assert missing.status_code == 404
    forbidden = client.get("/api/v1/model/settings/compare", params={"from": 1, "to": 3}, headers=ANALYST)
    assert forbidden.status_code == 403
