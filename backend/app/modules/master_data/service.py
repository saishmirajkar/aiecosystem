"""Master data business logic."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.common import audit, diff
from app.core.deps import Actor
from app.core.errors import BusinessRuleViolation, Conflict, NotFound, ValidationFailed
from app.models import (
    Attachment, InventoryBalance, LocationType, Product, ProductCategory, ProductSupplier, ProductValuation,
    StorageLocation, Supplier, UnitConversion, UnitOfMeasure, User, Warehouse,
)
from app.modules.inventory.queries import stock_summaries, summary_for
from app.modules.master_data import schemas as s


def _get(db: Session, model, obj_id: uuid.UUID, label: str):
    obj = db.get(model, obj_id)
    if obj is None:
        raise NotFound(f"{label} not found")
    return obj


def _unique(db: Session, model, field: str, value: Any, label: str, exclude_id: uuid.UUID | None = None) -> None:
    if value is None:
        return
    col = getattr(model, field)
    stmt = select(model.id).where(func.lower(col) == str(value).lower())
    if exclude_id:
        stmt = stmt.where(model.id != exclude_id)
    if db.execute(stmt).first():
        raise Conflict(f"{label} '{value}' already exists", details={"field": field})


# ---------------------------------------------------------------- units
def create_unit(db: Session, actor: Actor, data: s.UnitIn) -> UnitOfMeasure:
    _unique(db, UnitOfMeasure, "code", data.code, "Unit code")
    u = UnitOfMeasure(**data.model_dump())
    db.add(u)
    db.flush()
    audit(db, actor, "unit.create", "unit", u.id, data.model_dump())
    return u


def update_unit(db: Session, actor: Actor, unit_id: uuid.UUID, data: s.UnitUpdate) -> UnitOfMeasure:
    u = _get(db, UnitOfMeasure, unit_id, "Unit")
    audit(db, actor, "unit.update", "unit", u.id, diff(u, data.model_dump(exclude_unset=True)))
    return u


def conversion_out(c: UnitConversion) -> dict[str, Any]:
    return {"id": c.id, "from_uom_id": c.from_uom_id, "to_uom_id": c.to_uom_id, "from_uom_code": c.from_uom.code,
            "to_uom_code": c.to_uom.code, "factor": c.factor, "product_id": c.product_id}


def create_conversion(db: Session, actor: Actor, data: s.ConversionIn) -> UnitConversion:
    if data.from_uom_id == data.to_uom_id:
        raise ValidationFailed("Conversion units must differ")
    _get(db, UnitOfMeasure, data.from_uom_id, "From unit")
    _get(db, UnitOfMeasure, data.to_uom_id, "To unit")
    if data.product_id:
        _get(db, Product, data.product_id, "Product")
    dup = db.execute(select(UnitConversion).where(
        UnitConversion.from_uom_id == data.from_uom_id, UnitConversion.to_uom_id == data.to_uom_id,
        UnitConversion.product_id.is_(None) if data.product_id is None else UnitConversion.product_id == data.product_id,
    )).scalar_one_or_none()
    if dup:
        raise Conflict("This conversion already exists")
    c = UnitConversion(**data.model_dump())
    db.add(c)
    db.flush()
    db.refresh(c)
    audit(db, actor, "unit_conversion.create", "unit_conversion", c.id, data.model_dump())
    return c


def convert_quantity(db: Session, qty: Decimal, from_uom_id: uuid.UUID, to_uom_id: uuid.UUID,
                     product_id: uuid.UUID | None = None) -> Decimal:
    """Convert using a direct or inverse conversion (product-specific first, then global)."""
    if from_uom_id == to_uom_id:
        return qty
    for pid in ([product_id] if product_id else []) + [None]:
        for f, t, invert in ((from_uom_id, to_uom_id, False), (to_uom_id, from_uom_id, True)):
            c = db.execute(select(UnitConversion).where(
                UnitConversion.from_uom_id == f, UnitConversion.to_uom_id == t,
                UnitConversion.product_id.is_(None) if pid is None else UnitConversion.product_id == pid)).scalar_one_or_none()
            if c:
                return (qty / c.factor) if invert else (qty * c.factor)
    raise ValidationFailed("No unit conversion defined between these units")


# ---------------------------------------------------------------- categories
def category_out(db: Session, c: ProductCategory) -> dict[str, Any]:
    count = db.execute(select(func.count()).select_from(Product).where(Product.category_id == c.id)).scalar_one()
    return {**{k: getattr(c, k) for k in ("id", "code", "name", "parent_id", "description", "is_active")}, "product_count": count}


def _check_parent_cycle(db: Session, category_id: uuid.UUID | None, parent_id: uuid.UUID | None) -> None:
    seen = set()
    current = parent_id
    while current:
        if current == category_id or current in seen:
            raise ValidationFailed("Category hierarchy cannot contain cycles")
        seen.add(current)
        parent = _get(db, ProductCategory, current, "Parent category")
        current = parent.parent_id


def create_category(db: Session, actor: Actor, data: s.CategoryIn) -> ProductCategory:
    _unique(db, ProductCategory, "code", data.code, "Category code")
    _check_parent_cycle(db, None, data.parent_id)
    c = ProductCategory(**data.model_dump(), created_by=actor.user_id, updated_by=actor.user_id)
    db.add(c)
    db.flush()
    audit(db, actor, "category.create", "category", c.id, data.model_dump())
    return c


def update_category(db: Session, actor: Actor, cat_id: uuid.UUID, data: s.CategoryUpdate) -> ProductCategory:
    c = _get(db, ProductCategory, cat_id, "Category")
    updates = data.model_dump(exclude_unset=True)
    if "parent_id" in updates:
        _check_parent_cycle(db, c.id, updates["parent_id"])
    c.updated_by = actor.user_id
    audit(db, actor, "category.update", "category", c.id, diff(c, updates))
    return c


# ---------------------------------------------------------------- suppliers
def create_supplier(db: Session, actor: Actor, data: s.SupplierIn) -> Supplier:
    _unique(db, Supplier, "code", data.code, "Supplier code")
    sup = Supplier(**data.model_dump(), created_by=actor.user_id, updated_by=actor.user_id)
    db.add(sup)
    db.flush()
    audit(db, actor, "supplier.create", "supplier", sup.id, data.model_dump())
    return sup


def update_supplier(db: Session, actor: Actor, sup_id: uuid.UUID, data: s.SupplierUpdate) -> Supplier:
    sup = _get(db, Supplier, sup_id, "Supplier")
    updates = data.model_dump(exclude_unset=True)
    if updates.get("code"):
        _unique(db, Supplier, "code", updates["code"], "Supplier code", exclude_id=sup.id)
    elif "code" in updates:
        updates.pop("code")
    sup.updated_by = actor.user_id
    audit(db, actor, "supplier.update", "supplier", sup.id, diff(sup, updates))
    return sup


# ---------------------------------------------------------------- products
def product_out(p: Product, summary: dict | None = None, include_stock: bool = True) -> dict[str, Any]:
    data = {c: getattr(p, c) for c in s.ProductOut.model_fields if hasattr(p, c) and c not in ("stock",)}
    data["uom_code"] = p.uom.code
    data["category_name"] = p.category.name if p.category else None
    if include_stock:
        data["stock"] = summary_for(p, summary)
    return data


def products_out(db: Session, products: list[Product], actor: Actor | None = None) -> list[dict[str, Any]]:
    sums = stock_summaries(db, [p.id for p in products], actor.warehouse_ids if actor else None)
    return [product_out(p, sums.get(p.id)) for p in products]


def _validate_product_refs(db: Session, data: dict[str, Any]) -> None:
    if data.get("category_id"):
        _get(db, ProductCategory, data["category_id"], "Category")
    if data.get("uom_id"):
        uom = _get(db, UnitOfMeasure, data["uom_id"], "Unit of measure")
        if not uom.is_active:
            raise ValidationFailed("Unit of measure is inactive")
    if data.get("image_attachment_id"):
        att = _get(db, Attachment, data["image_attachment_id"], "Image")
        if not att.content_type.startswith("image/"):
            raise ValidationFailed("Product image must be an image file")


def create_product(db: Session, actor: Actor, data: s.ProductCreate) -> Product:
    _unique(db, Product, "sku", data.sku, "SKU")
    _unique(db, Product, "internal_code", data.internal_code, "Internal code")
    _unique(db, Product, "barcode", data.barcode, "Barcode")
    payload = data.model_dump()
    _validate_product_refs(db, payload)
    if data.track_serials:
        uom = db.get(UnitOfMeasure, data.uom_id)
        if uom and uom.decimals != 0:
            raise ValidationFailed("Serial-tracked products need a whole-number unit (e.g. PCS)")
    p = Product(**payload, created_by=actor.user_id, updated_by=actor.user_id)
    db.add(p)
    db.flush()
    db.add(ProductValuation(product_id=p.id))
    audit(db, actor, "product.create", "product", p.id, payload)
    return p


def update_product(db: Session, actor: Actor, product_id: uuid.UUID, data: s.ProductUpdate) -> Product:
    p = _get(db, Product, product_id, "Product")
    updates = data.model_dump(exclude_unset=True)
    _unique(db, Product, "internal_code", updates.get("internal_code"), "Internal code", exclude_id=p.id)
    _unique(db, Product, "barcode", updates.get("barcode"), "Barcode", exclude_id=p.id)
    _validate_product_refs(db, updates)
    new_min = updates.get("min_stock_level", p.min_stock_level)
    new_max = updates.get("max_stock_level", p.max_stock_level)
    if new_max is not None and new_max < new_min:
        raise ValidationFailed("max_stock_level must be greater than or equal to min_stock_level")
    if updates.get("track_expiry") and not p.track_batches:
        raise ValidationFailed("Expiry tracking requires batch tracking")
    p.updated_by = actor.user_id
    audit(db, actor, "product.update", "product", p.id, diff(p, updates))
    return p


def list_product_suppliers(db: Session, product_id: uuid.UUID) -> list[dict[str, Any]]:
    _get(db, Product, product_id, "Product")
    rows = db.execute(select(ProductSupplier).where(ProductSupplier.product_id == product_id)
                      .order_by(ProductSupplier.is_preferred.desc())).scalars()
    return [{"id": r.id, "supplier_id": r.supplier_id, "supplier_code": r.supplier.code, "supplier_name": r.supplier.name,
             "supplier_sku": r.supplier_sku, "lead_time_days": r.lead_time_days,
             "last_purchase_price": r.last_purchase_price, "is_preferred": r.is_preferred} for r in rows]


def set_product_suppliers(db: Session, actor: Actor, product_id: uuid.UUID, items: list[s.ProductSupplierIn]) -> None:
    _get(db, Product, product_id, "Product")
    ids = [i.supplier_id for i in items]
    if len(ids) != len(set(ids)):
        raise ValidationFailed("Each supplier may appear only once")
    if sum(1 for i in items if i.is_preferred) > 1:
        raise ValidationFailed("Only one preferred supplier is allowed")
    for sid in ids:
        _get(db, Supplier, sid, "Supplier")
    existing = {r.supplier_id: r for r in db.execute(select(ProductSupplier).where(ProductSupplier.product_id == product_id)).scalars()}
    for sid, row in existing.items():
        if sid not in ids:
            db.delete(row)
    for i in items:
        row = existing.get(i.supplier_id)
        if row is None:
            db.add(ProductSupplier(product_id=product_id, **i.model_dump()))
        else:
            for k, v in i.model_dump().items():
                setattr(row, k, v)
    audit(db, actor, "product.suppliers_update", "product", product_id, {"suppliers": [i.model_dump() for i in items]})


# ---------------------------------------------------------------- warehouses & locations
def create_warehouse(db: Session, actor: Actor, data: s.WarehouseIn) -> Warehouse:
    _unique(db, Warehouse, "code", data.code, "Warehouse code")
    if data.manager_user_id:
        _get(db, User, data.manager_user_id, "Manager")
    wh = Warehouse(**data.model_dump(), created_by=actor.user_id, updated_by=actor.user_id)
    db.add(wh)
    db.flush()
    db.add_all([
        StorageLocation(warehouse_id=wh.id, code="MAIN", name="Main storage", location_type=LocationType.STORAGE,
                        path="MAIN", is_default=True, is_system=True, created_by=actor.user_id),
        StorageLocation(warehouse_id=wh.id, code="TRANSIT", name="Inbound in-transit", location_type=LocationType.TRANSIT,
                        path="TRANSIT", is_system=True, created_by=actor.user_id),
    ])
    db.flush()
    audit(db, actor, "warehouse.create", "warehouse", wh.id, data.model_dump())
    return wh


def update_warehouse(db: Session, actor: Actor, wh_id: uuid.UUID, data: s.WarehouseUpdate) -> Warehouse:
    wh = _get(db, Warehouse, wh_id, "Warehouse")
    updates = data.model_dump(exclude_unset=True)
    if updates.get("manager_user_id"):
        _get(db, User, updates["manager_user_id"], "Manager")
    if updates.get("is_active") is False:
        stock = db.execute(select(func.coalesce(func.sum(InventoryBalance.qty_on_hand), 0))
                           .where(InventoryBalance.warehouse_id == wh.id)).scalar_one()
        if stock != 0:
            raise BusinessRuleViolation("Warehouse still holds stock; transfer it out before deactivating")
    wh.updated_by = actor.user_id
    audit(db, actor, "warehouse.update", "warehouse", wh.id, diff(wh, updates))
    return wh


def warehouses_out(db: Session, warehouses: list[Warehouse]) -> list[dict[str, Any]]:
    ids = [w.id for w in warehouses]
    loc_counts = dict(db.execute(select(StorageLocation.warehouse_id, func.count()).where(
        StorageLocation.warehouse_id.in_(ids), StorageLocation.is_system.is_(False)).group_by(StorageLocation.warehouse_id)).all()) if ids else {}
    value_rows = db.execute(
        select(InventoryBalance.warehouse_id,
               func.coalesce(func.sum(InventoryBalance.qty_on_hand * ProductValuation.average_cost), 0),
               func.count(func.distinct(InventoryBalance.product_id)).filter(InventoryBalance.qty_on_hand != 0))
        .join(ProductValuation, ProductValuation.product_id == InventoryBalance.product_id)
        .where(InventoryBalance.warehouse_id.in_(ids)).group_by(InventoryBalance.warehouse_id)
    ).all() if ids else []
    values = {r[0]: (r[1], r[2]) for r in value_rows}
    managers = dict(db.execute(select(User.id, User.full_name).where(
        User.id.in_([w.manager_user_id for w in warehouses if w.manager_user_id]))).all())
    out = []
    for w in warehouses:
        v = values.get(w.id, (Decimal("0"), 0))
        out.append({**{c: getattr(w, c) for c in ("id", "code", "name", "address", "city", "manager_user_id", "is_active", "created_at")},
                    "manager_name": managers.get(w.manager_user_id), "location_count": loc_counts.get(w.id, 0),
                    "stock_value": Decimal(v[0]).quantize(Decimal("0.0001")), "sku_count": v[1]})
    return out


def create_location(db: Session, actor: Actor, warehouse_id: uuid.UUID, data: s.LocationIn) -> StorageLocation:
    wh = _get(db, Warehouse, warehouse_id, "Warehouse")
    actor.require_warehouse(wh.id)
    path = data.code.upper()
    if data.parent_id:
        parent = _get(db, StorageLocation, data.parent_id, "Parent location")
        if parent.warehouse_id != wh.id:
            raise ValidationFailed("Parent location belongs to a different warehouse")
        if parent.location_type == LocationType.TRANSIT:
            raise ValidationFailed("Transit locations cannot have children")
        path = f"{parent.path}/{path}"
    if db.execute(select(StorageLocation.id).where(StorageLocation.warehouse_id == wh.id, StorageLocation.path == path)).first():
        raise Conflict(f"Location '{path}' already exists in this warehouse")
    loc = StorageLocation(warehouse_id=wh.id, parent_id=data.parent_id, code=data.code.upper(), name=data.name,
                          location_type=data.location_type, path=path, created_by=actor.user_id, updated_by=actor.user_id)
    db.add(loc)
    db.flush()
    audit(db, actor, "location.create", "storage_location", loc.id, {"warehouse": wh.code, "path": path})
    return loc


def update_location(db: Session, actor: Actor, location_id: uuid.UUID, data: s.LocationUpdate) -> StorageLocation:
    loc = _get(db, StorageLocation, location_id, "Location")
    actor.require_warehouse(loc.warehouse_id)
    updates = data.model_dump(exclude_unset=True)
    if loc.is_system and updates.get("is_active") is False:
        raise BusinessRuleViolation("System locations cannot be deactivated")
    if updates.get("is_active") is False:
        stock = db.execute(select(func.coalesce(func.sum(InventoryBalance.qty_on_hand), 0))
                           .where(InventoryBalance.location_id == loc.id)).scalar_one()
        if stock != 0:
            raise BusinessRuleViolation("Location still holds stock")
    if updates.get("is_default"):
        if loc.location_type == LocationType.TRANSIT:
            raise ValidationFailed("Transit location cannot be the default")
        for other in db.execute(select(StorageLocation).where(StorageLocation.warehouse_id == loc.warehouse_id,
                                                              StorageLocation.is_default.is_(True))).scalars():
            other.is_default = False
    loc.updated_by = actor.user_id
    audit(db, actor, "location.update", "storage_location", loc.id, diff(loc, updates))
    return loc


def search_products_stmt(q: str | None):
    stmt = select(Product)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Product.name).like(like), func.lower(Product.sku).like(like),
                              func.lower(func.coalesce(Product.internal_code, "")).like(like),
                              func.lower(func.coalesce(Product.barcode, "")).like(like),
                              func.lower(func.coalesce(Product.manufacturer, "")).like(like)))
    return stmt
