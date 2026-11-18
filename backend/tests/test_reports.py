"""Reports return real ledger data, honour filters and export safe CSV."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.conftest import MD


def key():
    return {"Idempotency-Key": uuid.uuid4().hex}


def test_reports_catalogue_and_data(client: TestClient, admin_headers: dict, md: MD, make_user) -> None:
    h = admin_headers
    wh = md.warehouse("WH-A")
    steel = md.product("STL-1", "Steel plate")
    client.post("/api/v1/inventory/opening-balances", headers={**h, **key()}, json={
        "lines": [{"product_id": steel["id"], "location_id": wh["main"], "quantity": "80", "unit_cost": "100"}]})
    client.post("/api/v1/inventory/issues", headers={**h, **key()}, json={
        "reason": "=HYPERLINK(\"http://evil\")", "lines": [{"product_id": steel["id"], "location_id": wh["main"], "quantity": "5"}]})
    catalogue = client.get("/api/v1/reports", headers=h).json()
    assert len(catalogue) == 13
    cur = client.get("/api/v1/reports/current_stock", headers=h).json()
    assert cur["total"] == 1 and cur["rows"][0]["physical"] == "75.0000" and cur["rows"][0]["value"] == "7500.0000"
    low = client.get("/api/v1/reports/low_stock", headers=h).json()
    assert low["rows"][0]["status"] == "low"
    out = client.get("/api/v1/reports/stock_out", headers=h).json()
    assert out["total"] == 1 and out["rows"][0]["quantity"] == "-5.0000"
    assert client.get("/api/v1/reports/stock_out", headers=h, params={"date_from": "2020-01-01", "date_to": "2020-01-31"}).json()["total"] == 0
    assert client.get("/api/v1/reports/product_history", headers=h).status_code == 422
    assert client.get("/api/v1/reports/product_history", headers=h, params={"product_id": steel["id"]}).json()["total"] == 2
    assert client.get("/api/v1/reports/nope", headers=h).status_code == 404
    csv_resp = client.get("/api/v1/reports/stock_out", headers=h, params={"format": "csv"})
    assert csv_resp.status_code == 200 and csv_resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_resp.headers["content-disposition"]
    assert "'=HYPERLINK" in csv_resp.text  # formula injection neutralised
    staff = make_user("inventory_staff")  # no reports.view
    assert client.get("/api/v1/reports/current_stock", headers=staff).status_code == 403
