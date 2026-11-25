"""Synthetic manufacturing demo data — ONLY for clearly identified demo/evaluation installations.

Everything is created through the real business services (so ledger, valuation and audit are consistent).
The installation is flagged with the `system.demo_data` setting, which the UI shows as a banner.
"""
from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.deps import Actor, build_actor
from app.core.security import hash_password
from app.models import (
    Department, MovementType, Role, StockStatus, SystemSetting, UnitOfMeasure, User, UserDepartment,
)
from app.modules.inventory import schemas as inv
from app.modules.inventory import service as inv_svc
from app.modules.inventory.ledger import Line, Posting, get_or_create_batch, post
from app.modules.master_data import schemas as md
from app.modules.master_data import service as md_svc
from app.modules.purchasing import schemas as pur
from app.modules.purchasing import service as pur_svc

DEMO_PASSWORD = "Demo-Passw0rd!"

USERS = [
    ("ops@demo.example.com", "Rohan Mehta", "operations_manager", "MGMT"),
    ("inventory@demo.example.com", "Priya Nair", "inventory_manager", "INV"),
    ("warehouse@demo.example.com", "Arjun Singh", "warehouse_manager", "WH"),
    ("purchase@demo.example.com", "Kavita Rao", "purchase_manager", "PROC"),
    ("staff@demo.example.com", "Vikram Patil", "inventory_staff", "WH"),
    ("viewer@demo.example.com", "Meera Iyer", "read_only", "FIN"),
]

CATEGORIES = [("RM-STEEL", "Steel & Alloys"), ("RM-NONFER", "Non-ferrous Metals"), ("FAST", "Fasteners"),
              ("BEAR", "Bearings & Seals"), ("HYD", "Hydraulics & Lubricants"), ("CONS", "Shop Consumables"),
              ("ELEC", "Electrical Components"), ("FG", "Finished Assemblies")]

SUPPLIERS = [("SUP-BST", "Bharat Steel Traders", "Pune", 7, "30 days"), ("SUP-SKF", "Precision Bearings Co.", "Chennai", 10, "45 days"),
             ("SUP-FST", "Allied Fasteners Pvt Ltd", "Ludhiana", 5, "30 days"),
             ("SUP-HYD", "HydroLube Industrial", "Ahmedabad", 6, "15 days"), ("SUP-ELC", "Volt Components", "Bengaluru", 12, "45 days")]

