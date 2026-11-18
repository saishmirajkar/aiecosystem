"""Test fixtures: real PostgreSQL test database, migrated with Alembic, truncated between tests."""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://above:above@localhost:5432/above_test")
os.environ["ENVIRONMENT"] = "test"
os.environ["AI_ENGINE"] = "offline"
os.environ["UPLOAD_DIR"] = "/tmp/above-test-uploads"
os.environ.pop("ANTHROPIC_API_KEY", None)

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import SessionLocal, engine
from app.core.rate_limit import limiter

PASSWORD = "Str0ng-Passw0rd!"


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> Iterator[None]:
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    # Start from an empty schema so leftovers from another branch or run cannot interfere,
    # then prove the migrations apply, roll back and re-apply.
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


def _truncate_all() -> None:
    with engine.begin() as conn:
        tables = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'")).scalars().all()
        if tables:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def clean_db() -> Iterator[None]:
    _truncate_all()
    limiter.reset()
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import app
    with TestClient(app) as c:
        yield c


ADMIN_EMAIL = "admin@demo.example.com"


@pytest.fixture
def installed(client: TestClient) -> dict:
    r = client.post("/api/v1/setup/initialize", json={
        "company": {"name": "Precision Forge Pvt Ltd", "currency_code": "INR", "timezone": "Asia/Kolkata"},
        "admin": {"full_name": "Asha Admin", "email": ADMIN_EMAIL, "password": PASSWORD},
    })
    assert r.status_code == 201, r.text
    return r.json()


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def admin_headers(client: TestClient, installed: dict) -> dict[str, str]:
    return login(client, ADMIN_EMAIL)


def role_id(client: TestClient, headers: dict, code: str) -> str:
    roles = client.get("/api/v1/roles", headers=headers).json()
    return next(r["id"] for r in roles if r["code"] == code)


@pytest.fixture
def make_user(client: TestClient, admin_headers: dict):
    """Create a user with the given role codes and return auth headers for them."""

    def _make(*role_codes: str, warehouse_ids: list[str] | None = None) -> dict[str, str]:
        email = f"user-{uuid.uuid4().hex[:8]}@demo.example.com"
        r = client.post("/api/v1/users", headers=admin_headers, json={
            "email": email, "full_name": "Test User", "password": PASSWORD,
            "role_ids": [role_id(client, admin_headers, c) for c in role_codes],
            "warehouse_ids": warehouse_ids or [],
        })
        assert r.status_code == 201, r.text
        return login(client, email)

    return _make


class MD:
    """Convenience builder for master data through the public API."""

    def __init__(self, client: TestClient, headers: dict[str, str]):
        self.c, self.h = client, headers
        self.units = {u["code"]: u["id"] for u in client.get("/api/v1/units", headers=headers).json()}

    def warehouse(self, code: str, name: str | None = None) -> dict:
        r = self.c.post("/api/v1/warehouses", headers=self.h, json={"code": code, "name": name or f"Warehouse {code}"})
        assert r.status_code == 201, r.text
        wh = r.json()
        locs = self.c.get(f"/api/v1/warehouses/{wh['id']}/locations", headers=self.h).json()
        wh["main"] = next(l["id"] for l in locs if l["code"] == "MAIN")
        wh["transit"] = next(l["id"] for l in locs if l["code"] == "TRANSIT")
        return wh

    def supplier(self, code: str = "SUP-1", name: str = "Bharat Steel Traders") -> dict:
        r = self.c.post("/api/v1/suppliers", headers=self.h, json={"code": code, "name": name, "lead_time_days": 7})
        assert r.status_code == 201, r.text
        return r.json()

    def product(self, sku: str, name: str, uom: str = "KG", **extra) -> dict:
        body = {"sku": sku, "name": name, "product_type": "raw_material", "uom_id": self.units[uom],
                "purchase_price": "80", "standard_cost": "78", "reorder_level": "100", "min_stock_level": "50",
                "max_stock_level": "5000", "reorder_quantity": "500", **extra}
        r = self.c.post("/api/v1/products", headers=self.h, json=body)
        assert r.status_code == 201, r.text
        return r.json()


@pytest.fixture
def md(client: TestClient, admin_headers: dict) -> MD:
    return MD(client, admin_headers)
