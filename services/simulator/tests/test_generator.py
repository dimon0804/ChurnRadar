from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from app import generate as cli
from app.generator import MONTHLY_TARIFFS, TERM_MULTIPLIER, generate, monthly_of, price_for
from app.timeutil import add_terms, boundary_after, parse_iso

REF = datetime(2026, 10, 19, 15, tzinfo=UTC)


@pytest.fixture(scope="module")
def data() -> dict:
    return generate(2000, 42, REF)


def test_deterministic_for_same_seed() -> None:
    assert generate(300, 7, REF) == generate(300, 7, REF)


def test_different_seed_gives_different_data() -> None:
    assert generate(300, 7, REF) != generate(300, 8, REF)


def test_reference_time_is_written(data) -> None:  # type: ignore[no-untyped-def]
    assert data["reference_time"] == "2026-10-19T15:00:00.000Z"


def test_matches_source_snapshot_schema(data, openapi_validator) -> None:  # type: ignore[no-untyped-def]
    errors = list(openapi_validator("SourceSnapshot").iter_errors(data))
    assert errors == [], errors[:3]


def test_referential_integrity_and_uniqueness(data) -> None:  # type: ignore[no-untyped-def]
    account_ids = [a["id"] for a in data["accounts"]]
    service_ids = [s["id"] for s in data["services"]]
    tx_ids = [t["id"] for t in data["transactions"]]
    assert len(set(account_ids)) == len(account_ids) == 2000
    assert len(set(service_ids)) == len(service_ids)
    assert len(set(tx_ids)) == len(tx_ids)
    assert len({a["email"] for a in data["accounts"]}) == len(account_ids)
    assert {s["ownerId"] for s in data["services"]} <= set(account_ids)
    assert {t["serviceId"] for t in data["transactions"]} <= set(service_ids)


def test_service_dates_consistent_with_status(data) -> None:  # type: ignore[no-untyped-def]
    for s in data["services"]:
        created, expires = parse_iso(s["createdAt"]), parse_iso(s["expiresAt"])
        assert created <= expires
        if s["status"] in ("active", "suspended"):
            assert expires > REF, s
        elif s["status"] == "prolong_wait":
            assert REF - timedelta(days=5) <= expires <= REF, s
        elif s["status"] == "deleted":
            assert expires + timedelta(days=5) <= REF, s


def test_price_consistent_with_term(data) -> None:  # type: ignore[no-untyped-def]
    for s in data["services"]:
        assert s["price"] == price_for(_nearest_tariff(s), s["paymentTerm"]), s


def _nearest_tariff(service: dict) -> int:
    monthly = monthly_of(service["price"], service["paymentTerm"])
    return min(MONTHLY_TARIFFS, key=lambda t: abs(t - monthly))


def test_transactions_consistent(data) -> None:  # type: ignore[no-untyped-def]
    services = {s["id"]: s for s in data["services"]}
    orders = Counter()
    for t in data["transactions"]:
        created = parse_iso(t["createdAt"])
        assert created <= REF
        assert created >= parse_iso(services[t["serviceId"]]["createdAt"])
        assert t["amount"] == services[t["serviceId"]]["price"]
        if t["status"] == "performed":
            assert t["performedAt"] is not None and parse_iso(t["performedAt"]) >= created
        else:
            assert t["performedAt"] is None
        if t["type"] == "order":
            orders[t["serviceId"]] += 1
    assert set(orders) == set(services)
    assert set(orders.values()) == {1}


def test_balances_consistent(data) -> None:  # type: ignore[no-untyped-def]
    owner = {s["id"]: s["ownerId"] for s in data["services"]}
    paid = Counter()
    for t in data["transactions"]:
        if t["status"] == "performed":
            paid[owner[t["serviceId"]]] += t["amount"]
    for a in data["accounts"]:
        assert 0 <= a["withdrawBalance"] <= a["balance"]
        assert a["totalReplenished"] >= a["balance"] + paid[a["id"]]


def test_segments_give_realistic_mix(data) -> None:  # type: ignore[no-untyped-def]
    statuses = Counter(s["status"] for s in data["services"])
    terms = Counter(s["paymentTerm"] for s in data["services"])
    assert set(statuses) == {"active", "suspended", "prolong_wait", "deleted", "blocked"}
    assert set(terms) == set(TERM_MULTIPLIER)
    assert statuses["active"] > statuses["deleted"] > statuses["blocked"]
    no_services = {a["id"] for a in data["accounts"]} - {s["ownerId"] for s in data["services"]}
    assert 0 < len(no_services) < 0.1 * len(data["accounts"])
    failed = sum(1 for t in data["transactions"] if t["status"] == "not_performed")
    assert 0 < failed < 0.2 * len(data["transactions"])


@pytest.mark.parametrize("term", list(TERM_MULTIPLIER))
def test_boundary_after(term: str) -> None:
    created = datetime(2025, 1, 31, 10, tzinfo=UTC)
    boundary, n = boundary_after(created, term, REF)
    assert boundary > REF
    assert add_terms(created, term, n) == boundary
    assert add_terms(created, term, n - 1) <= REF


def test_invalid_count() -> None:
    with pytest.raises(ValueError):
        generate(0, 1, REF)


def test_cli_writes_snapshot(tmp_path, openapi_validator) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "x.json"
    assert cli.main(["--accounts", "25", "--seed", "3", "--reference", "2026-10-19T15:00:00Z", "--out", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc == generate(25, 3, REF)
    assert not list(openapi_validator("SourceSnapshot").iter_errors(doc))


def test_cli_default_reference_is_now(tmp_path) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "now.json"
    cli.main(["--accounts", "2", "--out", str(out)])
    reference = parse_iso(json.loads(out.read_text(encoding="utf-8"))["reference_time"])
    assert abs((datetime.now(UTC) - reference).total_seconds()) < 60
