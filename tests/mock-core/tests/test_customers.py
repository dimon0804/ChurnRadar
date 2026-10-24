import pytest

SORT_FIELDS = ["id", "email", "balance", "total_replenished", "risk_score", "expected_loss"]


def all_customers(client, **params):
    params.setdefault("page_size", 200)
    resp = client.get("/api/v1/customers", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_default_sort_is_risk_desc_with_unknown_last(client):
    items = all_customers(client)["items"]
    scores = [c["risk_score"] for c in items]
    known = [s for s in scores if s is not None]
    assert known == sorted(known, reverse=True)
    assert scores[len(known):] == [None] * (len(scores) - len(known))
    assert len(known) < len(scores)


@pytest.mark.parametrize("field", SORT_FIELDS)
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_sorting_all_fields(client, field, order):
    items = all_customers(client, sort=field, order=order)["items"]
    assert len(items) == 10
    values = [c[field] for c in items]
    present = [v for v in values if v is not None]
    if field == "email":
        present = [v.lower() for v in present]
    assert present == sorted(present, reverse=(order == "desc"))
    # пустые значения всегда в конце, в обоих направлениях
    assert values[len(present):] == [None] * (len(values) - len(present))


def test_risk_score_nulls_last_in_both_directions(client):
    for order in ("asc", "desc"):
        items = all_customers(client, sort="risk_score", order=order)["items"]
        assert [c["risk_level"] for c in items[-2:]] == ["unknown", "unknown"]
        assert items[0]["risk_score"] is not None


def test_equal_values_are_ordered_by_id(client):
    items = all_customers(client, sort="expected_loss", order="asc")["items"]
    zeros = [c["id"] for c in items if c["expected_loss"] == 0]
    assert zeros == sorted(zeros)


def test_unknown_customers(client):
    page = all_customers(client, level="unknown")
    assert page["total"] == 2
    assert sorted(c["id"] for c in page["items"]) == [3, 8]
    for c in page["items"]:
        assert c["risk_score"] is None
        assert c["loyalty"] is None
        assert c["risk_level"] == "unknown"
        assert c["services_total"] == 0
        assert c["expected_loss"] == 0 and c["exposure"] == 0
        assert len(c["reasons"]) == 1
        assert c["reasons"][0]["contribution"] == 0
        assert "удален" in c["reasons"][0]["title"]


def test_unknown_customer_detail(client):
    body = client.get("/api/v1/customers/3").json()
    assert body["risk_level"] == "unknown"
    assert all(s["status"] == "deleted" and s["risk_level"] == "unknown" for s in body["services"])
    assert body["money"]["expected_loss"] == 0
    assert body["recommendations"][0]["code"] == "winback"


def test_loyalty_is_inverse_of_score(client):
    for c in all_customers(client)["items"]:
        if c["risk_score"] is not None:
            assert c["loyalty"] == pytest.approx(100 - c["risk_score"], abs=0.11)


def test_level_filter_multiple_values(client):
    page = all_customers(client, level="high,medium")
    assert page["total"] > 0
    assert {c["risk_level"] for c in page["items"]} <= {"high", "medium"}
    everything = all_customers(client, level="high,medium,low,unknown")
    assert everything["total"] == 10


def test_level_filter_rejects_unknown_value(client):
    resp = client.get("/api/v1/customers", params={"level": "high,critical"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_search_by_email_substring_and_exact_id(client):
    assert [c["id"] for c in all_customers(client, search="USER-0010")["items"]] == [10]
    assert [c["id"] for c in all_customers(client, search="user-001")["items"]] == [10]
    assert [c["id"] for c in all_customers(client, search="7")["items"]] == [7]
    assert all_customers(client, search="nobody@nowhere")["total"] == 0


def test_paging(client):
    first = all_customers(client, sort="id", order="asc", page=1, page_size=4)
    second = all_customers(client, sort="id", order="asc", page=3, page_size=4)
    assert [c["id"] for c in first["items"]] == [1, 2, 3, 4]
    assert [c["id"] for c in second["items"]] == [9, 10]
    assert second["total"] == 10 and second["page"] == 3 and second["page_size"] == 4
    assert all_customers(client, page=5, page_size=4)["items"] == []


@pytest.mark.parametrize(
    "params",
    [{"page_size": 201}, {"page_size": 0}, {"page": 0}, {"sort": "name"}, {"order": "up"}],
)
def test_invalid_query_params(client, params):
    resp = client.get("/api/v1/customers", params=params)
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"]


def test_customer_detail(client):
    body = client.get("/api/v1/customers/7").json()
    assert body["id"] == 7
    assert len(body["services"]) == 3
    assert len(body["features"]) == 7
    assert body["transactions"] == sorted(body["transactions"], key=lambda t: t["created_at"], reverse=True)
    assert body["money"]["explanation"].startswith("Клиент приносит")
    assert body["recommendations"]
    contributions = sum(f["contribution"] for f in body["features"])
    assert contributions == pytest.approx(body["risk_score"], abs=0.5)
    assert body["reasons"] == sorted(body["reasons"], key=lambda r: r["contribution"], reverse=True)


def test_customer_not_found(client):
    resp = client.get("/api/v1/customers/999999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_summary(client):
    s = client.get("/api/v1/analytics/summary").json()
    assert s["customers_total"] == 10
    assert sum(s["by_level"].values()) == 10
    assert s["by_level"]["unknown"] == 2
    assert 0 <= s["revenue_at_risk_share"] <= 1
    assert s["expected_loss"] <= s["exposure"]
    facts = [p for p in s["timeline"] if p["kind"] == "fact"]
    forecast = [p for p in s["timeline"] if p["kind"] == "forecast"]
    assert len(facts) == 14
    assert len(forecast) == s["horizon_days"]
    assert facts[-1]["date"] == "2026-10-19"
    assert sum(p["deletions"] for p in facts) >= 1
    assert sum(p["expected_loss"] for p in forecast) == pytest.approx(s["expected_loss"], rel=0.01)
    assert s["top_reasons"]
