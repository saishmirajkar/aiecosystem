"""Permission catalogue and default role → permission mapping.

Permissions are seeded into the database; roles are editable at runtime. This module is the
source of the *catalogue* and of the *initial* role definitions only.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDef:
    code: str
    name: str
    module: str


P = PermissionDef

PERMISSIONS: list[PermissionDef] = [
    # ERP core
    P("company.view", "View company settings", "erp_core"),
    P("company.manage", "Manage company settings", "erp_core"),
    P("users.view", "View users", "erp_core"),
    P("users.manage", "Create and manage users", "erp_core"),
    P("roles.view", "View roles and permissions", "erp_core"),
    P("roles.manage", "Manage roles and permissions", "erp_core"),
    P("departments.view", "View departments", "erp_core"),
    P("departments.manage", "Manage departments", "erp_core"),
    P("settings.view", "View system settings", "erp_core"),
    P("settings.manage", "Manage system settings", "erp_core"),
    P("audit.view", "View audit logs", "erp_core"),
    # Master data
    P("masterdata.manage", "Manage units, conversions and categories", "master_data"),
    P("products.create", "Create products", "master_data"),
    P("products.edit", "Edit products", "master_data"),
    P("warehouses.manage", "Manage warehouses and storage locations", "master_data"),
    P("suppliers.manage", "Manage suppliers", "master_data"),
    # Inventory
    P("inventory.view", "View inventory", "inventory"),
    P("inventory.receive", "Receive stock (goods receipts, stock-in)", "inventory"),
    P("inventory.issue", "Issue stock", "inventory"),
    P("inventory.transfer", "Create and process stock transfers", "inventory"),
    P("inventory.transfer.approve", "Approve stock transfers", "inventory"),
    P("inventory.adjust", "Create stock adjustments and counts", "inventory"),
    P("inventory.adjust.approve", "Approve stock adjustments", "inventory"),
    P("inventory.reserve", "Reserve and release stock", "inventory"),
    P("inventory.opening_balance", "Post opening balances", "inventory"),
    P("inventory.high_value", "Perform high-value inventory movements", "inventory"),
    P("inventory.negative_stock", "Post movements resulting in negative stock (if enabled)", "inventory"),
    # Purchasing
    P("purchasing.view", "View suppliers and purchasing documents", "purchasing"),
    P("purchasing.requisition.create", "Create purchase requisitions", "purchasing"),
    P("purchasing.requisition.approve", "Approve purchase requisitions", "purchasing"),
    P("purchasing.po.create", "Create and edit purchase orders", "purchasing"),
    P("purchasing.po.approve", "Approve purchase orders", "purchasing"),
    P("purchasing.over_receipt", "Receive more than the outstanding PO quantity", "purchasing"),
    # Reports & AI
    P("reports.view", "Access reports and valuation", "reports"),
    P("ai.access", "Access the AI Command Center", "ai"),
    P("approvals.view", "View action approvals", "ai"),
    P("approvals.decide", "Approve or reject action approvals", "ai"),
    # Workforce & shift management
    P("workforce.self", "Own schedule, leave, overtime claims and attendance", "workforce"),
    P("workforce.view", "View employees and schedules in scope", "workforce"),
    P("workforce.schedule", "Create and edit shifts and assignments", "workforce"),
    P("workforce.leave.approve", "Approve or reject leave", "workforce"),
    P("workforce.overtime.manage", "Publish, extend and cancel overtime opportunities; manual assignment", "workforce"),
    P("workforce.attendance.manage", "Record attendance and decide corrections", "workforce"),
    P("workforce.overtime.approve", "Approve overtime records", "workforce"),
    P("workforce.employees.manage", "Manage employee records", "workforce"),
    P("workforce.config", "Configure shifts, teams, skills, leave and overtime policies", "workforce"),
    P("workforce.payroll", "View pay rates and create payroll exports", "workforce"),
    P("workforce.all_departments", "Access workforce data across all departments", "workforce"),
]

ALL_CODES: frozenset[str] = frozenset(p.code for p in PERMISSIONS)

_VIEW = ["inventory.view", "purchasing.view", "reports.view", "ai.access", "departments.view", "company.view"]

DEFAULT_ROLES: dict[str, dict[str, object]] = {
    "super_admin": {
        "name": "Super Administrator",
        "description": "Unrestricted access to the installation. Always holds every permission.",
        "permissions": sorted(ALL_CODES),
    },
    "company_admin": {
        "name": "Company Administrator",
        "description": "Administers company, users, roles and settings; full operational access.",
        "permissions": sorted(ALL_CODES - {"inventory.negative_stock"}),
    },
    "operations_manager": {
        "name": "Operations Manager",
        "description": "Oversees inventory and purchasing; approves sensitive operations.",
        "permissions": _VIEW + [
            "users.view", "settings.view", "audit.view", "inventory.receive", "inventory.issue",
            "inventory.transfer", "inventory.transfer.approve", "inventory.adjust",
            "inventory.adjust.approve", "inventory.reserve", "inventory.high_value",
            "purchasing.requisition.create", "purchasing.requisition.approve", "purchasing.po.create",
            "purchasing.po.approve", "purchasing.over_receipt", "approvals.view", "approvals.decide",
        ],
    },
    "inventory_manager": {
        "name": "Inventory Manager",
        "description": "Manages products, stock and warehouses; approves adjustments and transfers.",
        "permissions": _VIEW + [
            "masterdata.manage", "products.create", "products.edit", "warehouses.manage",
            "inventory.receive", "inventory.issue", "inventory.transfer", "inventory.transfer.approve",
            "inventory.adjust", "inventory.adjust.approve", "inventory.reserve",
            "inventory.opening_balance", "inventory.high_value", "purchasing.requisition.create",
            "approvals.view", "approvals.decide",
        ],
    },
    "warehouse_manager": {
        "name": "Warehouse Manager",
        "description": "Runs warehouse operations: receipts, issues, transfers, counts.",
        "permissions": _VIEW + [
            "warehouses.manage", "inventory.receive", "inventory.issue", "inventory.transfer",
            "inventory.transfer.approve", "inventory.adjust", "inventory.reserve",
            "purchasing.requisition.create", "approvals.view", "approvals.decide",
        ],
    },
    "purchase_manager": {
        "name": "Purchase Manager",
        "description": "Manages suppliers, requisitions and purchase orders.",
        "permissions": _VIEW + [
            "suppliers.manage", "purchasing.requisition.create", "purchasing.requisition.approve",
            "purchasing.po.create", "purchasing.po.approve", "purchasing.over_receipt",
            "approvals.view", "approvals.decide",
        ],
    },
    "inventory_staff": {
        "name": "Inventory Staff",
        "description": "Performs day-to-day stock movements.",
        "permissions": ["inventory.view", "purchasing.view", "ai.access", "inventory.receive",
                        "inventory.issue", "inventory.transfer", "inventory.adjust",
                        "purchasing.requisition.create", "approvals.view"],
    },
    "employee": {
        "name": "Employee",
        "description": "Self-service: own shifts, leave, overtime opportunities and attendance.",
        "permissions": ["workforce.self", "ai.access"],
    },
    "shift_manager": {
        "name": "Shift Manager",
        "description": "Schedules shifts, approves leave, runs the overtime marketplace and attendance for their departments.",
        "permissions": ["workforce.self", "workforce.view", "workforce.schedule", "workforce.leave.approve",
                        "workforce.overtime.manage", "workforce.attendance.manage", "workforce.overtime.approve",
                        "ai.access", "departments.view", "approvals.view"],
    },
    "hr_admin": {
        "name": "HR Administrator",
        "description": "Manages employees, workforce policies, overtime records and payroll exports across departments.",
        "permissions": [p.code for p in PERMISSIONS if p.module == "workforce"] + [
            "users.view", "departments.view", "departments.manage", "ai.access", "approvals.view", "audit.view"],
    },
    "read_only": {
        "name": "Read-Only User",
        "description": "Can view inventory, purchasing and reports.",
        "permissions": ["inventory.view", "purchasing.view", "reports.view", "company.view", "departments.view"],
    },
}

DEFAULT_DEPARTMENTS: list[tuple[str, str]] = [
    ("MGMT", "Management"), ("INV", "Inventory"), ("WH", "Warehouse"), ("PROC", "Procurement"),
    ("PROD", "Production"), ("FIN", "Finance"), ("QA", "Quality"), ("MAINT", "Maintenance"),
    ("HR", "HR"), ("LOG", "Logistics"),
]

for _role in DEFAULT_ROLES.values():
    _unknown = set(_role["permissions"]) - ALL_CODES  # type: ignore[arg-type]
    assert not _unknown, f"Unknown permissions in default roles: {_unknown}"
