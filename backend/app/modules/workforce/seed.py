"""Workforce reference data: one company policy, one example overtime policy, standard leave types.

Values are examples for a new installation and are meant to be edited by HR; nothing depends on them.
"""
from __future__ import annotations

from datetime import time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DayRule, LeaveType, OvertimePolicy, RoundingMode, WorkforcePolicy

D = Decimal

LEAVE_TYPES = [
    # code, name, paid, annual days, carry forward, unlimited, document required, half day
    ("CL", "Casual Leave", True, D("8"), D("0"), False, False, True),
    ("SL", "Sick Leave", True, D("8"), D("0"), False, True, True),
    ("AL", "Annual Leave", True, D("15"), D("30"), False, False, False),
    ("EL", "Emergency Leave", True, D("3"), D("0"), False, False, True),
    ("UL", "Unpaid Leave", False, D("0"), D("0"), True, False, True),
]


def seed_workforce(db: Session) -> None:
    if db.execute(select(WorkforcePolicy).where(WorkforcePolicy.department_id.is_(None))).scalar_one_or_none() is None:
        db.add(WorkforcePolicy(department_id=None))
    if db.execute(select(OvertimePolicy)).first() is None:
        db.add(OvertimePolicy(
            code="STD", name="Standard overtime (example values — review before use)",
            description="Example: overtime above 9 h/day or 48 h/week at 2x; weekly-off and holiday hours at 2x; "
                        "night allowance 22:00–06:00. Configure to match your location's rules and agreements.",
            daily_threshold_hours=D("9"), weekly_threshold_hours=D("48"), ot_multiplier=D("2"),
            weekly_off_rule=DayRule.ALL_OVERTIME, weekly_off_multiplier=D("2"), holiday_rule=DayRule.ALL_OVERTIME,
            holiday_multiplier=D("2"), night_start=time(22, 0), night_end=time(6, 0), night_allowance_per_hour=D("0"),
            rounding_minutes=15, rounding_mode=RoundingMode.DOWN, min_overtime_minutes=30, requires_approval=True, is_default=True))
    existing = set(db.execute(select(LeaveType.code)).scalars())
    for code, name, paid, days, carry, unlimited, doc, half in LEAVE_TYPES:
        if code not in existing:
            db.add(LeaveType(code=code, name=name, is_paid=paid, annual_days=days, carry_forward_max=carry,
                             unlimited=unlimited, requires_document=doc, allow_half_day=half))
    db.flush()
