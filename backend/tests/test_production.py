"""Company X production flow: structure, orders, job work (split, direct, rework, scrap), costing, tracking, dispatch."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MD


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


class Plant:
    """Builds the Company X gearbox scenario through the public API."""

    def __init__(self, client: TestClient, h: dict, md: MD):
        self.c, self.h, self.md = client, h, md
        self.wh = md.warehouse("WH-MAIN", "Main Store")
        self.plant = self.post("/api/v1/production/sites/plants", {"code": "PLANT-A", "name": "Plant A"})
        self.plant_loc = self.default_loc(self.plant["warehouse_id"])
        self.vendors = {}
        for code, name in (("VA", "Precision Machining Works"), ("VB", "Coatings & Gears Co"), ("VC", "Sharp Tools Pvt Ltd")):
            sup = md.supplier(code, name)
            self.vendors[code] = self.post("/api/v1/production/sites/vendors", {"supplier_id": sup["id"]})
        self.blank = md.product("HSG-BLK", "Housing casting blank", uom="PCS", purchase_price="1000", standard_cost="1000")
        self.steel = md.product("STL-32", "Steel bar 32mm", uom="KG")
        self.brg = md.product("BRG-6205", "Bearing 6205", uom="PCS", purchase_price="185")
        self.housing = md.product("HSG-100", "Housing GB-100", uom="PCS", product_type="component")
        self.shaft = md.product("SFT-100", "Output shaft GB-100", uom="PCS", product_type="component")
        self.gearbox = md.product("GB-100", "Gearbox GB-100", uom="PCS", product_type="finished_good")
        va, vb = self.vendors["VA"]["warehouse_id"], self.vendors["VB"]["warehouse_id"]
        self.structure(self.housing, [(self.blank, "1")], [("Cutting", "plant", None, "40", 1), ("Machining", "vendor", va, "650", 5),
                                                           ("Coating", "vendor", vb, "180", 3)])
        self.structure(self.shaft, [(self.steel, "1.2")], [("Turning & grinding", "vendor", va, "420", 5)])
        self.structure(self.gearbox, [(self.housing, "1"), (self.shaft, "1"), (self.brg, "2")], [("Assembly & testing", "plant", None, "500", 2)])
        for p, qty, cost in ((self.blank, "50", "1000"), (self.steel, "100", "80"), (self.brg, "100", "185")):
            r = client.post("/api/v1/inventory/opening-balances", headers={**h, **key()},
                            json={"lines": [{"product_id": p["id"], "location_id": self.plant_loc, "quantity": qty, "unit_cost": cost}]})
            assert r.status_code == 201, r.text

    def post(self, path, body, headers=None, status=201):
        r = self.c.post(path, headers=headers or self.h, json=body)
        assert r.status_code == status, r.text
        return r.json()

    def default_loc(self, wid):
        locs = self.c.get(f"/api/v1/warehouses/{wid}/locations", headers=self.h).json()
        return next(l["id"] for l in locs if l["is_default"])

    def structure(self, product, bom, route):
        r = self.c.put(f"/api/v1/production/products/{product['id']}/structure", headers=self.h, json={
            "bom": [{"component_id": c["id"], "quantity": q} for c, q in bom],
            "route": [{"name": n, "performer": p, "site_id": s, "rate_per_unit": rate, "planned_days": d} for n, p, s, rate, d in route]})
        assert r.status_code == 200, r.text

    def order(self, qty="10", **extra):
        return self.post("/api/v1/production/orders", {"product_id": self.gearbox["id"], "quantity": qty,
                                                         "plant_id": self.plant["warehouse_id"], **extra})

    def part(self, order, sku):
        return next(p for p in order["parts"] if p["sku"] == sku)

    def stage(self, order, sku, name):
        return next(st for st in self.part(order, sku)["stages"] if st["name"] == name)

    def refresh(self, order):
        return self.c.get(f"/api/v1/production/orders/{order['id']}", headers=self.h).json()

    def stock_at(self, product, wid):
        rows = self.c.get("/api/v1/inventory/balances", headers=self.h, params={"product_id": product["id"], "warehouse_id": wid}).json()["items"]
        return sum(Decimal(r["qty_on_hand"]) for r in rows)

    def challan(self, order, sku, stage, units, vendor=None, status=201):
        st = self.stage(order, sku, stage)
        body = {"units": units, **({"vendor_site_id": self.vendors[vendor]["warehouse_id"]} if vendor else {})}
        r = self.c.post(f"/api/v1/production/stages/{st['id']}/challans", headers={**self.h, **key()}, json=body)
        assert r.status_code == status, r.text
        return r.json()

    def receipt(self, order, sku, stage, vendor, status=201, **body):
        st = self.stage(order, sku, stage)
        r = self.c.post(f"/api/v1/production/stages/{st['id']}/receipts", headers={**self.h, **key()},
                        json={"vendor_site_id": self.vendors[vendor]["warehouse_id"], **body})
        assert r.status_code == status, r.text
        return r.json()

    def complete(self, order, sku, stage, status=201, **body):
        st = self.stage(order, sku, stage)
        r = self.c.post(f"/api/v1/production/stages/{st['id']}/complete", headers={**self.h, **key()}, json=body)
        assert r.status_code == status, r.text
        return r.json()


@pytest.fixture
def px(client: TestClient, admin_headers: dict, md: MD) -> Plant:
    return Plant(client, admin_headers, md)


def test_order_expands_bom_into_tree_and_schedules(px: Plant):
    o = px.order(start_date="2026-12-01", due_date="2026-12-10")
    assert [(p["sku"], p["source"], p["quantity"]) for p in o["parts"]] == [
        ("GB-100", "make", "10.0000"), ("HSG-100", "make", "10.0000"), ("HSG-BLK", "buy", "10.0000"),
        ("SFT-100", "make", "10.0000"), ("STL-32", "buy", "12.0000"), ("BRG-6205", "buy", "20.0000")]
    hsg = px.part(o, "HSG-100")["stages"]
    assert [(s["name"], s["planned_start"], s["planned_end"]) for s in hsg] == [
        ("Cutting", "2026-12-01", "2026-12-01"), ("Machining", "2026-12-02", "2026-12-06"), ("Coating", "2026-12-07", "2026-12-09")]
    assembly = px.stage(o, "GB-100", "Assembly & testing")
    assert assembly["planned_start"] == "2026-12-10" and assembly["planned_end"] == "2026-12-11"
    assert o["at_risk"] is True and o["planned_finish"] == "2026-12-11"  # due 10 Dec, plan finishes 11 Dec
    assert px.part(o, "HSG-100")["conversion_cost_per_unit"] == "870.000000"


def test_material_cannot_move_before_release(px: Plant):
    o = px.order()
    px.complete(o, "HSG-100", "Cutting", units_completed="10", status=409)


def test_full_flow_split_direct_rework_costing_and_dispatch(client: TestClient, px: Plant):
    h = px.h
    o = px.order()
    px.post(f"/api/v1/production/orders/{o['id']}/release", {}, status=200)
    va, vb, vc = (px.vendors[k]["warehouse_id"] for k in ("VA", "VB", "VC"))

    # Housing: cut in-house (no stock move until the part is finished)
    px.complete(o, "HSG-100", "Cutting", units_completed="10")
    assert px.stock_at(px.blank, px.plant["warehouse_id"]) == 50
    # Machining split between Vendor A (6) and Vendor C (4)
    px.challan(o, "HSG-100", "Machining", "6")
    px.challan(o, "HSG-100", "Machining", "4", vendor="VC")
    px.challan(o, "HSG-100", "Machining", "1", status=409)  # nothing left waiting
    assert px.stock_at(px.blank, va) == 6 and px.stock_at(px.blank, vc) == 4
    o = px.refresh(o)
    where = {(p["site_name"], p["kind"]): p["units"] for p in px.part(o, "HSG-100")["positions"]}
    assert where[("Precision Machining Works (job work)", "at_vendor")] == "6.0000"
    assert where[("Sharp Tools Pvt Ltd (job work)", "at_vendor")] == "4.0000"
    # Vendor A returns 5, holds 1 for rework; Vendor C ships 4 directly to Vendor B for coating
    px.receipt(o, "HSG-100", "Machining", "VA", units_accepted="5", units_rework="1")
    px.receipt(o, "HSG-100", "Machining", "VC", units_accepted="4", direct_to_next=True)
    assert px.stock_at(px.blank, va) == 1 and px.stock_at(px.blank, vb) == 4 and px.stock_at(px.blank, px.plant["warehouse_id"]) == 45
    px.receipt(o, "HSG-100", "Machining", "VA", status=409, units_accepted="2")  # vendor holds only 1
    # Coating: send the 5 that came back to the plant; Vendor B now holds 9
    px.challan(o, "HSG-100", "Coating", "5")
    o = px.refresh(o)
    coating = px.stage(o, "HSG-100", "Coating")
    assert coating["at_vendors"][0]["units"] == "9.0000"
    # Vendor B returns 9 finished housings: blank consumed at B, housing produced at the plant
    px.receipt(o, "HSG-100", "Coating", "VB", units_accepted="9")
    assert px.stock_at(px.blank, vb) == 0 and px.stock_at(px.housing, px.plant["warehouse_id"]) == 9
    hsg_cost = client.get(f"/api/v1/products/{px.housing['id']}", headers=h).json()["stock"]["average_cost"]
    assert Decimal(hsg_cost) == Decimal("1870")  # blank 1000 + cutting 40 + machining 650 + coating 180
    # Rework unit comes back and finishes
    px.receipt(o, "HSG-100", "Machining", "VA", units_accepted="1")
    px.challan(o, "HSG-100", "Coating", "1")
    px.receipt(o, "HSG-100", "Coating", "VB", units_accepted="1")
    o = px.refresh(o)
    assert all(s["status"] == "completed" for s in px.part(o, "HSG-100")["stages"])

    # Shaft: vendor makes the whole part from our steel
    px.challan(o, "SFT-100", "Turning & grinding", "10")
    assert px.stock_at(px.steel, va) == 12
    px.receipt(o, "SFT-100", "Turning & grinding", "VA", units_accepted="10")
    assert Decimal(client.get(f"/api/v1/products/{px.shaft['id']}", headers=h).json()["stock"]["average_cost"]) == Decimal("516")

    # Final assembly at the plant consumes parts and bearings
    o = px.refresh(o)
    assert px.stage(o, "GB-100", "Assembly & testing")["waiting_at_plant"] == "10.0000"
    px.complete(o, "GB-100", "Assembly & testing", units_completed="10")
    o = px.refresh(o)
    assert o["status"] == "completed" and o["progress"] == 100.0
    gb = client.get(f"/api/v1/products/{px.gearbox['id']}", headers=h).json()["stock"]
    assert Decimal(gb["physical"]) == 10 and Decimal(gb["average_cost"]) == Decimal("3256")  # 1870 + 516 + 2×185 + 500
    hsg_positions = {p["kind"]: p["units"] for p in px.part(o, "HSG-100")["positions"]}
    assert hsg_positions == {"consumed": "10.0000"}
    assert px.stock_at(px.housing, px.plant["warehouse_id"]) == 0 and px.stock_at(px.brg, px.plant["warehouse_id"]) == 80
    events = [e["event_type"] for e in o["events"]]
    assert "completed" in events and events.count("sent_to_vendor") >= 5

    # Finished goods → warehouse (foundation transfer) → dispatch to customer
    t = px.post("/api/v1/inventory/transfers", {"source_warehouse_id": px.plant["warehouse_id"], "destination_warehouse_id": px.wh["id"],
                                                 "items": [{"product_id": px.gearbox["id"], "quantity": "10"}]})
    for action in ("submit", "approve"):
        client.post(f"/api/v1/inventory/transfers/{t['id']}/{action}", headers=h)
    for action in ("dispatch", "receive"):
        assert client.post(f"/api/v1/inventory/transfers/{t['id']}/{action}", headers={**h, **key()}).status_code == 200
    cust = px.post("/api/v1/customers", {"code": "SHREE", "name": "Shree Engineering"})
    dn = px.post("/api/v1/dispatch-notes", {"customer_id": cust["id"], "warehouse_id": px.wh["id"], "order_id": o["id"],
                                            "lines": [{"product_id": px.gearbox["id"], "quantity": "10"}], "vehicle_number": "MH12AB1234"})
    r = client.post(f"/api/v1/dispatch-notes/{dn['id']}/dispatch", headers={**h, **key()})
    assert r.status_code == 200 and r.json()["status"] == "dispatched"
    assert px.stock_at(px.gearbox, px.wh["id"]) == 0
    mv = client.get("/api/v1/inventory/movements", headers=h, params={"movement_type": "dispatch"}).json()["items"][0]
    assert mv["value"] == "-32560.0000"


def test_scrap_consumes_kit_and_reduces_target(client: TestClient, px: Plant):
    o = px.order(qty="5")
    px.post(f"/api/v1/production/orders/{o['id']}/release", {}, status=200)
    px.complete(o, "HSG-100", "Cutting", units_completed="5")
    px.challan(o, "HSG-100", "Machining", "5")
    px.receipt(o, "HSG-100", "Machining", "VA", status=422, units_scrapped="1")  # reason required
    px.receipt(o, "HSG-100", "Machining", "VA", units_accepted="4", units_scrapped="1", scrap_reason="Blow hole in casting")
    va = px.vendors["VA"]["warehouse_id"]
    assert px.stock_at(px.blank, va) == 0
    o = px.refresh(o)
    assert px.stage(o, "HSG-100", "Machining")["status"] == "completed"
    assert px.stage(o, "HSG-100", "Coating")["target"] == "4.0000"
    scrap = client.get("/api/v1/inventory/movements", headers=px.h, params={"movement_type": "production_scrap"}).json()["items"]
    assert scrap[0]["quantity"] == "-1.0000" and scrap[0]["value"] == "-1000.0000"
    wl = {w["code"]: w for w in client.get("/api/v1/production/vendors/workload", headers=px.h).json()}
    assert wl["V-VA"]["rejection_rate"] == 20.0 and wl["V-VA"]["units_processed"] == "5.0000"


def test_direct_shipment_rules(px: Plant):
    o = px.order(qty="2")
    px.post(f"/api/v1/production/orders/{o['id']}/release", {}, status=200)
    px.complete(o, "HSG-100", "Cutting", units_completed="2")
    px.challan(o, "HSG-100", "Machining", "2")
    px.challan(o, "SFT-100", "Turning & grinding", "2")
    # last stage cannot ship "to next vendor"
    px.receipt(o, "SFT-100", "Turning & grinding", "VA", status=409, units_accepted="2", direct_to_next=True)
    # receiving into another vendor needs the direct-shipment option
    px.receipt(o, "HSG-100", "Machining", "VA", status=422, units_accepted="2", receive_to_site_id=px.vendors["VB"]["warehouse_id"])


def test_stage_editing_per_order(client: TestClient, px: Plant):
    o = px.order(qty="3")
    machining = px.stage(o, "HSG-100", "Machining")
    vc = px.vendors["VC"]["warehouse_id"]
    r = client.patch(f"/api/v1/production/stages/{machining['id']}", headers=px.h, json={"site_id": vc, "rate_per_unit": "600"})
    assert r.status_code == 200
    assert px.stage(r.json(), "HSG-100", "Machining")["site_name"] == "Sharp Tools Pvt Ltd (job work)"
    # the default route is unchanged
    route = client.get(f"/api/v1/production/products/{px.housing['id']}/structure", headers=px.h).json()["route"]
    assert route[1]["site_name"] == "Precision Machining Works (job work)"
    part = px.part(o, "HSG-100")
    r = client.post(f"/api/v1/production/parts/{part['id']}/stages", headers=px.h, json={
        "after_sequence": 3, "name": "Final inspection", "performer": "plant", "planned_days": 1})
    assert r.status_code == 201 and [s["name"] for s in px.part(r.json(), "HSG-100")["stages"]][-1] == "Final inspection"
    bad = client.patch(f"/api/v1/production/stages/{machining['id']}", headers=px.h, json={"performer": "vendor", "site_id": px.plant["warehouse_id"]})
    assert bad.status_code == 422  # a vendor stage must point at a vendor site
    px.post(f"/api/v1/production/orders/{o['id']}/release", {}, status=200)
    px.complete(o, "HSG-100", "Cutting", units_completed="3")
    px.challan(o, "HSG-100", "Machining", "3")  # goes to Vendor C now
    assert px.stock_at(px.blank, vc) == 3
    r = client.patch(f"/api/v1/production/stages/{machining['id']}", headers=px.h, json={"site_id": px.vendors["VA"]["warehouse_id"]})
    assert r.status_code == 409  # started stages keep their vendor
    assert client.delete(f"/api/v1/production/stages/{machining['id']}", headers=px.h).status_code == 409


def test_structure_validation(client: TestClient, px: Plant):
    r = client.put(f"/api/v1/production/products/{px.blank['id']}/structure", headers=px.h,
                   json={"bom": [{"component_id": px.housing["id"], "quantity": "1"}], "route": []})
    assert r.status_code == 422  # housing contains the blank → cycle
    r = client.put(f"/api/v1/production/products/{px.brg['id']}/structure", headers=px.h,
                   json={"bom": [], "route": [{"name": "X", "performer": "vendor"}]})
    assert r.status_code == 422


def test_challan_is_idempotent_and_printable(client: TestClient, px: Plant):
    o = px.order(qty="2")
    px.post(f"/api/v1/production/orders/{o['id']}/release", {}, status=200)
    st = px.stage(o, "SFT-100", "Turning & grinding")
    k = key()
    r1 = client.post(f"/api/v1/production/stages/{st['id']}/challans", headers={**px.h, **k}, json={"units": "2"})
    r2 = client.post(f"/api/v1/production/stages/{st['id']}/challans", headers={**px.h, **k}, json={"units": "2"})
    assert r1.json()["id"] == r2.json()["id"] and r2.headers.get("Idempotent-Replay") == "true"
    assert px.stock_at(px.steel, px.vendors["VA"]["warehouse_id"]) == Decimal("2.4")
    ch = client.get(f"/api/v1/production/challans/{r1.json()['id']}", headers=px.h).json()
    assert ch["lines"][0]["quantity"] == "2.4000" and ch["total_value"] == "192.0000" and ch["vendor_name"].startswith("Precision")


def test_cancel_only_without_activity(client: TestClient, px: Plant):
    o = px.order(qty="1")
    r = client.post(f"/api/v1/production/orders/{o['id']}/cancel", headers=px.h, json={"reason": "Customer changed spec"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    o2 = px.order(qty="1")
    px.post(f"/api/v1/production/orders/{o2['id']}/release", {}, status=200)
    px.challan(o2, "SFT-100", "Turning & grinding", "1")
    assert client.post(f"/api/v1/production/orders/{o2['id']}/cancel", headers=px.h, json={"reason": "x reason"}).status_code == 409


def test_production_permissions(client: TestClient, px: Plant, make_user):
    staff = make_user("inventory_staff")  # view + execute
    viewer = make_user("read_only")       # view only
    o = px.order(qty="1")
    assert client.get(f"/api/v1/production/orders/{o['id']}", headers=viewer).status_code == 200
    assert client.post("/api/v1/production/orders", headers=staff, json={"product_id": px.gearbox["id"], "quantity": "1",
                                                                         "plant_id": px.plant["warehouse_id"]}).status_code == 403
    px.post(f"/api/v1/production/orders/{o['id']}/release", {}, status=200)
    st = px.stage(o, "SFT-100", "Turning & grinding")
    assert client.post(f"/api/v1/production/stages/{st['id']}/challans", headers={**viewer, **key()}, json={"units": "1"}).status_code == 403
    assert client.post(f"/api/v1/production/stages/{st['id']}/challans", headers={**staff, **key()}, json={"units": "1"}).status_code == 201


def test_summary_and_delays(client: TestClient, px: Plant):
    o = px.order(qty="2", start_date=(date.today() - timedelta(days=20)).isoformat())
    px.post(f"/api/v1/production/orders/{o['id']}/release", {}, status=200)
    s = client.get("/api/v1/production/summary", headers=px.h).json()
    assert s["open_orders"] == 1 and s["late_stages"] >= 1
    delays = client.get("/api/v1/production/delays", headers=px.h).json()
    assert delays[0]["order_number"] == o["order_number"] and delays[0]["days_late"] > 0
