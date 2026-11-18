"""Master data CRUD, validation and permission tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import MD


def test_units_seeded_and_conversion(client: TestClient, admin_headers: dict, md: MD) -> None:
    assert {"KG", "G", "L", "M", "PCS", "BOX", "UNIT"} <= set(md.units)
    convs = client.get("/api/v1/unit-conversions", headers=admin_headers).json()
    assert any(c["from_uom_code"] == "KG" and c["to_uom_code"] == "G" and c["factor"].startswith("1000") for c in convs)
    p = md.product("BOLT-M8", "Hex bolt M8", uom="PCS")
    r = client.post("/api/v1/unit-conversions", headers=admin_headers,
                    json={"from_uom_id": md.units["BOX"], "to_uom_id": md.units["PCS"], "factor": "100", "product_id": p["id"]})
    assert r.status_code == 201
    r = client.post("/api/v1/unit-conversions", headers=admin_headers,
                    json={"from_uom_id": md.units["BOX"], "to_uom_id": md.units["PCS"], "factor": "100", "product_id": p["id"]})
    assert r.status_code == 409
    from app.core.database import SessionLocal
    from app.modules.master_data.service import convert_quantity
    from decimal import Decimal
    import uuid
    with SessionLocal() as db:
        assert convert_quantity(db, Decimal("2"), uuid.UUID(md.units["BOX"]), uuid.UUID(md.units["PCS"]), uuid.UUID(p["id"])) == Decimal("200")
        assert convert_quantity(db, Decimal("2500"), uuid.UUID(md.units["G"]), uuid.UUID(md.units["KG"])) == Decimal("2.5")


def test_product_crud_and_validation(client: TestClient, admin_headers: dict, md: MD) -> None:
    cat = client.post("/api/v1/categories", headers=admin_headers, json={"code": "STEEL", "name": "Steel"}).json()
    p = md.product("STL-RD-20", "Steel round bar 20mm", category_id=cat["id"], barcode="8901234567890", manufacturer="JSW")
    assert p["uom_code"] == "KG" and p["category_name"] == "Steel"
    assert p["stock"]["physical"] == "0" and p["stock"]["status"] == "out"
    assert isinstance(p["purchase_price"], str)
    dup = client.post("/api/v1/products", headers=admin_headers, json={
        "sku": "stl-rd-20", "name": "Dup", "product_type": "raw_material", "uom_id": md.units["KG"]})
    assert dup.status_code == 409
    bad = client.post("/api/v1/products", headers=admin_headers, json={
        "sku": "X1", "name": "Bad", "product_type": "raw_material", "uom_id": md.units["KG"],
        "min_stock_level": "10", "max_stock_level": "5"})
    assert bad.status_code == 422
    neg = client.post("/api/v1/products", headers=admin_headers, json={
        "sku": "X2", "name": "Bad", "product_type": "raw_material", "uom_id": md.units["KG"], "purchase_price": "-1"})
    assert neg.status_code == 422
    r = client.patch(f"/api/v1/products/{p['id']}", headers=admin_headers, json={"reorder_level": "250", "is_active": False})
    assert r.status_code == 200 and r.json()["reorder_level"] == "250.0000" and r.json()["is_active"] is False
    listed = client.get("/api/v1/products", headers=admin_headers, params={"q": "round", "is_active": False}).json()
    assert listed["total"] == 1
    assert client.get("/api/v1/products", headers=admin_headers, params={"sort": "password"}).status_code == 422


def test_serial_tracking_requires_whole_unit(client: TestClient, admin_headers: dict, md: MD) -> None:
    r = client.post("/api/v1/products", headers=admin_headers, json={
        "sku": "MTR-1", "name": "Motor", "product_type": "component", "uom_id": md.units["KG"], "track_serials": True})
    assert r.status_code == 422
    assert md.product("MTR-2", "Motor 2HP", uom="PCS", track_serials=True)["track_serials"] is True


def test_product_suppliers(client: TestClient, admin_headers: dict, md: MD) -> None:
    p = md.product("STL-1", "Steel plate")
    s1, s2 = md.supplier("S1", "Supplier One"), md.supplier("S2", "Supplier Two")
    r = client.put(f"/api/v1/products/{p['id']}/suppliers", headers=admin_headers, json=[
        {"supplier_id": s1["id"], "is_preferred": True, "last_purchase_price": "79.5"}, {"supplier_id": s2["id"]}])
    assert r.status_code == 200 and r.json()[0]["supplier_code"] == "S1"
    r = client.put(f"/api/v1/products/{p['id']}/suppliers", headers=admin_headers, json=[
        {"supplier_id": s1["id"], "is_preferred": True}, {"supplier_id": s2["id"], "is_preferred": True}])
    assert r.status_code == 422
    assert client.get("/api/v1/products", headers=admin_headers, params={"supplier_id": s2["id"]}).json()["total"] == 1


def test_warehouse_and_location_hierarchy(client: TestClient, admin_headers: dict, md: MD) -> None:
    wh = md.warehouse("WH-A", "Warehouse A")
    sec = client.post(f"/api/v1/warehouses/{wh['id']}/locations", headers=admin_headers,
                      json={"code": "B", "location_type": "section"}).json()
    rack = client.post(f"/api/v1/warehouses/{wh['id']}/locations", headers=admin_headers,
                       json={"code": "R04", "location_type": "rack", "parent_id": sec["id"]}).json()
    bin_ = client.post(f"/api/v1/warehouses/{wh['id']}/locations", headers=admin_headers,
                       json={"code": "B12", "location_type": "bin", "parent_id": rack["id"]})
    assert bin_.status_code == 201 and bin_.json()["path"] == "B/R04/B12"
    dup = client.post(f"/api/v1/warehouses/{wh['id']}/locations", headers=admin_headers,
                      json={"code": "B12", "location_type": "bin", "parent_id": rack["id"]})
    assert dup.status_code == 409
    transit = client.post(f"/api/v1/warehouses/{wh['id']}/locations", headers=admin_headers,
                          json={"code": "T2", "location_type": "transit"})
    assert transit.status_code == 422
    r = client.patch(f"/api/v1/locations/{wh['transit']}", headers=admin_headers, json={"is_active": False})
    assert r.status_code == 409
    whs = client.get("/api/v1/warehouses", headers=admin_headers).json()
    assert whs[0]["location_count"] == 3


def test_master_data_permissions(client: TestClient, make_user, md: MD) -> None:
    staff = make_user("inventory_staff")
    assert client.get("/api/v1/products", headers=staff).status_code == 200
    r = client.post("/api/v1/products", headers=staff, json={
        "sku": "Z", "name": "Z", "product_type": "consumable", "uom_id": md.units["PCS"]})
    assert r.status_code == 403
    assert client.post("/api/v1/warehouses", headers=staff, json={"code": "W9", "name": "W9"}).status_code == 403
    assert client.post("/api/v1/suppliers", headers=staff, json={"code": "S9", "name": "S9"}).status_code == 403
    purchaser = make_user("purchase_manager")
    assert client.post("/api/v1/suppliers", headers=purchaser, json={"code": "S9", "name": "S9"}).status_code == 201


def test_warehouse_scoping(client: TestClient, make_user, md: MD) -> None:
    a, b = md.warehouse("WA"), md.warehouse("WB")
    scoped = make_user("warehouse_manager", warehouse_ids=[a["id"]])
    whs = client.get("/api/v1/warehouses", headers=scoped).json()
    assert [w["code"] for w in whs] == ["WA"]
    assert client.get(f"/api/v1/warehouses/{b['id']}", headers=scoped).status_code == 404
    r = client.post(f"/api/v1/warehouses/{b['id']}/locations", headers=scoped, json={"code": "X", "location_type": "bin"})
    assert r.status_code == 403
