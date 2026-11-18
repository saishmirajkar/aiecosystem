"""Inventory business scenarios: ledger, receipts, issues, transfers, adjustments, valuation, concurrency, idempotency."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import MD


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def opening(client, h, product, loc, qty, cost):
    r = client.post("/api/v1/inventory/opening-balances", headers={**h, **key()},
                    json={"lines": [{"product_id": product["id"], "location_id": loc, "quantity": str(qty), "unit_cost": str(cost)}]})
    assert r.status_code == 201, r.text
    return r.json()


def stock(client, h, product):
    return client.get(f"/api/v1/products/{product['id']}", headers=h).json()["stock"]


@pytest.fixture
def setup(client: TestClient, admin_headers: dict, md: MD):
    a, b = md.warehouse("WH-A", "Main Plant Store"), md.warehouse("WH-B", "Assembly Store")
    steel = md.product("STL-RB-20", "Steel round bar 20mm")
    return {"a": a, "b": b, "steel": steel, "h": admin_headers}


def test_opening_and_stock_in_update_balance_and_valuation(client, setup):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    opening(client, h, steel, a["main"], 1000, 80)
    r = client.post("/api/v1/inventory/receipts", headers={**h, **key()}, json={
        "reason": "Return from production", "lines": [{"product_id": steel["id"], "location_id": a["main"], "quantity": "500", "unit_cost": "86"}]})
    assert r.status_code == 201
    s = stock(client, h, steel)
    assert Decimal(s["physical"]) == 1500 and Decimal(s["available"]) == 1500
    assert Decimal(s["average_cost"]) == Decimal("82")  # (1000*80 + 500*86) / 1500
    assert Decimal(s["value"]) == Decimal("123000")


def test_issue_reduces_stock_and_values_at_average(client, setup):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    opening(client, h, steel, a["main"], 100, 80)
    r = client.post("/api/v1/inventory/issues", headers={**h, **key()}, json={
        "reason": "Issued to machining", "reference_number": "WO-17", "lines": [{"product_id": steel["id"], "warehouse_id": a["id"], "quantity": "40"}]})
    assert r.status_code == 201, r.text
    mv = r.json()["movements"][0]
    assert mv["quantity"] == "-40.0000" and mv["value"] == "-3200.0000" and mv["balance_after"] == "60.0000"
    assert Decimal(stock(client, h, steel)["physical"]) == 60


def test_negative_stock_prevented_by_default(client, setup):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    opening(client, h, steel, a["main"], 10, 80)
    r = client.post("/api/v1/inventory/issues", headers={**h, **key()}, json={
        "reason": "Too much", "lines": [{"product_id": steel["id"], "location_id": a["main"], "quantity": "11"}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "insufficient_stock"
    assert "on hand 10, requested 11" in r.json()["error"]["message"]  # no scientific notation (e.g. '1E+1')
    assert Decimal(stock(client, h, steel)["physical"]) == 10


def test_negative_stock_policy_requires_setting_and_permission(client, setup, make_user):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    client.patch("/api/v1/settings", headers=h, json={"values": {"inventory.allow_negative_stock": True}})
    manager = make_user("inventory_manager")  # lacks inventory.negative_stock
    body = {"reason": "Backflush", "lines": [{"product_id": steel["id"], "location_id": a["main"], "quantity": "5"}]}
    assert client.post("/api/v1/inventory/issues", headers={**manager, **key()}, json=body).status_code == 409
    r = client.post("/api/v1/inventory/issues", headers={**h, **key()}, json=body)  # super admin holds it
    assert r.status_code == 201 and r.json()["movements"][0]["balance_after"] == "-5.0000"


def test_idempotent_retry_does_not_duplicate(client, setup):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    opening(client, h, steel, a["main"], 100, 80)
    k = key()
    body = {"reason": "Issue", "lines": [{"product_id": steel["id"], "location_id": a["main"], "quantity": "10"}]}
    r1 = client.post("/api/v1/inventory/issues", headers={**h, **k}, json=body)
    r2 = client.post("/api/v1/inventory/issues", headers={**h, **k}, json=body)
    assert r1.status_code == r2.status_code == 201
    assert r2.headers.get("Idempotent-Replay") == "true" and r1.json()["id"] == r2.json()["id"]
    assert Decimal(stock(client, h, steel)["physical"]) == 90
    r3 = client.post("/api/v1/inventory/issues", headers={**h, **k}, json={**body, "reason": "Different"})
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "idempotency_key_reused"
    r4 = client.post("/api/v1/inventory/issues", headers=h, json=body)
    assert r4.status_code == 400 and r4.json()["error"]["code"] == "idempotency_key_required"


def test_failed_posting_can_be_retried_with_same_key(client, setup):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    k = key()
    body = {"reason": "Issue", "lines": [{"product_id": steel["id"], "location_id": a["main"], "quantity": "10"}]}
    assert client.post("/api/v1/inventory/issues", headers={**h, **k}, json=body).status_code == 409
    opening(client, h, steel, a["main"], 50, 80)
    assert client.post("/api/v1/inventory/issues", headers={**h, **k}, json=body).status_code == 201


def test_concurrent_issues_never_oversell(client, setup):
    """20 threads each try to issue 10 from a stock of 100: exactly 10 succeed and stock ends at 0."""
    from app.core.database import SessionLocal
    from app.core.deps import build_actor
    from app.core.errors import InsufficientStock
    from app.models import User
    from app.modules.inventory import schemas as s
    from app.modules.inventory.service import post_issue
    from sqlalchemy import select

    h, steel, a = setup["h"], setup["steel"], setup["a"]
    opening(client, h, steel, a["main"], 100, 80)

    def worker(_: int) -> str:
        with SessionLocal() as db:
            user = db.execute(select(User)).scalars().first()
            actor = build_actor(db, user)
            try:
                post_issue(db, actor, s.IssueIn(reason="Concurrent issue", lines=[
                    s.IssueLine(product_id=steel["id"], location_id=a["main"], quantity=Decimal("10"))]))
                db.commit()
                return "ok"
            except InsufficientStock:
                db.rollback()
                return "insufficient"

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(worker, range(20)))
    assert results.count("ok") == 10 and results.count("insufficient") == 10
    s_ = stock(client, h, steel)
    assert Decimal(s_["physical"]) == 0
    movements = client.get("/api/v1/inventory/movements", headers=h, params={"product_id": steel["id"], "page_size": 200}).json()
    assert movements["total"] == 11
    assert sorted(Decimal(m["balance_after"]) for m in movements["items"] if m["movement_type"] == "issue") == \
        [Decimal(x) for x in range(0, 100, 10)]


def test_ledger_is_append_only(client, setup, db):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    opening(client, h, steel, a["main"], 10, 80)
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("UPDATE stock_movements SET quantity = 999"))
    db.rollback()
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("DELETE FROM stock_movements"))
    db.rollback()


def test_transfer_lifecycle_with_in_transit(client, setup):
    h, steel, a, b = setup["h"], setup["steel"], setup["a"], setup["b"]
    opening(client, h, steel, a["main"], 300, 80)
    r = client.post("/api/v1/inventory/transfers", headers=h, json={
        "source_warehouse_id": a["id"], "destination_warehouse_id": b["id"], "items": [{"product_id": steel["id"], "quantity": "120"}]})
    assert r.status_code == 201
    t = r.json()
    assert t["status"] == "draft" and Decimal(stock(client, h, steel)["physical"]) == 300
    assert client.post(f"/api/v1/inventory/transfers/{t['id']}/dispatch", headers={**h, **key()}).status_code == 409
    assert client.post(f"/api/v1/inventory/transfers/{t['id']}/submit", headers=h).json()["status"] == "pending_approval"
    assert client.post(f"/api/v1/inventory/transfers/{t['id']}/approve", headers=h).json()["status"] == "approved"
    r = client.post(f"/api/v1/inventory/transfers/{t['id']}/dispatch", headers={**h, **key()})
    assert r.status_code == 200 and r.json()["status"] == "in_transit"
    s_ = stock(client, h, steel)
    assert Decimal(s_["physical"]) == 180 and Decimal(s_["in_transit"]) == 120 and Decimal(s_["available"]) == 180
    dest = client.get("/api/v1/inventory/balances", headers=h, params={"warehouse_id": b["id"]}).json()["items"]
    assert [(x["stock_status"], x["qty_available"]) for x in dest] == [("in_transit", "0")]
    r = client.post(f"/api/v1/inventory/transfers/{t['id']}/receive", headers={**h, **key()})
    assert r.json()["status"] == "completed"
    s_ = stock(client, h, steel)
    assert Decimal(s_["physical"]) == 300 and Decimal(s_["in_transit"]) == 0
    assert Decimal(s_["average_cost"]) == 80  # transfers never change valuation
    assert client.post(f"/api/v1/inventory/transfers/{t['id']}/cancel", headers=h, json={"reason": "late"}).status_code == 409


def test_transfer_approval_requires_permission(client, setup, make_user):
    h, steel, a, b = setup["h"], setup["steel"], setup["a"], setup["b"]
    staff = make_user("inventory_staff")
    t = client.post("/api/v1/inventory/transfers", headers=staff, json={
        "source_warehouse_id": a["id"], "destination_warehouse_id": b["id"], "items": [{"product_id": steel["id"], "quantity": "1"}]}).json()
    client.post(f"/api/v1/inventory/transfers/{t['id']}/submit", headers=staff)
    r = client.post(f"/api/v1/inventory/transfers/{t['id']}/approve", headers=staff)
    assert r.status_code == 403


def test_adjustment_requires_approval_before_posting(client, setup, make_user):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    opening(client, h, steel, a["main"], 100, 80)
    staff = make_user("inventory_staff")
    r = client.post("/api/v1/inventory/adjustments", headers=staff, json={
        "warehouse_id": a["id"], "reason": "Scrap found", "submit": True,
        "items": [{"product_id": steel["id"], "location_id": a["main"], "quantity_delta": "-7"}]})
    assert r.status_code == 201, r.text
    adj = r.json()
    assert adj["status"] == "pending_approval" and adj["estimated_value"] == "-560.0000"
    assert Decimal(stock(client, h, steel)["physical"]) == 100  # nothing moved yet
    assert client.post(f"/api/v1/inventory/adjustments/{adj['id']}/approve", headers=staff).status_code == 403
    r = client.post(f"/api/v1/inventory/adjustments/{adj['id']}/approve", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "posted" and r.json()["transaction_id"]
    assert Decimal(stock(client, h, steel)["physical"]) == 93


def test_reconciliation_computes_variance(client, setup):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    opening(client, h, steel, a["main"], 100, 80)
    r = client.post("/api/v1/inventory/adjustments", headers=h, json={
        "adjustment_type": "reconciliation", "warehouse_id": a["id"], "reason": "Monthly cycle count", "submit": True,
        "items": [{"product_id": steel["id"], "location_id": a["main"], "counted_quantity": "96.5"}]})
    adj = r.json()
    assert adj["items"][0]["system_quantity"] == "100.0000" and adj["items"][0]["quantity_delta"] == "-3.5000"
    alerts = client.get("/api/v1/inventory/alerts", headers=h, params={"types": "stock_discrepancy"}).json()
    assert alerts["total"] == 1
    client.post(f"/api/v1/inventory/adjustments/{adj['id']}/approve", headers=h)
    assert Decimal(stock(client, h, steel)["physical"]) == Decimal("96.5")
    mv = client.get("/api/v1/inventory/movements", headers=h, params={"movement_type": "reconciliation"}).json()
    assert mv["total"] == 1


def test_reservations_reduce_available(client, setup):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    opening(client, h, steel, a["main"], 100, 80)
    r = client.post("/api/v1/inventory/reservations", headers=h, json={
        "product_id": steel["id"], "warehouse_id": a["id"], "quantity": "70", "reference_number": "WO-88"})
    assert r.status_code == 201
    res = r.json()
    s_ = stock(client, h, steel)
    assert Decimal(s_["available"]) == 30 and Decimal(s_["reserved"]) == 70 and Decimal(s_["physical"]) == 100
    body = {"reason": "Other job", "lines": [{"product_id": steel["id"], "location_id": a["main"], "quantity": "40"}]}
    assert client.post("/api/v1/inventory/issues", headers={**h, **key()}, json=body).status_code == 409
    r = client.post("/api/v1/inventory/issues", headers={**h, **key()}, json={
        "reason": "WO-88 consumption", "lines": [{"product_id": steel["id"], "reservation_id": res["id"], "quantity": "70"}]})
    assert r.status_code == 201, r.text
    s_ = stock(client, h, steel)
    assert Decimal(s_["available"]) == 30 and Decimal(s_["reserved"]) == 0 and Decimal(s_["physical"]) == 30


def test_damaged_status_change_excluded_from_available(client, setup):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    opening(client, h, steel, a["main"], 50, 80)
    r = client.post("/api/v1/inventory/status-changes", headers={**h, **key()}, json={
        "product_id": steel["id"], "location_id": a["main"], "quantity": "5", "from_status": "unrestricted",
        "to_status": "damaged", "reason": "Rust found"})
    assert r.status_code == 201
    s_ = stock(client, h, steel)
    assert Decimal(s_["physical"]) == 50 and Decimal(s_["available"]) == 45 and Decimal(s_["damaged"]) == 5


def test_batch_tracking_and_fefo_allocation(client, setup, md: MD):
    h, a = setup["h"], setup["a"]
    resin = md.product("RES-1", "Epoxy resin", uom="L", track_batches=True, track_expiry=True)
    def stock_in(batch, expiry, qty):
        r = client.post("/api/v1/inventory/receipts", headers={**h, **key()}, json={"reason": "Receipt", "lines": [{
            "product_id": resin["id"], "location_id": a["main"], "quantity": str(qty), "unit_cost": "300",
            "batch": {"batch_number": batch, "expiry_date": expiry}}]})
        assert r.status_code == 201, r.text
    stock_in("B-LATE", "2031-01-01", 20)
    stock_in("B-EARLY", "2030-01-01", 20)
    missing = client.post("/api/v1/inventory/receipts", headers={**h, **key()}, json={"reason": "Receipt", "lines": [{
        "product_id": resin["id"], "location_id": a["main"], "quantity": "1"}]})
    assert missing.status_code == 422
    r = client.post("/api/v1/inventory/issues", headers={**h, **key()}, json={
        "reason": "Use", "lines": [{"product_id": resin["id"], "warehouse_id": a["id"], "quantity": "25"}]})
    assert r.status_code == 201
    picked = [(m["batch_number"], m["quantity"]) for m in r.json()["movements"]]
    assert picked == [("B-EARLY", "-20.0000"), ("B-LATE", "-5.0000")]


def test_serial_tracking_through_issue(client, setup, md: MD):
    h, a = setup["h"], setup["a"]
    motor = md.product("MTR-5", "Motor 5HP", uom="PCS", track_serials=True)
    r = client.post("/api/v1/inventory/receipts", headers={**h, **key()}, json={"reason": "Receipt", "lines": [{
        "product_id": motor["id"], "location_id": a["main"], "quantity": "2", "unit_cost": "15000", "serial_numbers": ["SN1", "SN2"]}]})
    assert r.status_code == 201, r.text
    bad = client.post("/api/v1/inventory/receipts", headers={**h, **key()}, json={"reason": "Receipt", "lines": [{
        "product_id": motor["id"], "location_id": a["main"], "quantity": "1", "unit_cost": "15000", "serial_numbers": ["SN1"]}]})
    assert bad.status_code == 422  # already in stock
    r = client.post("/api/v1/inventory/issues", headers={**h, **key()}, json={"reason": "Fit to machine", "lines": [{
        "product_id": motor["id"], "location_id": a["main"], "quantity": "1", "serial_numbers": ["SN2"]}]})
    assert r.status_code == 201, r.text
    serials = {x["serial_number"]: x["status"] for x in client.get("/api/v1/inventory/serials", headers=h).json()["items"]}
    assert serials == {"SN1": "in_stock", "SN2": "issued"}


def test_high_value_issue_requires_permission(client, setup, make_user):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    opening(client, h, steel, a["main"], 1000, 80)
    client.patch("/api/v1/settings", headers=h, json={"values": {"inventory.high_value_threshold": "10000"}})
    staff = make_user("inventory_staff")
    body = {"reason": "Big issue", "lines": [{"product_id": steel["id"], "location_id": a["main"], "quantity": "200"}]}
    r = client.post("/api/v1/inventory/issues", headers={**staff, **key()}, json=body)
    assert r.status_code == 403 and r.json()["error"]["details"]["permission"] == "inventory.high_value"
    assert client.post("/api/v1/inventory/issues", headers={**h, **key()}, json=body).status_code == 201


def test_warehouse_scoped_user_cannot_post_elsewhere(client, setup, make_user):
    h, steel, a, b = setup["h"], setup["steel"], setup["a"], setup["b"]
    opening(client, h, steel, b["main"], 10, 80)
    scoped = make_user("warehouse_manager", warehouse_ids=[a["id"]])
    r = client.post("/api/v1/inventory/issues", headers={**scoped, **key()}, json={
        "reason": "x issue", "lines": [{"product_id": steel["id"], "location_id": b["main"], "quantity": "1"}]})
    assert r.status_code == 403
    assert client.get("/api/v1/inventory/balances", headers=scoped).json()["total"] == 0


def test_low_stock_alerts_and_overview(client, setup):
    h, steel, a = setup["h"], setup["steel"], setup["a"]
    opening(client, h, steel, a["main"], 90, 80)  # reorder 100, min 50 → low
    alerts = client.get("/api/v1/inventory/alerts", headers=h).json()
    assert alerts["counts"].get("low_stock") == 1
    ov = client.get("/api/v1/inventory/overview", headers=h).json()
    assert ov["metrics"]["active_products"] == 1 and ov["metrics"]["low_stock_items"] == 1
    assert ov["metrics"]["total_stock_value"] == "7200.0000" and ov["metrics"]["warehouse_count"] == 2
    assert len(ov["trend"]) == 30 and ov["recent_movements"][0]["product_sku"] == "STL-RB-20"
    val = client.get("/api/v1/inventory/valuation", headers=h, params={"group_by": "warehouse"}).json()
    assert val["total_value"] == "7200.0000"


def test_read_only_cannot_post(client, setup, make_user):
    ro = make_user("read_only")
    r = client.post("/api/v1/inventory/issues", headers={**ro, **key()}, json={
        "reason": "x issue", "lines": [{"product_id": setup["steel"]["id"], "location_id": setup["a"]["main"], "quantity": "1"}]})
    assert r.status_code == 403
