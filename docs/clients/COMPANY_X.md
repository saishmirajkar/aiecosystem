# Company X — production & job-work flow (branch `client/company-x`)

`main` is the foundation. This branch adds Company X's flow on top of it. Foundation fixes are made
on `main` and merged into this branch; Company X code lives in new modules so that merges stay clean.

## The flow
```
Supplier ──GRN──▶ Warehouse ──transfer──▶ Plant A ──┬─ in-house stage(s)
                                                   ├─ challan ─▶ Vendor A ─┬─ return to plant
                                                   │                       └─ direct ─▶ Vendor B ─▶ …
                                                   └─ split lots across vendors (e.g. 12 to A, 8 to C)
Parts finished ─▶ final assembly at plant ─▶ transfer FG to warehouse ─▶ dispatch note ─▶ customer
```

## Decisions (agreed in the architecture review)
| # | Decision |
|---|---|
| 1 | Branches: `main` = foundation, `client/company-x` = this customisation |
| 2 | Each product has a default route; every order gets its own copy that can be edited |
| 3 | A stage can be split across vendors in lots |
| 4 | Vendor → vendor direct shipment and return via plant are both supported |
| 5 | Job work on company material only (vendor does not buy material) |
| 6 | Vendor returns record accepted / rework (stays at vendor) / scrap (with a reason); vendor rejection rate is reported |
| 7 | Rate per unit on every stage, rolled into product cost; vendor billing is out of scope |
| 8 | Each plant is its own stock location |
| 9 | Basic customer master and dispatch note |
| 10 | Printable job-work delivery challan now; ITC-04 return later if confirmed |
| 11 | Lot-level tracking (units per stage per vendor), not serial numbers |

## How it works
* **Sites.** Plants and job-work vendors are warehouses flagged in `production_sites`. A vendor site
  is created from a supplier (`V-<supplier code>`). Company material held by a vendor is therefore
  still in the stock ledger and valued, and it shows in stock reports under that location.
* **Structure.** A made product has a bill of materials (its *kit*) and a route of stages. Each stage
  is done by the plant or a vendor and has a rate per unit and planned days.
* **Orders.** Creating an order expands the BOM recursively: products with a route become *make*
  parts with the route copied; everything else is *buy* (from plant stock). Stages are scheduled
  forward from the start date: sub-parts first, and a parent's first stage starts the day after its latest
  made sub-part finishes. The order can be edited while planned and released when ready. Nothing
  moves before release.
* **Movement.** The physical material in every stage is the kit itself:
  * challan → `JOB_WORK_OUT` (plant → vendor)
  * receipt → `JOB_WORK_RETURN` (vendor → plant, or vendor → next vendor when shipped direct)
* **Transformation at the last stage.** When the last stage completes, the kit is consumed
  (`PRODUCTION_CONSUME`) and the part is produced (`PRODUCTION_OUTPUT`) at:
  *kit value per unit + Σ stage rates*. Vendor and in-house conversion charges therefore land in
  the product's weighted-average cost. A gearbox's cost includes its housing's, shaft's and gears'.
* **Scrap.** Scrapped units post `PRODUCTION_SCRAP` for their kit at wherever the material is and
  reduce the target of the later stages.
* **Dispatch.** A dispatch note is a draft until dispatched; dispatching allocates FEFO and posts `DISPATCH`.
* **Positions ("where are the parts").** Computed from challans, receipts and completions, never
  stored separately. Per part:
  * not started / waiting at plant for a stage
  * at vendor X for a stage (since, expected, overdue, in rework)
  * finished, ready for assembly
  * used in the parent
  * for bought-in parts: issued / still to issue (with any shortage at the plant)

## Screens
| Menu | Purpose |
|---|---|
| Production Orders | List with progress and risk; order page with the parts tracker, stage actions (send to vendor, record return with direct / rework / scrap, record in-house progress, edit / add / remove stage), timeline (planned vs actual), challans and activity |
| Vendor Workload | Units and material value at each vendor, open jobs, overdue, rejection rate, turnaround |
| Job-work Challans | All challans; each opens a printable delivery challan |
| Product Structure | BOM + default route editor |
| Plants & Vendors | Create plants, use an existing warehouse as a plant, add vendors from suppliers |
| Customers / Dispatch Notes | Customer master; draft → dispatch → stock out |
| Overview | Production strip: open orders, at risk, late stages, units and value at vendors, overdue vendor jobs |

## AI
A **Production Agent** (department PROD) joins the Inventory Agent under the Main Agent. Tools:
`list_production_orders`, `get_production_order`, `where_are_parts`, `get_vendor_workload`,
`get_delayed_stages`, and `create_job_work_challan_draft`. The draft tool needs `production.execute`
and only creates an approval request. Issuing the challan happens on approval, through the same
service and permission checks as the UI. A question touching both departments (e.g. "where is
MO-000002 and what is low on stock?") is answered by both agents and consolidated.

## Permissions
| Permission | Granted to |
|---|---|
| `production.view` | every role with view access |
| `production.manage` (structure, orders, routing) | operations manager |
| `production.execute` (challans, receipts, completions) | operations, inventory and warehouse managers; inventory staff |
| `customers.manage` | operations manager |
| `dispatch.manage` | operations, inventory and warehouse managers |

Super admin has all of them.

## Data
Migration `0003_company_x_production_flow`:
* tables: `production_sites`, `customers`, `bom_lines`, `route_stages`, `production_orders`,
  `order_parts`, `order_stages`, `job_work_challans` (+ lines), `job_work_receipts`,
  `stage_completions`, `production_events`, `dispatch_notes` (+ lines)
* adds the six movement types to the ledger check constraints

Its downgrade refuses to run once the ledger contains the new movement types.

Document numbers: `MO-`, `JWC-`, `JWR-`, `DN-`.

## Demo data
The demo loads Plant A · Chakan and four vendors (Precision Machining Works, Sharp Tools, Coatings &
Gears, Thermal Treat). It defines structures for the housing, shaft, gear set and gearbox GB-100, and two customers. Orders:
* MO-000001 — completed, dispatched to Apex Pumps
* MO-000002 — in flight: machining split between two vendors with rework, a direct vendor-to-vendor shipment, a scrapped gear
* MO-000003 — planned

## Not included yet
* ITC-04 return export
* vendor bills / payables
* capacity planning
* serial-level traceability
* automatic material requests to the warehouse when a plant is short (the order page flags the shortage)
