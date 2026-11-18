"""Background tasks: alert digest respects preferences and is idempotent per day."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.conftest import MD


def test_alert_digest(client: TestClient, admin_headers: dict, md: MD, db) -> None:
    from app.workers.tasks import run_alert_digest
    wh = md.warehouse("WH-A")
    p = md.product("STL-1", "Steel plate")
    client.post("/api/v1/inventory/opening-balances", headers={**admin_headers, "Idempotency-Key": uuid.uuid4().hex},
                json={"lines": [{"product_id": p["id"], "location_id": wh["main"], "quantity": "10", "unit_cost": "80"}]})
    assert run_alert_digest(db) == 1
    db.commit()
    assert run_alert_digest(db) == 0  # once per day
    notes = client.get("/api/v1/notifications", headers=admin_headers).json()
    assert notes["total"] == 1 and "critical stock" in notes["items"][0]["body"]


def test_alert_digest_honours_opt_out(client: TestClient, admin_headers: dict, md: MD, db) -> None:
    from app.workers.tasks import run_alert_digest
    wh = md.warehouse("WH-A")
    p = md.product("STL-1", "Steel plate")
    client.post("/api/v1/inventory/opening-balances", headers={**admin_headers, "Idempotency-Key": uuid.uuid4().hex},
                json={"lines": [{"product_id": p["id"], "location_id": wh["main"], "quantity": "10", "unit_cost": "80"}]})
    client.put("/api/v1/notifications/preferences", headers=admin_headers,
               json=[{"alert_type": "critical_stock", "in_app": False, "email": False}])
    assert run_alert_digest(db) == 0
