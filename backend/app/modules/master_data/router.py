"""Master data HTTP endpoints."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import exists, or_, select

from app.core.common import Page, apply_sort, paginate
from app.core.deps import DB, Actor, require_permission
from app.core.errors import NotFound
from app.models import (
    InventoryBalance, Product, ProductCategory, ProductSupplier, ProductType, StorageLocation, Supplier,
    UnitConversion, UnitOfMeasure, Warehouse,
)
from app.modules.master_data import schemas as s
from app.modules.master_data import service as svc

router = APIRouter()
VIEW_INV = require_permission("inventory.view")
MANAGE_MD = require_permission("masterdata.manage")


# ---------------------------------------------------------------- units
@router.get("/units", response_model=list[s.UnitOut], tags=["master-data"])
def list_units(db: DB, _: Actor = Depends(VIEW_INV), include_inactive: bool = False):
    stmt = select(UnitOfMeasure).order_by(UnitOfMeasure.category, UnitOfMeasure.code)
    if not include_inactive:
        stmt = stmt.where(UnitOfMeasure.is_active.is_(True))
    return db.execute(stmt).scalars().all()


@router.post("/units", response_model=s.UnitOut, status_code=201, tags=["master-data"])
def create_unit(data: s.UnitIn, db: DB, actor: Actor = Depends(MANAGE_MD)):
    u = svc.create_unit(db, actor, data)
    db.commit()
    return u


@router.patch("/units/{unit_id}", response_model=s.UnitOut, tags=["master-data"])
def update_unit(unit_id: uuid.UUID, data: s.UnitUpdate, db: DB, actor: Actor = Depends(MANAGE_MD)):
    u = svc.update_unit(db, actor, unit_id, data)
    db.commit()
    return u


@router.get("/unit-conversions", response_model=list[s.ConversionOut], tags=["master-data"])
def list_conversions(db: DB, _: Actor = Depends(VIEW_INV), product_id: uuid.UUID | None = None):
    stmt = select(UnitConversion)
    if product_id:
        stmt = stmt.where(or_(UnitConversion.product_id == product_id, UnitConversion.product_id.is_(None)))
    return [svc.conversion_out(c) for c in db.execute(stmt).scalars()]


@router.post("/unit-conversions", response_model=s.ConversionOut, status_code=201, tags=["master-data"])
def create_conversion(data: s.ConversionIn, db: DB, actor: Actor = Depends(MANAGE_MD)):
    c = svc.create_conversion(db, actor, data)
    db.commit()
    return svc.conversion_out(c)


# ---------------------------------------------------------------- categories
@router.get("/categories", response_model=list[s.CategoryOut], tags=["master-data"])
def list_categories(db: DB, _: Actor = Depends(VIEW_INV), include_inactive: bool = False):
    stmt = select(ProductCategory).order_by(ProductCategory.name)
    if not include_inactive:
        stmt = stmt.where(ProductCategory.is_active.is_(True))
    return [svc.category_out(db, c) for c in db.execute(stmt).scalars()]


@router.post("/categories", response_model=s.CategoryOut, status_code=201, tags=["master-data"])
def create_category(data: s.CategoryIn, db: DB, actor: Actor = Depends(MANAGE_MD)):
    c = svc.create_category(db, actor, data)
    db.commit()
    return svc.category_out(db, c)


@router.patch("/categories/{cat_id}", response_model=s.CategoryOut, tags=["master-data"])
def update_category(cat_id: uuid.UUID, data: s.CategoryUpdate, db: DB, actor: Actor = Depends(MANAGE_MD)):
    c = svc.update_category(db, actor, cat_id, data)
    db.commit()
    return svc.category_out(db, c)


# ---------------------------------------------------------------- suppliers
@router.get("/suppliers", response_model=Page[s.SupplierOut], tags=["suppliers"])
def list_suppliers(db: DB, _: Actor = Depends(require_permission("purchasing.view")), q: str | None = None,
                   is_active: bool | None = True, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
                   sort: str | None = None):
    stmt = select(Supplier)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Supplier.name.ilike(like), Supplier.code.ilike(like), Supplier.contact_person.ilike(like)))
    if is_active is not None:
        stmt = stmt.where(Supplier.is_active.is_(is_active))
    stmt = apply_sort(stmt, sort, {"name": Supplier.name, "code": Supplier.code, "created_at": Supplier.created_at}, Supplier.name)
    return paginate(db, stmt, page, page_size)


@router.post("/suppliers", response_model=s.SupplierOut, status_code=201, tags=["suppliers"])
def create_supplier(data: s.SupplierIn, db: DB, actor: Actor = Depends(require_permission("suppliers.manage"))):
    sup = svc.create_supplier(db, actor, data)
    db.commit()
    return sup


@router.get("/suppliers/{sup_id}", response_model=s.SupplierOut, tags=["suppliers"])
def get_supplier(sup_id: uuid.UUID, db: DB, _: Actor = Depends(require_permission("purchasing.view"))):
    sup = db.get(Supplier, sup_id)
    if sup is None:
        raise NotFound("Supplier not found")
    return sup


@router.patch("/suppliers/{sup_id}", response_model=s.SupplierOut, tags=["suppliers"])
def update_supplier(sup_id: uuid.UUID, data: s.SupplierUpdate, db: DB, actor: Actor = Depends(require_permission("suppliers.manage"))):
    sup = svc.update_supplier(db, actor, sup_id, data)
    db.commit()
    return sup


# ---------------------------------------------------------------- products
@router.get("/products", response_model=Page[s.ProductOut], tags=["products"])
def list_products(db: DB, actor: Actor = Depends(VIEW_INV), q: str | None = None, category_id: uuid.UUID | None = None,
                  product_type: ProductType | None = None, is_active: bool | None = True,
                  supplier_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None,
                  page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200), sort: str | None = None):
    stmt = svc.search_products_stmt(q)
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if product_type:
        stmt = stmt.where(Product.product_type == product_type)
    if is_active is not None:
        stmt = stmt.where(Product.is_active.is_(is_active))
    if supplier_id:
        stmt = stmt.where(exists().where(ProductSupplier.product_id == Product.id, ProductSupplier.supplier_id == supplier_id))
    if warehouse_id:
        stmt = stmt.where(exists().where(InventoryBalance.product_id == Product.id, InventoryBalance.warehouse_id == warehouse_id,
                                         InventoryBalance.qty_on_hand != 0))
    stmt = apply_sort(stmt, sort, {"name": Product.name, "sku": Product.sku, "created_at": Product.created_at,
                                   "updated_at": Product.updated_at, "reorder_level": Product.reorder_level}, Product.name)
    result = paginate(db, stmt, page, page_size)
    result["items"] = svc.products_out(db, result["items"], actor)
    return result


@router.post("/products", response_model=s.ProductOut, status_code=201, tags=["products"])
def create_product(data: s.ProductCreate, db: DB, actor: Actor = Depends(require_permission("products.create"))):
    p = svc.create_product(db, actor, data)
    db.commit()
    return svc.products_out(db, [p], actor)[0]


@router.get("/products/{product_id}", response_model=s.ProductOut, tags=["products"])
def get_product(product_id: uuid.UUID, db: DB, actor: Actor = Depends(VIEW_INV)):
    p = db.get(Product, product_id)
    if p is None:
        raise NotFound("Product not found")
    return svc.products_out(db, [p], actor)[0]


@router.patch("/products/{product_id}", response_model=s.ProductOut, tags=["products"])
def update_product(product_id: uuid.UUID, data: s.ProductUpdate, db: DB, actor: Actor = Depends(require_permission("products.edit"))):
    p = svc.update_product(db, actor, product_id, data)
    db.commit()
    return svc.products_out(db, [p], actor)[0]


@router.get("/products/{product_id}/suppliers", response_model=list[s.ProductSupplierOut], tags=["products"])
def get_product_suppliers(product_id: uuid.UUID, db: DB, _: Actor = Depends(VIEW_INV)):
    return svc.list_product_suppliers(db, product_id)


@router.put("/products/{product_id}/suppliers", response_model=list[s.ProductSupplierOut], tags=["products"])
def put_product_suppliers(product_id: uuid.UUID, data: list[s.ProductSupplierIn], db: DB,
                          actor: Actor = Depends(require_permission("products.edit"))):
    svc.set_product_suppliers(db, actor, product_id, data)
    db.commit()
    return svc.list_product_suppliers(db, product_id)


# ---------------------------------------------------------------- warehouses & locations
@router.get("/warehouses", response_model=list[s.WarehouseOut], tags=["warehouses"])
def list_warehouses(db: DB, actor: Actor = Depends(VIEW_INV), include_inactive: bool = False):
    stmt = select(Warehouse).order_by(Warehouse.code)
    if not include_inactive:
        stmt = stmt.where(Warehouse.is_active.is_(True))
    if actor.warehouse_ids is not None:
        stmt = stmt.where(Warehouse.id.in_(actor.warehouse_ids))
    return svc.warehouses_out(db, list(db.execute(stmt).scalars()))


@router.post("/warehouses", response_model=s.WarehouseOut, status_code=201, tags=["warehouses"])
def create_warehouse(data: s.WarehouseIn, db: DB, actor: Actor = Depends(require_permission("warehouses.manage"))):
    wh = svc.create_warehouse(db, actor, data)
    db.commit()
    return svc.warehouses_out(db, [wh])[0]


@router.get("/warehouses/{wh_id}", response_model=s.WarehouseOut, tags=["warehouses"])
def get_warehouse(wh_id: uuid.UUID, db: DB, actor: Actor = Depends(VIEW_INV)):
    wh = db.get(Warehouse, wh_id)
    if wh is None or not actor.can_access_warehouse(wh.id):
        raise NotFound("Warehouse not found")
    return svc.warehouses_out(db, [wh])[0]


@router.patch("/warehouses/{wh_id}", response_model=s.WarehouseOut, tags=["warehouses"])
def update_warehouse(wh_id: uuid.UUID, data: s.WarehouseUpdate, db: DB, actor: Actor = Depends(require_permission("warehouses.manage"))):
    actor.require_warehouse(wh_id)
    wh = svc.update_warehouse(db, actor, wh_id, data)
    db.commit()
    return svc.warehouses_out(db, [wh])[0]


@router.get("/warehouses/{wh_id}/locations", response_model=list[s.LocationOut], tags=["warehouses"])
def list_locations(wh_id: uuid.UUID, db: DB, actor: Actor = Depends(VIEW_INV), include_inactive: bool = False,
                   include_system: bool = True):
    if not actor.can_access_warehouse(wh_id):
        raise NotFound("Warehouse not found")
    stmt = select(StorageLocation).where(StorageLocation.warehouse_id == wh_id).order_by(StorageLocation.path)
    if not include_inactive:
        stmt = stmt.where(StorageLocation.is_active.is_(True))
    if not include_system:
        stmt = stmt.where(StorageLocation.location_type != "transit")
    return db.execute(stmt).scalars().all()


@router.post("/warehouses/{wh_id}/locations", response_model=s.LocationOut, status_code=201, tags=["warehouses"])
def create_location(wh_id: uuid.UUID, data: s.LocationIn, db: DB, actor: Actor = Depends(require_permission("warehouses.manage"))):
    loc = svc.create_location(db, actor, wh_id, data)
    db.commit()
    return loc


@router.patch("/locations/{location_id}", response_model=s.LocationOut, tags=["warehouses"])
def update_location(location_id: uuid.UUID, data: s.LocationUpdate, db: DB, actor: Actor = Depends(require_permission("warehouses.manage"))):
    loc = svc.update_location(db, actor, location_id, data)
    db.commit()
    return loc
