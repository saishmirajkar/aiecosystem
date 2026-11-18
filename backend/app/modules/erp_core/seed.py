"""Idempotent reference-data seeding (permissions, system roles, departments, units, settings)."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import DEFAULT_SETTINGS
from app.core.permissions import DEFAULT_DEPARTMENTS, DEFAULT_ROLES, PERMISSIONS
from app.models import Department, Permission, Role, SystemSetting, UnitConversion, UnitOfMeasure, UomCategory

DEFAULT_UNITS: list[tuple[str, str, UomCategory, int]] = [
    ("KG", "Kilogram", UomCategory.MASS, 3), ("G", "Gram", UomCategory.MASS, 2),
    ("T", "Metric tonne", UomCategory.MASS, 4), ("L", "Litre", UomCategory.VOLUME, 3),
    ("ML", "Millilitre", UomCategory.VOLUME, 1), ("M", "Metre", UomCategory.LENGTH, 3),
    ("MM", "Millimetre", UomCategory.LENGTH, 1), ("PCS", "Pieces", UomCategory.COUNT, 0),
    ("UNIT", "Units", UomCategory.COUNT, 0), ("BOX", "Box", UomCategory.PACKAGING, 0),
    ("SET", "Set", UomCategory.COUNT, 0), ("M2", "Square metre", UomCategory.AREA, 3),
]

DEFAULT_CONVERSIONS: list[tuple[str, str, str]] = [
    ("KG", "G", "1000"), ("T", "KG", "1000"), ("L", "ML", "1000"), ("M", "MM", "1000"),
]


def seed_reference_data(db: Session) -> None:
    existing_perms = {p.code: p for p in db.execute(select(Permission)).scalars()}
    for p in PERMISSIONS:
        if p.code in existing_perms:
            existing_perms[p.code].name, existing_perms[p.code].module = p.name, p.module
        else:
            db.add(Permission(code=p.code, name=p.name, module=p.module))
    db.flush()
    perm_by_code = {p.code: p for p in db.execute(select(Permission)).scalars()}

    for code, spec in DEFAULT_ROLES.items():
        role = db.execute(select(Role).where(Role.code == code)).scalar_one_or_none()
        if role is None:
            role = Role(code=code, name=str(spec["name"]), description=str(spec["description"]), is_system=True)
            role.permissions = [perm_by_code[c] for c in spec["permissions"]]  # type: ignore[union-attr]
            db.add(role)
        elif code == "super_admin":
            role.permissions = list(perm_by_code.values())

    for code, name in DEFAULT_DEPARTMENTS:
        if db.execute(select(Department).where(Department.code == code)).scalar_one_or_none() is None:
            db.add(Department(code=code, name=name))

    units = {u.code: u for u in db.execute(select(UnitOfMeasure)).scalars()}
    for code, name, cat, decimals in DEFAULT_UNITS:
        if code not in units:
            units[code] = UnitOfMeasure(code=code, name=name, category=cat, decimals=decimals)
            db.add(units[code])
    db.flush()
    for f, t, factor in DEFAULT_CONVERSIONS:
        exists = db.execute(select(UnitConversion).where(
            UnitConversion.from_uom_id == units[f].id, UnitConversion.to_uom_id == units[t].id,
            UnitConversion.product_id.is_(None))).scalar_one_or_none()
        if exists is None:
            db.add(UnitConversion(from_uom_id=units[f].id, to_uom_id=units[t].id, factor=Decimal(factor)))

    for key, (value, description) in DEFAULT_SETTINGS.items():
        if db.get(SystemSetting, key) is None:
            db.add(SystemSetting(key=key, value=value, description=description))
    db.flush()
