"""Workforce demo data (demo installations only). Built through the real services, so every record is consistent:
schedules come from rotation patterns, vacancies from approved leave, assignments from real claims, overtime from
recorded attendance."""
from __future__ import annotations

import random
from datetime import date, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.deps import Actor, build_actor
from app.core.security import hash_password
from app.models import (
    AssignmentStatus, Department, Employee, LeaveType, OvertimeCalculation, Role, Shift, ShiftAssignment,
    User, UserDepartment,
)
from app.modules.workforce import attendance as att
from app.modules.workforce import leave as lv
from app.modules.workforce import marketplace as mk
from app.modules.workforce import overtime as ot
from app.modules.workforce import people as pp
from app.modules.workforce import schemas as s
from app.modules.workforce import shifts as sh
from app.modules.workforce.core import company_tz, get_scope

D = Decimal

# code, first, last, dept, team, designation, skills, rate, overtime-eligible
EMPLOYEES = [
    ("EMP-101", "Ravi", "Kumar", "PROD", "ASM", "ASSEMBLER", ["TORQUE"], "190", True),
    ("EMP-102", "Sneha", "Patil", "PROD", "ASM", "ASSEMBLER", ["TORQUE"], "185", True),
    ("EMP-103", "Imran", "Shaikh", "PROD", "ASM", "ASSEMBLER", [], "180", True),
    ("EMP-104", "Pooja", "Jadhav", "PROD", "ASM", "ASSEMBLER", ["TORQUE", "FORKLIFT"], "195", True),
    ("EMP-105", "Ganesh", "More", "PROD", "ASM", "ASSEMBLER", [], "175", True),
    ("EMP-106", "Farah", "Khan", "PROD", "ASM", "ASSEMBLER", ["TORQUE"], "185", False),
    ("EMP-111", "Suresh", "Pawar", "PROD", "MCH", "OPERATOR", ["CNC"], "230", True),
    ("EMP-112", "Anil", "Gaikwad", "PROD", "MCH", "OPERATOR", ["CNC", "WELD"], "245", True),
    ("EMP-113", "Deepa", "Kulkarni", "PROD", "MCH", "OPERATOR", ["CNC"], "225", True),
    ("EMP-114", "Mahesh", "Shinde", "PROD", "MCH", "OPERATOR", ["CNC"], "220", True),
    ("EMP-121", "Lata", "Sawant", "PROD", "PKG", "PACKER", ["FORKLIFT"], "160", True),
    ("EMP-122", "Nitin", "Bhosale", "PROD", "PKG", "PACKER", [], "155", True),
    ("EMP-123", "Asha", "Deshpande", "PROD", "PKG", "PACKER", [], "155", True),
    ("EMP-131", "Kiran", "Joshi", "QA", "QI", "INSPECTOR", ["VISUAL"], "210", True),
    ("EMP-132", "Rohit", "Naik", "QA", "QI", "INSPECTOR", ["VISUAL"], "205", True),
]

# code, name, start, end, break, team, required, skills, pattern days (Mon..Sun: True = works)
TEMPLATES = [
    ("ASM-M", "Morning", time(6), time(14), 30, "ASM", 5, [], "PROD"),
    ("MCH-E", "Evening", time(14), time(22), 30, "MCH", 2, ["CNC"], "PROD"),
    ("MCH-N", "Night", time(22), time(6), 30, "MCH", 1, ["CNC"], "PROD"),
    ("PKG-G", "General", time(8), time(16, 30), 30, "PKG", 2, [], "PROD"),
    ("QI-G", "Inspection", time(9), time(17, 30), 30, "QI", 1, ["VISUAL"], "QA"),
]


def _actor(db: Session, user_id) -> Actor:
    return build_actor(db, db.get(User, user_id))


