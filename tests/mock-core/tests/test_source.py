import asyncio
import copy

from conftest import ADMIN, ANALYST


def test_snapshot_requires_admin(client, sample_data):
    assert client.put("/api/v1/source/snapshot", json=sample_data, headers=ANALYST).status_code == 403
    assert client.post("/api/v1/source/events", json={}, headers=ANALYST).status_code == 403
    assert client.put("/api/v1/simulator", json={"running": True}, headers=ANALYST).status_code == 403


def test_snapshot_replaces_data(client, sample_data):
    data = copy.deepcopy(sample_data)
    data["accounts"] = data["accounts"][:2]
    data["services"] = [s for s in data["services"] if s["ownerId"] in (1, 2)]
    ids = {s["id"] for s in data["services"]}
    data["transactions"] = [t for t in data["transactions"] if t["serviceId"] in ids]
    data["reference_time"] = "2026-10-20T00:00:00Z"
    resp = client.put("/api/v1/source/snapshot", json=data, headers=ADMIN)
    assert resp.status_code == 202
    assert resp.json()["counts"] == {"accounts": 2, "services": 6, "transactions": len(data["transactions"])}
    page = client.get("/api/v1/customers").json()
    assert page["total"] == 2
    summary = client.get("/api/v1/analytics/summary").json()
    assert summary["timeline"][13]["date"] == "2026-10-20"


def test_snapshot_rejects_broken_references(client, sample_data):
    data = copy.deepcopy(sample_data)
    data["services"][0]["ownerId"] = 9999
    resp = client.put("/api/v1/source/snapshot", json=data, headers=ADMIN)
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]
    assert client.get("/api/v1/customers").json()["total"] == 10


def test_snapshot_rejects_invalid_body(client):
    resp = client.put("/api/v1/source/snapshot", json={"accounts": []}, headers=ADMIN)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_events_upsert(client, sample_data):
    before = client.get("/api/v1/customers/3").json()
    assert before["risk_level"] == "unknown"
    service = {
        "id": 500,
        "name": "service-0500",
        "ownerId": 3,
        "paymentTerm": "month",
        "autoProlong": False,
        "createdAt": "2026-10-01T00:00:00.000Z",
        "expiresAt": "2026-10-25T00:00:00.000Z",
        "status": "active",
        "price": 1000,
    }
    tx = {
        "id": 5000,
        "amount": 100,
        "status": "not_performed",
        "performedAt": None,
        "createdAt": "2026-10-18T00:00:00.000Z",
        "type": "prolong",
        "serviceId": 500,
    }
    resp = client.post("/api/v1/source/events", json={"services": [service], "transactions": [tx]}, headers=ADMIN)
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 2}
    after = client.get("/api/v1/customers/3").json()
    assert after["risk_level"] != "unknown"
    assert after["risk_score"] is not None
    assert after["services_total"] == 1
    assert any(r["service_id"] == 500 for r in after["reasons"])
    assert after["transactions"][0]["id"] == 5000


def test_events_reject_unknown_owner(client):
    service = {
        "id": 501,
        "name": "x",
        "ownerId": 777,
        "paymentTerm": "month",
        "autoProlong": False,
        "createdAt": "2026-10-01T00:00:00Z",
        "expiresAt": "2026-10-25T00:00:00Z",
        "status": "active",
        "price": 1,
    }
    resp = client.post("/api/v1/source/events", json={"services": [service]}, headers=ADMIN)
    assert resp.status_code == 422


def test_simulator_start_stop(client):
    state = client.get("/api/v1/simulator").json()
    assert state == {"running": False, "interval_ms": 2000, "events_emitted": 0}
    state = client.put("/api/v1/simulator", json={"running": True, "interval_ms": 200}, headers=ADMIN).json()
    assert state["running"] is True and state["interval_ms"] == 200
    state = client.put("/api/v1/simulator", json={"running": False}, headers=ADMIN).json()
    assert state["running"] is False
    resp = client.put("/api/v1/simulator", json={"interval_ms": 10}, headers=ADMIN)
    assert resp.status_code == 422


def test_simulator_tick_mutates_and_publishes(client, recorder):
    core = client.app.state.core
    for _ in range(20):
        client.portal.call(core.simulator_tick)
    assert client.get("/api/v1/simulator").json()["events_emitted"] == 20
    types = [e["type"] for e in recorder.events]
    assert types.count("source.changed") == 20
    assert types.count("scores.updated") == 20
    for env in recorder.events:
        if env["type"] == "source.changed":
            assert env["payload"]["kind"] == "changes"
            assert len(env["payload"]["account_ids"]) == 1


def test_simulator_running_loop(client):
    client.put("/api/v1/simulator", json={"running": True, "interval_ms": 200}, headers=ADMIN)
    client.portal.call(asyncio.sleep, 0.7)
    client.put("/api/v1/simulator", json={"running": False}, headers=ADMIN)
    assert client.get("/api/v1/simulator").json()["events_emitted"] >= 2


def test_simulator_generate(client):
    resp = client.put("/api/v1/simulator", json={"generate": {"accounts": 300, "seed": 7}}, headers=ADMIN)
    assert resp.status_code == 200
    page = client.get("/api/v1/customers", params={"page_size": 200}).json()
    assert page["total"] == 300
    by_level = client.get("/api/v1/analytics/summary").json()["by_level"]
    assert sum(by_level.values()) == 300
    assert all(by_level[level] > 0 for level in ("high", "medium", "low"))


def test_ops_endpoints(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz")
    assert ready.status_code == 200 and ready.json()["status"] == "ready"
    docs = client.get("/api/v1/docs")
    assert docs.status_code == 200 and "swagger-ui" in docs.text and "/api/v1/openapi.yaml" in docs.text
    spec = client.get("/api/v1/openapi.yaml")
    assert spec.status_code == 200 and spec.text.startswith("openapi: 3.1.0")
    missing = client.get("/api/v1/nothing")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