# sku, name, category, type, uom, price, min, reorder, max, reorder_qty, supplier, extra
PRODUCTS = [
    ("STL-RB-20", "Steel round bar EN8 20mm", "RM-STEEL", "raw_material", "KG", "82", "400", "800", "6000", "2000", "SUP-BST", {}),
    ("STL-RB-32", "Steel round bar EN8 32mm", "RM-STEEL", "raw_material", "KG", "84", "300", "600", "5000", "1500", "SUP-BST", {}),
    ("STL-PL-06", "MS plate 6mm", "RM-STEEL", "raw_material", "KG", "71", "500", "1000", "8000", "2500", "SUP-BST", {}),
    ("STL-PL-12", "MS plate 12mm", "RM-STEEL", "raw_material", "KG", "73", "300", "700", "6000", "2000", "SUP-BST", {}),
    ("SS-SH-304", "Stainless sheet SS304 2mm", "RM-STEEL", "raw_material", "KG", "245", "100", "250", "1500", "500", "SUP-BST", {}),
    ("AL-BL-6061", "Aluminium billet 6061", "RM-NONFER", "raw_material", "KG", "310", "80", "200", "1200", "400", "SUP-BST", {}),
    ("BR-RD-15", "Brass rod 15mm", "RM-NONFER", "raw_material", "KG", "520", "40", "100", "600", "200", "SUP-BST", {}),
    ("FST-HB-M10", "Hex bolt M10x40 Gr 8.8", "FAST", "component", "PCS", "6.5", "1000", "2500", "20000", "10000", "SUP-FST", {}),
    ("FST-HB-M12", "Hex bolt M12x50 Gr 8.8", "FAST", "component", "PCS", "9.2", "800", "2000", "15000", "8000", "SUP-FST", {}),
    ("FST-NT-M10", "Hex nut M10", "FAST", "component", "PCS", "1.8", "2000", "4000", "30000", "15000", "SUP-FST", {}),
    ("FST-WS-M10", "Plain washer M10", "FAST", "component", "PCS", "0.6", "2000", "4000", "30000", "15000", "SUP-FST", {}),
    ("BRG-6205", "Deep groove ball bearing 6205-2RS", "BEAR", "component", "PCS", "185", "40", "80", "600", "200", "SUP-SKF", {}),
    ("BRG-6308", "Deep groove ball bearing 6308-ZZ", "BEAR", "spare_part", "PCS", "640", "10", "25", "150", "50", "SUP-SKF", {}),
    ("SEAL-OR-50", "O-ring NBR 50x3", "BEAR", "spare_part", "PCS", "12", "200", "400", "3000", "1000", "SUP-SKF", {}),
    ("HYD-OIL-68", "Hydraulic oil ISO VG 68", "HYD", "consumable", "L", "165", "200", "400", "2500", "1000", "SUP-HYD",
     {"track_batches": True, "track_expiry": True}),
    ("COOL-SOL", "Soluble cutting coolant", "HYD", "consumable", "L", "210", "100", "200", "1500", "600", "SUP-HYD",
     {"track_batches": True, "track_expiry": True}),
    ("GRS-EP2", "EP2 lithium grease", "HYD", "consumable", "KG", "290", "30", "60", "400", "120", "SUP-HYD", {}),
    ("CON-GW-4", "Grinding wheel 4in", "CONS", "consumable", "PCS", "95", "50", "120", "800", "300", "SUP-FST", {}),
    ("CON-WR-1", "MIG welding wire 1.0mm", "CONS", "consumable", "KG", "185", "60", "150", "1000", "300", "SUP-FST", {}),
    ("CON-GLV", "Cut-resistant gloves", "CONS", "consumable", "PCS", "75", "100", "200", "1500", "500", "SUP-FST", {}),
    ("ELC-MTR-2", "Induction motor 2HP", "ELEC", "component", "PCS", "14500", "4", "8", "40", "10", "SUP-ELC", {"track_serials": True}),
    ("ELC-CBL-4", "Copper cable 4 sq mm", "ELEC", "component", "M", "62", "200", "400", "3000", "1000", "SUP-ELC", {}),
    ("ELC-PRX-12", "Proximity sensor M12", "ELEC", "spare_part", "PCS", "1150", "10", "20", "150", "40", "SUP-ELC", {}),
    ("FG-GBX-100", "Gearbox assembly GB-100", "FG", "finished_good", "PCS", "38500", "2", "5", "40", "10", None, {"selling_price": "52000"}),
    ("FG-PMP-50", "Centrifugal pump assembly P-50", "FG", "finished_good", "PCS", "21500", "3", "6", "50", "12", None, {"selling_price": "29900"}),
]

# warehouse, sku → opening qty
OPENING = {
    "WH-MAIN": {"STL-RB-20": 3200, "STL-RB-32": 2100, "STL-PL-06": 900, "STL-PL-12": 2600, "SS-SH-304": 420, "AL-BL-6061": 160,
                "BR-RD-15": 210, "FST-HB-M10": 9000, "FST-HB-M12": 1500, "FST-NT-M10": 14000, "FST-WS-M10": 12000, "BRG-6205": 140,
                "BRG-6308": 18, "SEAL-OR-50": 1300, "GRS-EP2": 95, "CON-GW-4": 260, "CON-WR-1": 120, "CON-GLV": 600,
                "ELC-CBL-4": 1800, "ELC-PRX-12": 14, "FG-GBX-100": 9, "FG-PMP-50": 14},
    "WH-ASSY": {"FST-HB-M10": 2500, "FST-NT-M10": 3000, "BRG-6205": 60, "SEAL-OR-50": 300, "ELC-CBL-4": 450, "CON-GLV": 150},
    "WH-SPARE": {"BRG-6308": 9, "SEAL-OR-50": 400, "ELC-PRX-12": 6, "GRS-EP2": 20, "CON-GW-4": 90},
}

