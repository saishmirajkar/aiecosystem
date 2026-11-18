"""Migrations must produce exactly the schema the ORM models declare (no drift)."""
from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

import app.models  # noqa: F401
from app.core.database import Base, engine


def test_migrated_schema_matches_models() -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"Schema drift between models and migrations: {diff}"


def test_ledger_trigger_installed() -> None:
    from sqlalchemy import text
    with engine.connect() as conn:
        found = conn.execute(text("SELECT 1 FROM pg_trigger WHERE tgname = 'trg_stock_movements_immutable'")).first()
    assert found is not None
