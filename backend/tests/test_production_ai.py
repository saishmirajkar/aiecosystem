"""Production Agent: routing, part tracking answers, vendor workload, delays, challan drafts via approval, multi-agent."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_production import Plant, key, px  # noqa: F401  (fixture)


def chat(client, h, message):
    r = client.post("/api/v1/ai/chat", headers=h, json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()["message"]


def started_order(px: Plant):
    o = px.order(qty="4")
    px.post(f"/api/v1/production/orders/{o['id']}/release", {}, status=200)
    px.complete(o, "HSG-100", "Cutting", units_completed="4")
    px.challan(o, "HSG-100", "Machining", "3")
    return px.refresh(o)


def test_where_are_parts_answer(client: TestClient, px: Plant):
    o = started_order(px)
    msg = chat(client, px.h, f"Where are all the parts of {o['order_number']}?")
    p = msg["payload"]
    assert [a["id"] for a in p["routing"]["agents"]] == ["production"]
    card = next(c for c in p["cards"] if c["title"].startswith("Where the parts are"))
    rows = {(r["part"], r["where"]): r for r in card["rows"]}
    assert rows[("Housing GB-100", "Precision Machining Works (job work)")]["units"] == "3"
    assert rows[("Housing GB-100", "Plant A")]["units"] == "1"
    assert card["provenance"] == "verified"


def test_vendor_workload_and_delays(client: TestClient, px: Plant):
    started_order(px)
    msg = chat(client, px.h, "What is Precision Machining holding?")
    rows = msg["payload"]["cards"][0]["rows"]
    assert rows[0]["vendor"] == "Precision Machining Works" and rows[0]["units"] == "3"
    msg = chat(client, px.h, "What is each vendor holding?")
    assert "Coatings & Gears Co" in msg["content"]
    msg = chat(client, px.h, "Which stages are delayed?")
    assert msg["payload"]["routing"]["agents"][0]["id"] == "production"


def test_challan_draft_requires_approval_then_moves_material(client: TestClient, px: Plant):
    o = px.order(qty="2")
    px.post(f"/api/v1/production/orders/{o['id']}/release", {}, status=200)
    msg = chat(client, px.h, f"Send 2 units of Output shaft for {o['order_number']} to Precision Machining")
    appr = msg["payload"]["approvals"][0]
    assert appr["action_type"] == "production.issue_challan" and appr["status"] == "pending"
    va = px.vendors["VA"]["warehouse_id"]
    assert px.stock_at(px.steel, va) == 0  # nothing moved yet
    r = client.post(f"/api/v1/approvals/{appr['id']}/approve", headers=px.h)
    assert r.status_code == 200 and r.json()["status"] == "executed" and r.json()["result"]["number"].startswith("JWC-")
    from decimal import Decimal
    assert px.stock_at(px.steel, va) == Decimal("2.4")


def test_combined_question_asks_both_agents(client: TestClient, px: Plant):
    msg = chat(client, px.h, "Do we have enough steel to start the gearbox order?")
    agents = [a["id"] for a in msg["payload"]["routing"]["agents"]]
    assert set(agents) == {"inventory", "production"}
    assert "**Inventory Agent**" in msg["content"] and "**Production Agent**" in msg["content"]


def test_production_agent_needs_permission(client: TestClient, px: Plant, make_user):
    r = client.post("/api/v1/roles", headers=px.h, json={"code": "ai_stock_only", "name": "AI stock only",
                                                        "permissions": ["ai.access", "inventory.view"]})
    assert r.status_code == 201
    client.post("/api/v1/users", headers=px.h, json={"email": "stockonly@demo.example.com", "full_name": "S O",
                                                    "password": "Str0ng-Passw0rd!", "role_ids": [r.json()["id"]]})
    from tests.conftest import login
    h = login(client, "stockonly@demo.example.com")
    msg = chat(client, h, "Where are all the parts of MO-000001?")
    assert all(a["id"] != "production" for a in msg["payload"]["routing"]["agents"])
