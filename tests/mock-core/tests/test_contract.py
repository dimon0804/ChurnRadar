"""Ответы сервиса проверяются схемами из contracts/openapi.yaml."""

import copy

import pytest

from conftest import ADMIN, ANALYST


def test_customer_page_matches_contract(client, contract):
    for params in ({}, {"level": "unknown"}, {"sort": "email", "order": "asc", "page_size": 3}):
        body = client.get("/api/v1/customers", params=params).json()
        contract.response("/api/v1/customers", "get", "200", body)


@pytest.mark.parametrize("customer_id", range(1, 11))
def test_customer_detail_matches_contract(client, contract, customer_id):
    body = client.get(f"/api/v1/customers/{customer_id}").json()
    contract.response("/api/v1/customers/{id}", "get", "200", body)


def test_summary_matches_contract(client, contract):
    contract.response("/api/v1/analytics/summary", "get", "200", client.get("/api/v1/analytics/summary").json())


def test_settings_endpoints_match_contract(client, contract):
    contract.response("/api/v1/model/settings", "get", "200", client.get("/api/v1/model/settings").json())
    patched = client.patch("/api/v1/model/settings", json={"horizon_days": 20}, headers=ADMIN).json()
    contract.response("/api/v1/model/settings", "patch", "200", patched)
    reset = client.post("/api/v1/model/settings/reset", headers=ADMIN).json()
    contract.response("/api/v1/model/settings/reset", "post", "200", reset)
    history = client.get("/api/v1/model/settings/history").json()
    contract.response("/api/v1/model/settings/history", "get", "200", history)


def test_source_endpoints_match_contract(client, contract, sample_data):
    resp = client.put("/api/v1/source/snapshot", json=sample_data, headers=ADMIN)
    assert resp.status_code == 202
    contract.response("/api/v1/source/snapshot", "put", "202", resp.json())

    account = copy.deepcopy(sample_data["accounts"][0])
    account["balance"] = 10
    resp = client.post("/api/v1/source/events", json={"accounts": [account]}, headers=ADMIN)
    assert resp.status_code == 202
    contract.response("/api/v1/source/events", "post", "202", resp.json())


def test_simulator_matches_contract(client, contract):
    contract.response("/api/v1/simulator", "get", "200", client.get("/api/v1/simulator").json())
    body = client.put("/api/v1/simulator", json={"interval_ms": 1000}, headers=ADMIN).json()
    contract.response("/api/v1/simulator", "put", "200", body)


def test_errors_match_contract(client, contract):
    contract.response("/api/v1/customers/{id}", "get", "404", client.get("/api/v1/customers/12345").json())
    contract.response(
        "/api/v1/customers", "get", "422", client.get("/api/v1/customers", params={"page_size": 500}).json()
    )
    forbidden = client.patch("/api/v1/model/settings", json={"horizon_days": 5}, headers=ANALYST).json()
    contract.response("/api/v1/model/settings", "patch", "403", forbidden)
    invalid = client.patch("/api/v1/model/settings", json={"horizon_days": 0}, headers=ADMIN).json()
    contract.response("/api/v1/model/settings", "patch", "422", invalid)
    contract.response("/metrics", "get", "401", client.get("/metrics").json())


def test_events_match_contract(client, contract, recorder, sample_data):
    client.patch("/api/v1/model/settings", json={"horizon_days": 21}, headers=ADMIN)
    client.put("/api/v1/source/snapshot", json=sample_data, headers=ADMIN)
    service = copy.deepcopy(sample_data["services"][0])
    service["autoProlong"] = True
    client.post("/api/v1/source/events", json={"services": [service]}, headers=ADMIN)

    assert [e["type"] for e in recorder.events] == [
        "settings.changed",
        "scores.updated",
        "source.changed",
        "scores.updated",
        "source.changed",
        "scores.updated",
    ]
    for env in recorder.events:
        contract.event("envelope", env)
        contract.event(env["type"], env["payload"])
        if env["type"] == "scores.updated":
            contract.schema("ScoresUpdatedEvent", env["payload"])
        elif env["type"] == "settings.changed":
            contract.schema("SettingsUpdatedEvent", env["payload"])
        else:
            contract.schema("SourceUpdatedEvent", env["payload"])
    incremental = recorder.events[-1]["payload"]
    assert incremental["full"] is False
    assert incremental["changed_customer_ids"] == [1]
    assert recorder.events[-2]["payload"]["account_ids"] == [1]
