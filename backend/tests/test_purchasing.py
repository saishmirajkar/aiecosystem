"""Procurement foundation: requisitions, PO approval, GRN partial/over receipt, stock changes only on GRN confirm."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MD


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


@pytest.fixture
def ctx(client: TestClient, admin_headers: dict, md: MD):
    wh = md.warehouse("WH-A", "Main Store")
    sup = md.supplier("SUP-1", "Bharat Steel Traders")
    steel = md.product("STL-1", "Steel plate 6mm")
    bolts = md.product("BLT-1", "Hex bolt M10", uom="PCS")
    return {"h": admin_headers, "wh": wh, "sup": sup, "steel": steel, "bolts": bolts}


def make_po(client, ctx, approve=True, qty_steel="500", qty_bolts="1000", headers=None):
    h = headers or ctx["h"]
    r = client.post("/api/v1/purchase-orders", headers=h, json={
        "supplier_id": ctx["sup"]["id"], "delivery_warehouse_id": ctx["wh"]["id"], "expected_delivery_date": "2026-12-01",
        "items": [{"product_id": ctx["steel"]["id"], "quantity_ordered": qty_steel, "unit_price": "82.5", "tax_rate": "18"},
                  {"product_id": ctx["bolts"]["id"], "quantity_ordered": qty_bolts, "unit_price": "4"}]})
    assert r.status_code == 201, r.text
    po = r.json()
    if approve:
        assert client.post(f"/api/v1/purchase-orders/{po['id']}/submit", headers=ctx["h"]).json()["status"] == "pending_approval"
        po = client.post(f"/api/v1/purchase-orders/{po['id']}/approve", headers=ctx["h"]).json()
        assert po["status"] == "approved"
    return po


def phys(client, h, product):
    return Decimal(client.get(f"/api/v1/products/{product['id']}", headers=h).json()["stock"]["physical"])


def test_po_totals_and_no_stock_change(client, ctx):
    po = make_po(client, ctx)
    assert po["subtotal"] == "45250.0000" and po["tax_amount"] == "7425.0000" and po["total_amount"] == "52675.0000"
    assert phys(client, ctx["h"], ctx["steel"]) == 0  # an approved PO never changes stock


def test_po_approval_permissions(client, ctx, make_user):
    staff = make_user("inventory_staff")
    r = client.post("/api/v1/purchase-orders", headers=staff, json={
        "supplier_id": ctx["sup"]["id"], "delivery_warehouse_id": ctx["wh"]["id"],
        "items": [{"product_id": ctx["steel"]["id"], "quantity_ordered": "1", "unit_price": "1"}]})
    assert r.status_code == 403
    po = make_po(client, ctx, approve=False)
    client.post(f"/api/v1/purchase-orders/{po['id']}/submit", headers=ctx["h"])
    wh_mgr = make_user("warehouse_manager")
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/approve", headers=wh_mgr).status_code == 403
    buyer = make_user("purchase_manager")
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/approve", headers=buyer).json()["status"] == "approved"


def test_cannot_receive_unapproved_po(client, ctx):
    po = make_po(client, ctx, approve=False)
    r = client.post("/api/v1/goods-receipts", headers=ctx["h"], json={
        "purchase_order_id": po["id"], "items": [{"purchase_order_item_id": po["items"][0]["id"], "quantity_received": "1"}]})
    assert r.status_code == 409


def test_partial_receipts_update_stock_and_po_status(client, ctx):
    h, po = ctx["h"], make_po(client, ctx)
    steel_line, bolt_line = po["items"]
    r = client.post("/api/v1/goods-receipts", headers=h, json={
        "purchase_order_id": po["id"], "delivery_note_number": "DN-4411",
        "items": [{"purchase_order_item_id": steel_line["id"], "quantity_received": "300", "quantity_damaged": "10",
                   "quantity_rejected": "5", "rejection_reason": "Wrong thickness"}]})
    assert r.status_code == 201, r.text
    grn = r.json()
    assert grn["status"] == "draft" and phys(client, h, ctx["steel"]) == 0  # draft GRN does not move stock
    r = client.post(f"/api/v1/goods-receipts/{grn['id']}/confirm", headers={**h, **key()})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed" and r.json()["transaction_id"]
    stock = client.get(f"/api/v1/products/{ctx['steel']['id']}", headers=h).json()["stock"]
    assert Decimal(stock["physical"]) == 310 and Decimal(stock["available"]) == 300 and Decimal(stock["damaged"]) == 10
    assert Decimal(stock["average_cost"]) == Decimal("82.5")  # PO price, not selling price
    po = client.get(f"/api/v1/purchase-orders/{po['id']}", headers=h).json()
    assert po["status"] == "partially_received"
    assert po["items"][0]["quantity_received"] == "310.0000" and po["items"][0]["quantity_outstanding"] == "190.0000"
    assert po["items"][0]["quantity_rejected"] == "5.0000"
    # second, completing delivery
    g2 = client.post("/api/v1/goods-receipts", headers=h, json={"purchase_order_id": po["id"], "items": [
        {"purchase_order_item_id": steel_line["id"], "quantity_received": "190"},
        {"purchase_order_item_id": bolt_line["id"], "quantity_received": "1000"}]}).json()
    client.post(f"/api/v1/goods-receipts/{g2['id']}/confirm", headers={**h, **key()})
    po = client.get(f"/api/v1/purchase-orders/{po['id']}", headers=h).json()
    assert po["status"] == "fully_received"
    assert phys(client, h, ctx["steel"]) == 500 and phys(client, h, ctx["bolts"]) == 1000
    suppliers = client.get(f"/api/v1/products/{ctx['steel']['id']}/suppliers", headers=h).json()
    assert suppliers[0]["last_purchase_price"].startswith("82.5")


def test_over_receipt_requires_permission_and_reason(client, ctx, make_user):
    h, po = ctx["h"], make_po(client, ctx, qty_steel="100")
    staff = make_user("inventory_staff")
    g = client.post("/api/v1/goods-receipts", headers=staff, json={"purchase_order_id": po["id"], "items": [
        {"purchase_order_item_id": po["items"][0]["id"], "quantity_received": "120"}]}).json()
    r = client.post(f"/api/v1/goods-receipts/{g['id']}/confirm", headers={**staff, **key()})
    assert r.status_code == 409 and r.json()["error"]["code"] == "over_receipt"
    r = client.post(f"/api/v1/goods-receipts/{g['id']}/confirm", headers={**h, **key()})
    assert r.status_code == 422  # authorized user must still give a reason
    r = client.post(f"/api/v1/goods-receipts/{g['id']}/confirm", headers={**h, **key()},
                    json={"over_receipt_reason": "Supplier shipped full bundle; accepted by plant head"})
    assert r.status_code == 200 and r.json()["over_receipt_reason"]
    assert phys(client, h, ctx["steel"]) == 120


def test_grn_confirm_is_idempotent(client, ctx):
    h, po = ctx["h"], make_po(client, ctx)
    g = client.post("/api/v1/goods-receipts", headers=h, json={"purchase_order_id": po["id"], "items": [
        {"purchase_order_item_id": po["items"][0]["id"], "quantity_received": "50"}]}).json()
    k = key()
    r1 = client.post(f"/api/v1/goods-receipts/{g['id']}/confirm", headers={**h, **k})
    r2 = client.post(f"/api/v1/goods-receipts/{g['id']}/confirm", headers={**h, **k})
    assert r1.status_code == r2.status_code == 200 and r2.headers.get("Idempotent-Replay") == "true"
    assert phys(client, h, ctx["steel"]) == 50
    r3 = client.post(f"/api/v1/goods-receipts/{g['id']}/confirm", headers={**h, **key()})
    assert r3.status_code == 409  # already confirmed


def test_requisition_flow_and_conversion(client, ctx, make_user):
    staff = make_user("inventory_staff")
    r = client.post("/api/v1/purchase-requisitions", headers=staff, json={
        "justification": "Below reorder level", "submit": True,
        "items": [{"product_id": ctx["steel"]["id"], "quantity": "500", "preferred_supplier_id": ctx["sup"]["id"]}]})
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "pending_approval" and req["estimated_total"] == "40000.0000"
    assert client.post(f"/api/v1/purchase-requisitions/{req['id']}/approve", headers=staff).status_code == 403
    assert client.post(f"/api/v1/purchase-requisitions/{req['id']}/approve", headers=ctx["h"]).json()["status"] == "approved"
    r = client.post(f"/api/v1/purchase-requisitions/{req['id']}/convert", headers=ctx["h"], json={
        "supplier_id": ctx["sup"]["id"], "delivery_warehouse_id": ctx["wh"]["id"], "unit_prices": {ctx["steel"]["id"]: "81"}})
    assert r.status_code == 201
    po = r.json()
    assert po["status"] == "draft" and po["requisition_numbers"] == [req["requisition_number"]]
    assert po["items"][0]["unit_price"] == "81.000000"
    assert client.get(f"/api/v1/purchase-requisitions/{req['id']}", headers=ctx["h"]).json()["status"] == "converted"


def test_cancel_rules(client, ctx):
    h, po = ctx["h"], make_po(client, ctx)
    g = client.post("/api/v1/goods-receipts", headers=h, json={"purchase_order_id": po["id"], "items": [
        {"purchase_order_item_id": po["items"][0]["id"], "quantity_received": "5"}]}).json()
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/cancel", headers=h, json={"reason": "No longer needed"}).status_code == 409
    client.post(f"/api/v1/goods-receipts/{g['id']}/confirm", headers={**h, **key()})
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/cancel", headers=h, json={"reason": "No longer needed"}).status_code == 409
    r = client.post(f"/api/v1/purchase-orders/{po['id']}/close", headers=h, json={"reason": "Short-closed by buyer"})
    assert r.json()["status"] == "closed"


def test_batch_required_on_grn_for_batch_products(client, ctx, md: MD):
    h = ctx["h"]
    oil = md.product("OIL-1", "Hydraulic oil", uom="L", track_batches=True)
    po = client.post("/api/v1/purchase-orders", headers=h, json={
        "supplier_id": ctx["sup"]["id"], "delivery_warehouse_id": ctx["wh"]["id"],
        "items": [{"product_id": oil["id"], "quantity_ordered": "200", "unit_price": "150"}]}).json()
    client.post(f"/api/v1/purchase-orders/{po['id']}/submit", headers=h)
    client.post(f"/api/v1/purchase-orders/{po['id']}/approve", headers=h)
    r = client.post("/api/v1/goods-receipts", headers=h, json={"purchase_order_id": po["id"], "items": [
        {"purchase_order_item_id": po["items"][0]["id"], "quantity_received": "200"}]})
    assert r.status_code == 422
    r = client.post("/api/v1/goods-receipts", headers=h, json={"purchase_order_id": po["id"], "items": [
        {"purchase_order_item_id": po["items"][0]["id"], "quantity_received": "200", "batch": {"batch_number": "HO-2611"}}]})
    g = r.json()
    assert client.post(f"/api/v1/goods-receipts/{g['id']}/confirm", headers={**h, **key()}).status_code == 200
    batches = client.get("/api/v1/inventory/batches", headers=h, params={"product_id": oil["id"]}).json()["items"]
    assert batches[0]["batch_number"] == "HO-2611" and batches[0]["quantity"] == "200.0000"


def test_overdue_po_alert(client, ctx):
    h = ctx["h"]
    r = client.post("/api/v1/purchase-orders", headers=h, json={
        "supplier_id": ctx["sup"]["id"], "delivery_warehouse_id": ctx["wh"]["id"], "order_date": "2026-01-01",
        "expected_delivery_date": "2026-01-10", "items": [{"product_id": ctx["steel"]["id"], "quantity_ordered": "5", "unit_price": "80"}]})
    po = r.json()
    client.post(f"/api/v1/purchase-orders/{po['id']}/submit", headers=h)
    client.post(f"/api/v1/purchase-orders/{po['id']}/approve", headers=h)
    assert client.get("/api/v1/purchase-orders", headers=h, params={"overdue": True}).json()["total"] == 1
    alerts = client.get("/api/v1/inventory/alerts", headers=h, params={"types": "pending_receipt"}).json()
    assert alerts["total"] == 1 and alerts["items"][0]["data"]["po_number"] == po["po_number"]