def load_workforce_demo(db: Session, admin: User, password: str) -> None:
    rnd = random.Random(7)
    tz = company_tz(db)
    admin_actor = build_actor(db, admin)
    depts = {d.code: d for d in db.execute(select(Department)).scalars()}
    roles = {r.code: r for r in db.execute(select(Role)).scalars()}

    def user(email: str, name: str, role: str, dept: str) -> User:
        u = User(email=email, full_name=name, password_hash=hash_password(password), created_by=admin.id)
        u.roles = [roles[role]]
        u.departments = [UserDepartment(department_id=depts[dept].id, is_primary=True)]
        db.add(u)
        db.flush()
        return u

    mgr_user = user("shift.manager@demo.example.com", "Sunil Kulkarni", "shift_manager", "PROD")
    qa_mgr_user = user("qa.lead@demo.example.com", "Nandini Rao", "shift_manager", "QA")
    hr_user = user("hr@demo.example.com", "Anita Desai", "hr_admin", "HR")
    hr = _actor(db, hr_user.id)

    loc = pp.create_ref(db, hr, "locations", s.LocationIn(code="PUNE-1", name="Pune Plant, Chakan MIDC"))
    desig = {code: pp.create_ref(db, hr, "designations", s.DesignationIn(code=code, name=name)) for code, name in (
        ("ASSEMBLER", "Assembly Technician"), ("OPERATOR", "CNC Operator"), ("PACKER", "Packing Operator"),
        ("INSPECTOR", "Quality Inspector"), ("SUPERVISOR", "Shift Supervisor"))}
    skills = {code: pp.create_ref(db, hr, "skills", s.SkillIn(code=code, name=name, kind=kind)) for code, name, kind in (
        ("CNC", "CNC machine operation", "skill"), ("WELD", "MIG welding", "certification"), ("TORQUE", "Torque tool calibration", "skill"),
        ("FORKLIFT", "Forklift licence", "certification"), ("VISUAL", "Visual inspection (IS 2500)", "qualification"))}
    teams = {code: pp.create_team(db, hr, s.TeamIn(code=code, name=name, department_id=depts[d].id)) for code, name, d in (
        ("ASM", "Assembly Team", "PROD"), ("MCH", "Machine Operations Team", "PROD"), ("PKG", "Packaging Team", "PROD"),
        ("QI", "Quality Inspection Team", "QA"))}

    supervisor = pp.create_employee(db, hr, s.EmployeeCreate(
        employee_code="EMP-001", first_name="Sunil", last_name="Kulkarni", department_id=depts["PROD"].id,
        designation_id=desig["SUPERVISOR"].id, email="shift.manager@demo.example.com", date_of_joining=date(2019, 6, 1),
        work_location_id=loc.id, overtime_eligible=False, base_hourly_rate=D("320")))
    pp.link_user(db, hr, supervisor.id, mgr_user.id)
    qa_lead = pp.create_employee(db, hr, s.EmployeeCreate(
        employee_code="EMP-002", first_name="Nandini", last_name="Rao", department_id=depts["QA"].id,
        designation_id=desig["SUPERVISOR"].id, email="qa.lead@demo.example.com", date_of_joining=date(2020, 2, 10),
        work_location_id=loc.id, overtime_eligible=False, base_hourly_rate=D("300")))
    pp.link_user(db, hr, qa_lead.id, qa_mgr_user.id)

    templates = {}
    for code, name, st, en, brk, team, req, sk, dept in TEMPLATES:
        templates[code] = sh.create_template(db, hr, s.TemplateIn(
            code=code, name=name, start_time=st, end_time=en, break_minutes=brk, department_id=depts[dept].id,
            work_location_id=loc.id, team_id=teams[team].id, required_count=req, skill_ids=[skills[k].id for k in sk],
            supervisor_id=(supervisor if dept == "PROD" else qa_lead).id,
            color={"ASM-M": "#3d6b57", "MCH-E": "#a86a12", "MCH-N": "#4a5a8a", "PKG-G": "#7a5c99", "QI-G": "#2f7f8f"}[code]))

    today = utcnow().astimezone(tz).date()
    anchor = today - timedelta(days=today.weekday() + 7)  # Monday of last week
    patterns = {}
    for code, tcode, days in (("ASM-6", "ASM-M", 6), ("MCH-E6", "MCH-E", 6), ("MCH-N5", "MCH-N", 5), ("PKG-6", "PKG-G", 6), ("QI-5", "QI-G", 5)):
        patterns[code] = sh.save_pattern(db, hr, s.PatternIn(code=code, name=f"{templates[tcode].name}, {days} days a week", cycle_days=7,
                                                             days=[templates[tcode].id] * days + [None] * (7 - days)))

    emps: dict[str, Employee] = {}
    seat: dict[str, int] = {}
    for code, first, last, dept, team, d, sk, rate, eligible in EMPLOYEES:
        # Staggered rotations: each team member's weekly off falls on a different day, as on a real shop floor.
        seat[team] = seat.get(team, -1) + 1
        pattern = {"ASM": "ASM-6", "PKG": "PKG-6", "QI": "QI-5"}.get(team) or ("MCH-N5" if code == "EMP-114" else "MCH-E6")
        e = pp.create_employee(db, hr, s.EmployeeCreate(
            employee_code=code, first_name=first, last_name=last, department_id=depts[dept].id, designation_id=desig[d].id,
            manager_id=(supervisor if dept == "PROD" else qa_lead).id, date_of_joining=date(2021 + rnd.randint(0, 3), rnd.randint(1, 12), 1),
            work_location_id=loc.id, shift_pattern_id=patterns[pattern].id, pattern_anchor_date=anchor - timedelta(days=seat[team]),
            overtime_eligible=eligible,
            base_hourly_rate=D(rate), team_ids=[teams[team].id], phone=f"98{rnd.randint(10000000, 99999999)}",
            emergency_contact_name="Family contact", emergency_contact_phone=f"97{rnd.randint(10000000, 99999999)}",
            skills=[s.EmployeeSkillIn(skill_id=skills[k].id, level=rnd.randint(2, 4),
                                      valid_until=today + timedelta(days=400) if k in ("WELD", "FORKLIFT") else None) for k in sk]))
        pp.grant_portal_access(db, hr, e.id, s.PortalAccessIn(password=password))
        emps[code] = e
    db.flush()

    # Schedules: last week (attendance history) through two weeks ahead.
    sh.generate(db, admin_actor, get_scope(db, admin_actor), s.GenerateIn(date_from=anchor, date_to=today + timedelta(days=14)))
    db.flush()

    def ea(code: str) -> Actor:
        return _actor(db, emps[code].user_id)

    def scope(a: Actor):
        return get_scope(db, a)

    mgr = _actor(db, mgr_user.id)
    types = {t.code: t for t in db.execute(select(LeaveType)).scalars()}

    def next_workday(code: str, after: int) -> date:
        d = today + timedelta(days=after)
        while lv.is_day_off(db, emps[code], d):
            d += timedelta(days=1)
        return d

    def first_recipient(opp_id) -> str | None:
        from app.models import OpportunityRecipient
        eid = db.execute(select(OpportunityRecipient.employee_id).where(OpportunityRecipient.opportunity_id == opp_id)
                         .order_by(OpportunityRecipient.notified_at)).scalars().first()
        return next((c for c, e in emps.items() if e.id == eid), None)

    # 1. Approved leave tomorrow → vacancy → open opportunity with a rejected and a live claim history.
    d1 = next_workday("EMP-102", 1)
    r = lv.create(db, ea("EMP-102"), scope(ea("EMP-102")), s.LeaveCreate(leave_type_id=types["CL"].id, start_date=d1, end_date=d1,
                                                                         reason="Sister's wedding"))
    res = lv.approve(db, mgr, scope(mgr), r.id, "Approved — enjoy the wedding")
    open_opp = res["opportunities"][0] if res["opportunities"] else None
    if open_opp is not None:
        mk.claim(db, ea("EMP-106"), scope(ea("EMP-106")), open_opp.id, None)  # rejected: not overtime-eligible

    # 2. Leave in three days → opportunity filled by the first eligible claim.
    d2 = next_workday("EMP-103", 3)
    r = lv.create(db, ea("EMP-103"), scope(ea("EMP-103")), s.LeaveCreate(leave_type_id=types["AL"].id, start_date=d2, end_date=d2,
                                                                         reason="Bank and passport appointments"))
    res = lv.approve(db, mgr, scope(mgr), r.id, None)
    for o in res["opportunities"]:
        mk.claim(db, ea("EMP-105"), scope(ea("EMP-105")), o.id, None)  # rejected: already on that shift
        winner = first_recipient(o.id)
        if winner:
            mk.claim(db, ea(winner), scope(ea(winner)), o.id, None)

    # 3. Leave cancelled after cover was assigned → open conflict for the manager.
    d3 = next_workday("EMP-112", 5)
    r = lv.create(db, ea("EMP-112"), scope(ea("EMP-112")), s.LeaveCreate(leave_type_id=types["CL"].id, start_date=d3, end_date=d3,
                                                                         reason="Village festival"))
    res = lv.approve(db, mgr, scope(mgr), r.id, None)
    for o in res["opportunities"]:
        winner = first_recipient(o.id)
        if winner:
            mk.claim(db, ea(winner), scope(ea(winner)), o.id, None)
            lv.cancel(db, ea("EMP-112"), scope(ea("EMP-112")), r.id, "Festival moved to next month")

    # 4. Pending requests for the manager, one rejected request.
    d4 = next_workday("EMP-104", 8)
    lv.create(db, ea("EMP-104"), scope(ea("EMP-104")), s.LeaveCreate(leave_type_id=types["AL"].id, start_date=d4, end_date=d4 + timedelta(days=1),
                                                                     reason="Family trip to Kolhapur"))
    d5 = next_workday("EMP-121", 2)
    lv.create(db, ea("EMP-121"), scope(ea("EMP-121")), s.LeaveCreate(leave_type_id=types["CL"].id, start_date=d5, end_date=d5,
                                                                     half_day="second_half", reason="Child's school event"))
    d6 = next_workday("EMP-111", 4)
    rj = lv.create(db, ea("EMP-111"), scope(ea("EMP-111")), s.LeaveCreate(leave_type_id=types["CL"].id, start_date=d6, end_date=d6,
                                                                          reason="Personal"))
    lv.reject(db, mgr, scope(mgr), rj.id, "Two CNC operators already off that day; please choose another date")

    # 5. An extra position nobody claimed → expired, waiting for the manager.
    d7 = next_workday("EMP-121", 2)
    pkg_shift = db.execute(select(Shift).where(Shift.template_id == templates["PKG-G"].id, Shift.shift_date == d7)).unique().scalar_one_or_none()
    if pkg_shift is not None:
        sh.update_shift(db, mgr, scope(mgr), pkg_shift.id, s.ShiftUpdate(required_count=pkg_shift.required_count + 1))
        exp = mk.create_manual(db, mgr, scope(mgr), s.OpportunityCreate(shift_id=pkg_shift.id, positions=1,
                                                                        notes="Export consignment — extra packer needed"))
        exp.claim_deadline = utcnow() - timedelta(minutes=5)
        mk.refresh(db, exp)

    # 6. Last week's attendance → overtime calculated from it.
    week_end = today - timedelta(days=1)
    past = db.execute(select(ShiftAssignment).join(Shift, Shift.id == ShiftAssignment.shift_id).where(
        Shift.shift_date >= anchor, Shift.shift_date <= week_end, ShiftAssignment.status == AssignmentStatus.ASSIGNED,
        ShiftAssignment.end_at < utcnow()).order_by(ShiftAssignment.start_at)).unique().scalars().all()
    hr_scope = scope(hr)
    for a in past:
        roll = rnd.random()
        if roll < 0.04:
            continue  # leaves it for the absence job to mark
        late = rnd.choice([0, 0, 0, 0, 3, 7, 18]) if roll > 0.1 else 0
        extra = rnd.choice([0, 0, 0, 0, 0, 30, 60, 120, 150]) if a.employee.overtime_eligible else 0
        cin = a.start_at + timedelta(minutes=late - rnd.randint(0, 8))
        cout = a.end_at + timedelta(minutes=extra + rnd.randint(0, 6))
        att.record(db, hr, hr_scope, s.RecordIn(assignment_id=a.id, check_in_at=cin, check_out_at=cout))
    att.close_attendance(db)

    # One correction request waiting for a decision.
    req_a = next((a for a in past if a.employee.employee_code == "EMP-101"), None)
    if req_a is not None:
        att.request_correction(db, ea("EMP-101"), scope(ea("EMP-101")), s.CorrectionIn(
            assignment_id=req_a.id, check_out_at=req_a.end_at + timedelta(minutes=95),
            reason="Stayed back to finish the pump housing batch; badge reader was down at the exit"))

    # Approve most of last week's overtime and export the first part of it; leave the rest for the manager.
    calcs = db.execute(select(OvertimeCalculation).join(Employee, Employee.id == OvertimeCalculation.employee_id).where(
        OvertimeCalculation.payable_minutes > 0, Employee.department_id == depts["PROD"].id).order_by(OvertimeCalculation.work_date)).unique().scalars().all()
    for c in calcs[: max(0, len(calcs) - 4)]:
        ot.approve(db, mgr, scope(mgr), c.id, s.CalcDecisionIn())
    exported_until = anchor + timedelta(days=3)
    if any(c.work_date <= exported_until for c in calcs[: max(0, len(calcs) - 4)]):
        ot.create_export(db, hr, hr_scope, s.ExportIn(period_start=anchor, period_end=exported_until))
    db.flush()
