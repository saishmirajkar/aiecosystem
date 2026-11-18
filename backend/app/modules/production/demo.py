"""Company X demo scenario (demo installations only), built through the real services."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.deps import Actor
from app.models import InventoryBalance, JobWorkChallan, JobWorkReceipt, ProductionEvent, ProductionOrder
from app.modules.inventory import schemas as inv
from app.modules.inventory import service as inv_svc
from app.modules.master_data import schemas as md
from app.modules.master_data import service as md_svc
from app.modules.production import schemas as s
from app.modules.production import service as svc

D = Decimal


def load_production_demo(db: Session, actor: Actor, products: dict, units: dict, cats: dict, warehouses: dict) -> None:
    now = utcnow()
    plant = svc.create_plant(db, actor, s.PlantIn(code="PLANT-A", name="Plant A · Chakan", city="Chakan"))
    vendors = {}
    for code, name, city in (("SUP-PMW", "Precision Machining Works", "Bhosari"), ("SUP-STP", "Sharp Tools Pvt Ltd", "Talegaon"),
                             ("SUP-CGC", "Coatings & Gears Co", "Pimpri"), ("SUP-TTI", "Thermal Treat Industries", "Chakan")):
        sup = md_svc.create_supplier(db, actor, md.SupplierIn(code=code, name=name, city=city, country="India", lead_time_days=5,
                                                              payment_terms="30 days", currency_code="INR"))
        vendors[code] = svc.create_vendor_site(db, actor, sup.id).warehouse_id

    def product(sku, name, ptype, uom, price, cat):
        p = md_svc.create_product(db, actor, md.ProductCreate(sku=sku, name=name, product_type=ptype, uom_id=units[uom].id,
                                                              category_id=cats[cat].id, purchase_price=D(price), standard_cost=D(price)))
        products[sku] = p
        return p

    product("HSG-BLK-100", "Housing casting blank GB-100", "raw_material", "PCS", "1150", "RM-NONFER")
    product("GR-BLK-100", "Gear blank GB-100", "raw_material", "PCS", "640", "RM-STEEL")
    product("HSG-100", "Housing GB-100 (machined, coated)", "component", "PCS", "0", "FG")
    product("SFT-100", "Output shaft GB-100", "component", "PCS", "0", "FG")
    product("GR-100", "Gear set GB-100 (hobbed, hardened)", "component", "PCS", "0", "FG")

    st = lambda name, perf, site, rate, days: s.RouteStageIn(name=name, performer=perf, site_id=site, rate_per_unit=D(rate), planned_days=days)
    svc.set_structure(db, actor, products["HSG-100"].id, s.StructureIn(
        bom=[s.BomLineIn(component_id=products["HSG-BLK-100"].id, quantity=D(1))],
        route=[st("Fettling & cutting", "plant", None, "40", 1), st("Machining", "vendor", vendors["SUP-PMW"], "650", 5),
               st("Powder coating", "vendor", vendors["SUP-CGC"], "180", 3)]))
    svc.set_structure(db, actor, products["SFT-100"].id, s.StructureIn(
        bom=[s.BomLineIn(component_id=products["STL-RB-32"].id, quantity=D("1.2"))],
        route=[st("Turning & grinding", "vendor", vendors["SUP-PMW"], "420", 5)]))
    svc.set_structure(db, actor, products["GR-100"].id, s.StructureIn(
        bom=[s.BomLineIn(component_id=products["GR-BLK-100"].id, quantity=D(1))],
        route=[st("Blanking", "plant", None, "60", 1), st("Hobbing", "vendor", vendors["SUP-CGC"], "900", 6),
               st("Heat treatment", "vendor", vendors["SUP-TTI"], "300", 3)]))
    svc.set_structure(db, actor, products["FG-GBX-100"].id, s.StructureIn(
        bom=[s.BomLineIn(component_id=products[x].id, quantity=D(q)) for x, q in
             (("HSG-100", "1"), ("SFT-100", "1"), ("GR-100", "1"), ("BRG-6205", "2"), ("FST-HB-M10", "8"))],
        route=[st("Assembly & testing", "plant", None, "500", 3)]))

    # Raw material: receive castings and gear blanks into the main store, then transfer what the plant needs.
    main = warehouses["WH-MAIN"]
    main_loc = inv_svc.default_location(db, main.id)
    inv_svc.post_opening_balances(db, actor, inv.OpeningBalanceIn(reason="Go-live opening balance", lines=[
        inv.InboundLine(product_id=products["HSG-BLK-100"].id, location_id=main_loc.id, quantity=D(60), unit_cost=D(1150)),
        inv.InboundLine(product_id=products["GR-BLK-100"].id, location_id=main_loc.id, quantity=D(60), unit_cost=D(640))]))
    def source(sku: str, qty: Decimal):
        """Pick the location in the main store that holds enough of this item (bar stock sits on rack bins)."""
        bal = db.execute(select(InventoryBalance).where(InventoryBalance.product_id == products[sku].id,
                                                        InventoryBalance.warehouse_id == main.id,
                                                        InventoryBalance.qty_on_hand - InventoryBalance.qty_reserved >= qty)
                         .order_by(InventoryBalance.qty_on_hand.desc())).scalars().first()
        return bal.location_id if bal else None

    t = inv_svc.create_transfer(db, actor, inv.TransferCreate(source_warehouse_id=main.id, destination_warehouse_id=plant.warehouse_id,
                                                              notes="Material for gearbox orders", items=[
        inv.TransferItemIn(product_id=products[x].id, quantity=D(q), source_location_id=source(x, D(q))) for x, q in
        (("HSG-BLK-100", 30), ("GR-BLK-100", 30), ("STL-RB-32", 40), ("BRG-6205", 52), ("FST-HB-M10", 220))]))
    inv_svc.submit_transfer(db, actor, t.id)
    inv_svc.approve_transfer(db, actor, t.id)
    inv_svc.dispatch_transfer(db, actor, t.id)
    inv_svc.receive_transfer(db, actor, t.id)

    shree = svc.create_customer(db, actor, s.CustomerIn(code="SHREE", name="Shree Engineering Pvt Ltd", city="Pune",
                                                        contact_person="Nikhil Joshi", gstin="27AABCS1234F1Z5"))
    apex = svc.create_customer(db, actor, s.CustomerIn(code="APEX", name="Apex Pumps Ltd", city="Nashik", contact_person="R. Deshmukh"))

    def stage(order, sku, name):
        part = next(p for p in order.parts if p.product.sku == sku)
        return next(x for x in part.stages if x.name == name)

    # MO-000002 equivalent first? Keep numbering intuitive: the completed order is MO-000001.
    done = svc.create_order(db, actor, s.OrderCreate(product_id=products["FG-GBX-100"].id, quantity=D(5), plant_id=plant.warehouse_id,
                                                     customer_id=apex.id, start_date=(now - timedelta(days=24)).date(),
                                                     due_date=(now - timedelta(days=8)).date(), notes="Pilot batch for Apex Pumps"))
    svc.release_order(db, actor, done.id)
    db.refresh(done)
    svc.complete_in_house(db, actor, stage(done, "HSG-100", "Fettling & cutting").id, s.CompletionIn(units_completed=D(5)))
    svc.issue_challan(db, actor, stage(done, "HSG-100", "Machining").id, s.ChallanIn(units=D(5)))
    svc.record_receipt(db, actor, stage(done, "HSG-100", "Machining").id, s.ReceiptIn(vendor_site_id=vendors["SUP-PMW"], units_accepted=D(5)))
    svc.issue_challan(db, actor, stage(done, "HSG-100", "Powder coating").id, s.ChallanIn(units=D(5)))
    svc.record_receipt(db, actor, stage(done, "HSG-100", "Powder coating").id, s.ReceiptIn(vendor_site_id=vendors["SUP-CGC"], units_accepted=D(5)))
    svc.issue_challan(db, actor, stage(done, "SFT-100", "Turning & grinding").id, s.ChallanIn(units=D(5)))
    svc.record_receipt(db, actor, stage(done, "SFT-100", "Turning & grinding").id, s.ReceiptIn(vendor_site_id=vendors["SUP-PMW"], units_accepted=D(5)))
    svc.complete_in_house(db, actor, stage(done, "GR-100", "Blanking").id, s.CompletionIn(units_completed=D(5)))
    svc.issue_challan(db, actor, stage(done, "GR-100", "Hobbing").id, s.ChallanIn(units=D(5)))
    svc.record_receipt(db, actor, stage(done, "GR-100", "Hobbing").id, s.ReceiptIn(vendor_site_id=vendors["SUP-CGC"], units_accepted=D(5),
                                                                                   direct_to_next=True))
    svc.record_receipt(db, actor, stage(done, "GR-100", "Heat treatment").id, s.ReceiptIn(vendor_site_id=vendors["SUP-TTI"], units_accepted=D(5)))
    svc.complete_in_house(db, actor, stage(done, "FG-GBX-100", "Assembly & testing").id, s.CompletionIn(units_completed=D(5)))
    t2 = inv_svc.create_transfer(db, actor, inv.TransferCreate(source_warehouse_id=plant.warehouse_id, destination_warehouse_id=main.id,
                                                               notes="Finished gearboxes to store",
                                                               items=[inv.TransferItemIn(product_id=products["FG-GBX-100"].id, quantity=D(5))]))
    inv_svc.submit_transfer(db, actor, t2.id)
    inv_svc.approve_transfer(db, actor, t2.id)
    inv_svc.dispatch_transfer(db, actor, t2.id)
    inv_svc.receive_transfer(db, actor, t2.id)
    dn = svc.create_dispatch(db, actor, s.DispatchCreate(customer_id=apex.id, warehouse_id=main.id, order_id=done.id, vehicle_number="MH14 GT 4521",
                                                        transporter="Deccan Roadways", customer_reference="APX/PO/2611",
                                                        lines=[s.DispatchLineIn(product_id=products["FG-GBX-100"].id, quantity=D(5))]))
    svc.dispatch(db, actor, dn.id)

    # MO-000002: 20 gearboxes for Shree Engineering, mid-flight.
    live = svc.create_order(db, actor, s.OrderCreate(product_id=products["FG-GBX-100"].id, quantity=D(20), plant_id=plant.warehouse_id,
                                                     customer_id=shree.id, start_date=(now - timedelta(days=9)).date(),
                                                     due_date=(now + timedelta(days=5)).date(), notes="Customer PO SE/4417"))
    svc.release_order(db, actor, live.id)
    db.refresh(live)
    svc.complete_in_house(db, actor, stage(live, "HSG-100", "Fettling & cutting").id, s.CompletionIn(units_completed=D(20)))
    m = stage(live, "HSG-100", "Machining").id
    svc.issue_challan(db, actor, m, s.ChallanIn(units=D(12), vehicle_number="MH12 KX 2210"))
    svc.issue_challan(db, actor, m, s.ChallanIn(units=D(8), vendor_site_id=vendors["SUP-STP"], notes="Split to meet the due date"))
    svc.record_receipt(db, actor, m, s.ReceiptIn(vendor_site_id=vendors["SUP-PMW"], units_accepted=D(10), units_rework=D(1),
                                                 vendor_document_number="PMW/DC/889", notes="1 piece back for re-boring"))
    svc.record_receipt(db, actor, m, s.ReceiptIn(vendor_site_id=vendors["SUP-STP"], units_accepted=D(8), direct_to_next=True,
                                                 vendor_document_number="STP/1043"))
    c = stage(live, "HSG-100", "Powder coating").id
    svc.issue_challan(db, actor, c, s.ChallanIn(units=D(10)))
    svc.record_receipt(db, actor, c, s.ReceiptIn(vendor_site_id=vendors["SUP-CGC"], units_accepted=D(12)))
    sh = stage(live, "SFT-100", "Turning & grinding").id
    svc.issue_challan(db, actor, sh, s.ChallanIn(units=D(20)))
    svc.record_receipt(db, actor, sh, s.ReceiptIn(vendor_site_id=vendors["SUP-PMW"], units_accepted=D(20)))
    svc.complete_in_house(db, actor, stage(live, "GR-100", "Blanking").id, s.CompletionIn(units_completed=D(20)))
    h = stage(live, "GR-100", "Hobbing").id
    svc.issue_challan(db, actor, h, s.ChallanIn(units=D(20)))
    svc.record_receipt(db, actor, h, s.ReceiptIn(vendor_site_id=vendors["SUP-CGC"], units_accepted=D(18), units_scrapped=D(1),
                                                 scrap_reason="Tooth profile out of tolerance", direct_to_next=True))

    # MO-000003: planned, not yet released.
    svc.create_order(db, actor, s.OrderCreate(product_id=products["FG-GBX-100"].id, quantity=D(10), plant_id=plant.warehouse_id,
                                              customer_id=shree.id, start_date=(now + timedelta(days=6)).date(),
                                              due_date=(now + timedelta(days=24)).date(), notes="Call-off 2 of 3"))
    db.flush()
    _backdate(db, done, now - timedelta(days=24), now - timedelta(days=9))
    _backdate(db, live, now - timedelta(days=9), now - timedelta(hours=3))


def _backdate(db: Session, order: ProductionOrder, start, end) -> None:
    """Spread the demo order's activity over a realistic period (stages and timeline only; the ledger stays as posted)."""
    events = list(db.execute(select(ProductionEvent).where(ProductionEvent.order_id == order.id).order_by(ProductionEvent.created_at)).scalars())
    step = (end - start) / max(len(events), 1)
    for i, e in enumerate(events):
        e.created_at = start + step * i
    order.released_at = start + timedelta(hours=2)
    if order.completed_at:
        order.completed_at = end
    # Documents carry the date of the activity that created them.
    for ch in db.execute(select(JobWorkChallan).where(JobWorkChallan.order_id == order.id)).scalars():
        ev = next((e for e in events if e.message.endswith(f"({ch.challan_number})")), None)
        if ev:
            ch.challan_date = ev.created_at.date()
    for rc in db.execute(select(JobWorkReceipt).where(JobWorkReceipt.order_id == order.id)).scalars():
        ev = next((e for e in events if e.message.endswith(f"({rc.receipt_number})")), None)
        if ev:
            rc.received_date = ev.created_at.date()
    for part in order.parts:
        for st in part.stages:
            evs = [e.created_at for e in events if e.stage_id == st.id]
            if evs:
                st.actual_start = min(evs)
                if st.actual_end:
                    st.actual_end = max(evs)
    db.flush()