CONSUMPTION = ["STL-RB-20", "STL-RB-32", "STL-PL-06", "STL-PL-12", "FST-HB-M10", "FST-NT-M10", "FST-WS-M10", "BRG-6205",
               "CON-GW-4", "CON-WR-1", "CON-GLV", "ELC-CBL-4", "SS-SH-304"]


def load_demo_data(db: Session, admin: User) -> None:
    rnd = random.Random(42)
    actor: Actor = build_actor(db, admin)
    now = utcnow()

    depts = {d.code: d for d in db.execute(select(Department)).scalars()}
    roles = {r.code: r for r in db.execute(select(Role)).scalars()}
    for email, name, role, dept in USERS:
        u = User(email=email, full_name=name, password_hash=hash_password(DEMO_PASSWORD), created_by=admin.id)
        u.roles = [roles[role]]
        u.departments = [UserDepartment(department_id=depts[dept].id, is_primary=True)]
        db.add(u)
    db.flush()

    cats = {code: md_svc.create_category(db, actor, md.CategoryIn(code=code, name=name)) for code, name in CATEGORIES}
    sups = {code: md_svc.create_supplier(db, actor, md.SupplierIn(code=code, name=name, city=city, country="India",
                                                                  lead_time_days=lt, payment_terms=terms, currency_code="INR"))
            for code, name, city, lt, terms in SUPPLIERS}
    units = {u.code: u for u in db.execute(select(UnitOfMeasure)).scalars()}

    whs = {}
    for code, name, city in (("WH-MAIN", "Main Plant Store", "Pune"), ("WH-ASSY", "Assembly Line Store", "Pune"),
                             ("WH-SPARE", "Maintenance Spares Store", "Chakan")):
        whs[code] = md_svc.create_warehouse(db, actor, md.WarehouseIn(code=code, name=name, city=city))
    racks = {}
    for code in ("A", "B"):
        sec = md_svc.create_location(db, actor, whs["WH-MAIN"].id, md.LocationIn(code=code, name=f"Section {code}", location_type="section"))
        for r in ("R01", "R02", "R04"):
            rack = md_svc.create_location(db, actor, whs["WH-MAIN"].id, md.LocationIn(code=r, location_type="rack", parent_id=sec.id))
            for b in ("B01", "B12"):
                racks[f"{code}/{r}/{b}"] = md_svc.create_location(db, actor, whs["WH-MAIN"].id,
                                                                  md.LocationIn(code=b, location_type="bin", parent_id=rack.id))
    main_loc = {code: inv_svc.default_location(db, wh.id) for code, wh in whs.items()}

    products = {}
    for sku, name, cat, ptype, uom, price, mn, ro, mx, rq, sup, extra in PRODUCTS:
        p = md_svc.create_product(db, actor, md.ProductCreate(
            sku=sku, name=name, category_id=cats[cat].id, product_type=ptype, uom_id=units[uom].id,
            purchase_price=Decimal(price), standard_cost=Decimal(price), min_stock_level=Decimal(mn), reorder_level=Decimal(ro),
            max_stock_level=Decimal(mx), reorder_quantity=Decimal(rq), manufacturer=None, **extra))
        products[sku] = p
        if sup:
            md_svc.set_product_suppliers(db, actor, p.id, [md.ProductSupplierIn(supplier_id=sups[sup].id, is_preferred=True,
                                                                                lead_time_days=sups[sup].lead_time_days,
                                                                                last_purchase_price=Decimal(price))])
    db.flush()

    # Opening balances 40 days ago (steel bars binned on racks to show exact locations)
    t0 = now - timedelta(days=40)
    for wcode, items in OPENING.items():
        lines = []
        for sku, qty in items.items():
            loc = main_loc[wcode]
            if wcode == "WH-MAIN" and sku.startswith("STL-RB"):
                loc = racks["B/R04/B12" if sku == "STL-RB-20" else "B/R04/B01"]
            lines.append(Line(product_id=products[sku].id, location_id=loc.id, quantity=Decimal(qty),
                              movement_type=MovementType.OPENING, unit_cost=products[sku].purchase_price))
        post(db, actor, Posting(MovementType.OPENING, lines, reference_type="opening_balance", reason="Go-live opening balance",
                                effective_at=t0))
    for sku, batch, qty, exp_days in (("HYD-OIL-68", "HO-2609", 260, 20), ("HYD-OIL-68", "HO-2611", 420, 300),
                                      ("COOL-SOL", "CS-2610", 180, 200)):
        b = get_or_create_batch(db, products[sku], batch, expiry_date=(now + timedelta(days=exp_days)).date(),
                                supplier_id=sups["SUP-HYD"].id)
        post(db, actor, Posting(MovementType.OPENING, [Line(product_id=products[sku].id, location_id=main_loc["WH-MAIN"].id,
                                                            quantity=Decimal(qty), batch_id=b.id, movement_type=MovementType.OPENING,
                                                            unit_cost=products[sku].purchase_price)],
                                reference_type="opening_balance", reason="Go-live opening balance", effective_at=t0))
    post(db, actor, Posting(MovementType.OPENING, [Line(product_id=products["ELC-MTR-2"].id, location_id=main_loc["WH-MAIN"].id,
                                                        quantity=Decimal(6), movement_type=MovementType.OPENING,
                                                        unit_cost=Decimal("14500"),
                                                        serial_numbers=[f"MTR2-24{n:04d}" for n in range(101, 107)])],
                            reference_type="opening_balance", reason="Go-live opening balance", effective_at=t0))

    # 30 days of production consumption (back-dated issues) for trends and history
    for day in range(30, 0, -1):
        when = (now - timedelta(days=day)).replace(hour=rnd.randint(6, 15), minute=rnd.randint(0, 59))
        picks = rnd.sample(CONSUMPTION, 4)
        lines = []
        for sku in picks:
            p = products[sku]
            loc_id = (racks["B/R04/B12"].id if sku == "STL-RB-20" else racks["B/R04/B01"].id if sku == "STL-RB-32"
                      else main_loc["WH-MAIN"].id)
            on_hand = inv_svc._balance_qty(db, p.id, loc_id, None, StockStatus.UNRESTRICTED)
            base = Decimal(p.reorder_quantity) / Decimal(rnd.choice([18, 22, 26]))
            qty = min(base, on_hand * Decimal("0.06")).quantize(Decimal(1))
            if qty >= 1:
                lines.append(Line(product_id=p.id, location_id=loc_id, quantity=-qty, movement_type=MovementType.ISSUE))
        if not lines:
            continue
        post(db, actor, Posting(MovementType.ISSUE, lines, reference_type="stock_issue", reference_number=f"WO-{2400 + day}",
                                reason="Issued to production", effective_at=when))

    def make_po(sup: str, items: list[tuple[str, int]], expected_days: int, approve: bool = True, send: bool = True, days_ago: int = 10):
        po = pur_svc.create_po(db, actor, pur.POCreate(
            supplier_id=sups[sup].id, delivery_warehouse_id=whs["WH-MAIN"].id,
            order_date=(now - timedelta(days=days_ago)).date(), expected_delivery_date=(now + timedelta(days=expected_days)).date(),
            items=[pur.POItemIn(product_id=products[s].id, quantity_ordered=Decimal(q),
                                unit_price=products[s].purchase_price * Decimal("1.02"), tax_rate=Decimal("18")) for s, q in items]))
        if approve:
            pur_svc.submit_po(db, actor, po.id)
            pur_svc.decide_po(db, actor, po.id, True)
            if send:
                pur_svc.send_po(db, actor, po.id)
        return po

    # Received today (partially) — "what came in today?"
    po1 = make_po("SUP-BST", [("STL-PL-06", 2500), ("STL-RB-32", 1500)], -1, days_ago=8)
    g1 = pur_svc.create_grn(db, actor, pur.GRNCreate(purchase_order_id=po1.id, delivery_note_number="BST/DN/7781", items=[
        pur.GRNItemIn(purchase_order_item_id=po1.items[0].id, quantity_received=Decimal(1800), quantity_damaged=Decimal(20)),
    ]))
    pur_svc.confirm_grn(db, actor, g1.id)
    po2 = make_po("SUP-FST", [("FST-HB-M12", 8000), ("FST-WS-M10", 15000)], 2, days_ago=4)
    g2 = pur_svc.create_grn(db, actor, pur.GRNCreate(purchase_order_id=po2.id, delivery_note_number="AF/2611/112", items=[
        pur.GRNItemIn(purchase_order_item_id=po2.items[0].id, quantity_received=Decimal(8000)),
        pur.GRNItemIn(purchase_order_item_id=po2.items[1].id, quantity_received=Decimal(14800), quantity_rejected=Decimal(200),
                      rejection_reason="Rusted washers"),
    ]))
    pur_svc.confirm_grn(db, actor, g2.id)
    # Overdue and upcoming deliveries
    make_po("SUP-SKF", [("BRG-6308", 50), ("SEAL-OR-50", 1000)], -5, days_ago=20)
    make_po("SUP-ELC", [("ELC-PRX-12", 40)], 6, days_ago=3)
    make_po("SUP-HYD", [("GRS-EP2", 120)], 9, approve=False)
    po_pending = make_po("SUP-BST", [("SS-SH-304", 500)], 12, approve=False)
    pur_svc.submit_po(db, actor, po_pending.id)
    # Draft GRN awaiting confirmation
    po3 = make_po("SUP-ELC", [("ELC-CBL-4", 1000)], 1, days_ago=6)
    pur_svc.create_grn(db, actor, pur.GRNCreate(purchase_order_id=po3.id, delivery_note_number="VC/8812", items=[
        pur.GRNItemIn(purchase_order_item_id=po3.items[0].id, quantity_received=Decimal(1000))]))

    # Requisition awaiting approval
    pur_svc.create_requisition(db, actor, pur.RequisitionCreate(
        justification="Coolant consumption up on new CNC line", submit=True,
        items=[pur.RequisitionItemIn(product_id=products["COOL-SOL"].id, quantity=Decimal(600),
                                     preferred_supplier_id=sups["SUP-HYD"].id)]))

    # Transfers: one in transit (dispatched 5 days ago → unconfirmed alert), one pending approval
    t1 = inv_svc.create_transfer(db, actor, inv.TransferCreate(source_warehouse_id=whs["WH-MAIN"].id,
                                                               destination_warehouse_id=whs["WH-ASSY"].id,
                                                               items=[inv.TransferItemIn(product_id=products["FST-HB-M10"].id, quantity=Decimal(1500))]))
    inv_svc.submit_transfer(db, actor, t1.id)
    inv_svc.approve_transfer(db, actor, t1.id)
    inv_svc.dispatch_transfer(db, actor, t1.id)
    t1.dispatched_at = now - timedelta(days=5)
    t2 = inv_svc.create_transfer(db, actor, inv.TransferCreate(source_warehouse_id=whs["WH-MAIN"].id,
                                                               destination_warehouse_id=whs["WH-SPARE"].id,
                                                               items=[inv.TransferItemIn(product_id=products["BRG-6205"].id, quantity=Decimal(20))]))
    inv_svc.submit_transfer(db, actor, t2.id)

    # Cycle count with a variance awaiting approval; a reservation for a work order
    nuts_on_hand = inv_svc._balance_qty(db, products["FST-NT-M10"].id, main_loc["WH-MAIN"].id, None, StockStatus.UNRESTRICTED)
    inv_svc.create_adjustment(db, actor, inv.AdjustmentCreate(
        adjustment_type="reconciliation", warehouse_id=whs["WH-MAIN"].id, reason="Monthly cycle count — fasteners", submit=True,
        items=[inv.AdjustmentItemIn(product_id=products["FST-NT-M10"].id, location_id=main_loc["WH-MAIN"].id,
                                    counted_quantity=nuts_on_hand - Decimal(140))]))
    inv_svc.create_reservation(db, actor, inv.ReservationIn(product_id=products["BRG-6205"].id, warehouse_id=whs["WH-MAIN"].id,
                                                            quantity=Decimal(24), reference_type="work_order", reference_number="WO-2611"))

    # client/company-x: job-work production scenario on top of the foundation demo
    from app.modules.production.demo import load_production_demo
    load_production_demo(db, actor, products, units, cats, whs)
    from app.modules.workforce.demo import load_workforce_demo  # workforce module: people, rotas, leave, overtime
    load_workforce_demo(db, admin, DEMO_PASSWORD)

    db.add(SystemSetting(key="system.demo_data", value=True, description="Installation contains synthetic demo data"))
    db.flush()
