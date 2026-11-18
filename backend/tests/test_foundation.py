"""Foundation tests: setup, authentication, sessions, RBAC, users, roles, departments, company, settings, audit."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ADMIN_EMAIL, PASSWORD, login, role_id


def test_setup_runs_once(client: TestClient, installed: dict) -> None:
    assert client.get("/api/v1/setup/status").json() == {"initialized": True, "demo_data": False}
    r = client.post("/api/v1/setup/initialize", json={
        "company": {"name": "X"}, "admin": {"full_name": "Y", "email": "y@demo.example.com", "password": PASSWORD}})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "setup_already_completed"


def test_setup_rejects_weak_password(client: TestClient) -> None:
    r = client.post("/api/v1/setup/initialize", json={
        "company": {"name": "X"}, "admin": {"full_name": "Y", "email": "y@demo.example.com", "password": "short"}})
    assert r.status_code == 422


def test_login_me_logout(client: TestClient, installed: dict) -> None:
    headers = login(client, ADMIN_EMAIL)
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me["email"] == ADMIN_EMAIL
    assert "inventory.view" in me["permissions"] and me["company"]["name"] == "Precision Forge Pvt Ltd"
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_session_probe(client: TestClient, installed: dict) -> None:
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False, "user": None}
    headers = login(client, ADMIN_EMAIL)
    body = client.get("/api/v1/auth/session", headers=headers).json()
    assert body["authenticated"] is True and body["user"]["email"] == ADMIN_EMAIL


def test_invalid_credentials_and_lockout(client: TestClient, installed: dict) -> None:
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password-1"})
        assert r.status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": PASSWORD})
    assert r.status_code == 423
    assert r.json()["error"]["code"] == "account_locked"


def test_unauthenticated_and_error_envelope(client: TestClient, installed: dict) -> None:
    r = client.get("/api/v1/users")
    assert r.status_code == 401
    body = r.json()["error"]
    assert body["code"] == "authentication_required" and body["request_id"]


def test_cookie_session_requires_csrf_header(client: TestClient, installed: dict) -> None:
    r = client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": PASSWORD})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert client.get("/api/v1/auth/me").status_code == 200  # cookie works for reads
    r = client.patch("/api/v1/auth/me", json={"job_title": "CEO"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = client.patch("/api/v1/auth/me", json={"job_title": "CEO"}, headers={"X-Requested-With": "above-web"})
    assert r.status_code == 200 and r.json()["job_title"] == "CEO"


def test_rbac_enforced_on_backend(client: TestClient, make_user) -> None:
    readonly = make_user("read_only")
    assert client.get("/api/v1/users", headers=readonly).status_code == 403
    assert client.get("/api/v1/departments", headers=readonly).status_code == 200
    r = client.post("/api/v1/departments", headers=readonly, json={"code": "RND", "name": "R&D"})
    assert r.status_code == 403 and r.json()["error"]["details"]["permission"] == "departments.manage"


def test_user_lifecycle_and_session_revocation(client: TestClient, admin_headers: dict) -> None:
    r = client.post("/api/v1/users", headers=admin_headers, json={
        "email": "staff@demo.example.com", "full_name": "Staff", "password": PASSWORD,
        "role_ids": [role_id(client, admin_headers, "inventory_staff")]})
    assert r.status_code == 201
    uid = r.json()["id"]
    staff = login(client, "staff@demo.example.com")
    assert client.get("/api/v1/auth/me", headers=staff).status_code == 200
    assert client.post(f"/api/v1/users/{uid}/deactivate", headers=admin_headers).json()["is_active"] is False
    assert client.get("/api/v1/auth/me", headers=staff).status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "staff@demo.example.com", "password": PASSWORD})
    assert r.status_code == 401
    client.post(f"/api/v1/users/{uid}/activate", headers=admin_headers)
    assert client.post("/api/v1/auth/login", json={"email": "staff@demo.example.com", "password": PASSWORD}).status_code == 200


def test_duplicate_email_rejected_case_insensitive(client: TestClient, admin_headers: dict) -> None:
    r = client.post("/api/v1/users", headers=admin_headers, json={
        "email": ADMIN_EMAIL.upper(), "full_name": "Dup", "password": PASSWORD})
    assert r.status_code == 409


def test_cannot_escalate_to_super_admin(client: TestClient, admin_headers: dict, make_user) -> None:
    company_admin = make_user("company_admin")
    r = client.post("/api/v1/users", headers=company_admin, json={
        "email": "evil@demo.example.com", "full_name": "Evil", "password": PASSWORD,
        "role_ids": [role_id(client, admin_headers, "super_admin")]})
    assert r.status_code == 403


def test_cannot_change_own_roles_or_deactivate_self(client: TestClient, admin_headers: dict) -> None:
    me = client.get("/api/v1/auth/me", headers=admin_headers).json()
    r = client.patch(f"/api/v1/users/{me['id']}", headers=admin_headers, json={"role_ids": []})
    assert r.status_code == 403
    assert client.post(f"/api/v1/users/{me['id']}/deactivate", headers=admin_headers).status_code == 409


def test_custom_role_permissions_are_configurable(client: TestClient, admin_headers: dict) -> None:
    r = client.post("/api/v1/roles", headers=admin_headers, json={
        "code": "auditor", "name": "Auditor", "permissions": ["audit.view", "inventory.view"]})
    assert r.status_code == 201
    rid = r.json()["id"]
    client.post("/api/v1/users", headers=admin_headers, json={
        "email": "aud@demo.example.com", "full_name": "Aud", "password": PASSWORD, "role_ids": [rid]})
    aud = login(client, "aud@demo.example.com")
    assert client.get("/api/v1/audit-logs", headers=aud).status_code == 200
    assert client.get("/api/v1/users", headers=aud).status_code == 403
    client.patch(f"/api/v1/roles/{rid}", headers=admin_headers, json={"permissions": ["users.view"]})
    assert client.get("/api/v1/users", headers=aud).status_code == 200
    assert client.get("/api/v1/audit-logs", headers=aud).status_code == 403
    assert client.post("/api/v1/roles", headers=admin_headers, json={
        "code": "bad", "name": "Bad", "permissions": ["not.a.permission"]}).status_code == 422
    sys_role = role_id(client, admin_headers, "read_only")
    assert client.delete(f"/api/v1/roles/{sys_role}", headers=admin_headers).status_code == 409


def test_departments_seeded_and_manageable(client: TestClient, admin_headers: dict) -> None:
    depts = client.get("/api/v1/departments", headers=admin_headers).json()
    assert {d["code"] for d in depts} >= {"MGMT", "INV", "WH", "PROC", "PROD", "FIN", "QA", "MAINT", "HR", "LOG"}
    r = client.post("/api/v1/departments", headers=admin_headers, json={"code": "RND", "name": "Research"})
    assert r.status_code == 201
    r = client.patch(f"/api/v1/departments/{r.json()['id']}", headers=admin_headers, json={"is_active": False})
    assert r.json()["is_active"] is False


def test_company_and_settings(client: TestClient, admin_headers: dict) -> None:
    r = client.patch("/api/v1/company", headers=admin_headers, json={"timezone": "Europe/Berlin", "currency_code": "EUR"})
    assert r.status_code == 200 and r.json()["timezone"] == "Europe/Berlin"
    assert client.patch("/api/v1/company", headers=admin_headers, json={"timezone": "Mars/Base"}).status_code == 422
    r = client.patch("/api/v1/settings", headers=admin_headers, json={"values": {"inventory.high_value_threshold": 250000}})
    assert r.status_code == 200
    assert next(s for s in r.json() if s["key"] == "inventory.high_value_threshold")["value"] == "250000"
    assert client.patch("/api/v1/settings", headers=admin_headers, json={"values": {"nope": 1}}).status_code == 422
    assert client.patch("/api/v1/settings", headers=admin_headers,
                        json={"values": {"inventory.allow_negative_stock": "yes"}}).status_code == 422


def test_audit_log_records_actions(client: TestClient, admin_headers: dict) -> None:
    client.patch("/api/v1/company", headers=admin_headers, json={"city": "Pune"})
    logs = client.get("/api/v1/audit-logs", headers=admin_headers, params={"entity_type": "company"}).json()
    actions = [l["action"] for l in logs["items"]]
    assert "company.update" in actions and "setup.initialize" in actions


def test_password_change_and_reset(client: TestClient, admin_headers: dict) -> None:
    from app.core.database import SessionLocal
    from app.modules.erp_core.service import request_password_reset

    r = client.post("/api/v1/auth/change-password", headers=admin_headers,
                    json={"current_password": PASSWORD, "new_password": "An0ther-Strong-Pass"})
    assert r.status_code == 204
    login(client, ADMIN_EMAIL, "An0ther-Strong-Pass")
    assert client.post("/api/v1/auth/password-reset/request", json={"email": "unknown@demo.example.com"}).status_code == 202
    with SessionLocal() as db:
        token = request_password_reset(db, ADMIN_EMAIL)
        db.commit()
    assert token
    r = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "Reset-Passw0rd-9"})
    assert r.status_code == 204
    r = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "Reset-Passw0rd-10"})
    assert r.status_code == 422  # single use
    login(client, ADMIN_EMAIL, "Reset-Passw0rd-9")


def test_notification_preferences(client: TestClient, admin_headers: dict) -> None:
    r = client.put("/api/v1/notifications/preferences", headers=admin_headers,
                   json=[{"alert_type": "low_stock", "in_app": False, "email": True}])
    assert r.status_code == 200
    pref = next(p for p in r.json() if p["alert_type"] == "low_stock")
    assert pref == {"alert_type": "low_stock", "in_app": False, "email": True}


def test_upload_validation(client: TestClient, admin_headers: dict) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = client.post("/api/v1/company/logo", headers=admin_headers, files={"file": ("logo.png", png, "image/png")})
    assert r.status_code == 200 and r.json()["logo_attachment_id"]
    r = client.post("/api/v1/company/logo", headers=admin_headers, files={"file": ("logo.png", b"<script>", "image/png")})
    assert r.status_code == 422
    r = client.post("/api/v1/company/logo", headers=admin_headers, files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 422


def test_health(client: TestClient) -> None:
    body = client.get("/api/v1/health").json()
    assert body["database"] == "ok" and body["ai_engine"] == "offline"


def test_demo_data_loads_consistently(client: TestClient) -> None:
    r = client.post("/api/v1/setup/initialize", json={
        "company": {"name": "Demo Precision Components"}, "load_demo_data": True,
        "admin": {"full_name": "Demo Admin", "email": ADMIN_EMAIL, "password": PASSWORD}})
    assert r.status_code == 201, r.text
    assert client.get("/api/v1/setup/status").json()["demo_data"] is True
    h = login(client, ADMIN_EMAIL)
    ov = client.get("/api/v1/inventory/overview", headers=h).json()
    m = ov["metrics"]
    assert m["active_products"] == 25 and m["warehouse_count"] == 3 and m["pos_awaiting_delivery"] >= 3
    assert m["low_stock_items"] >= 1 and m["pending_goods_receipts"] == 1
    assert sum(1 for d in ov["trend"] if d["movements"]) >= 25
    val = client.get("/api/v1/reports/inventory_valuation", headers=h).json()
    from decimal import Decimal
    total_report = sum(Decimal(r["value"]) for r in val["rows"])
    assert total_report == Decimal(m["total_stock_value"])
    login(client, "staff@demo.example.com", "Demo-Passw0rd!")


def test_reset_token_never_logged_in_production(client: TestClient, installed: dict, caplog, monkeypatch) -> None:
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "environment", "production")
    with caplog.at_level("WARNING", logger="above.auth"):
        client.post("/api/v1/auth/password-reset/request", json={"email": ADMIN_EMAIL})
    assert "reset-password?token=" not in caplog.text and "no mail backend" in caplog.text
