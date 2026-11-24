"""Attendance: supervisor recording, optional self check-in, corrections with approval, CSV import, absence marking.

No location data is collected. Every change to an existing record leaves an attendance_corrections row
(supervisor edits are recorded as auto-approved corrections), so the history of a record is always visible.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.common import audit, jsonable
from app.core.database import utcnow
from app.core.deps import Actor
from app.core.errors import Conflict, InvalidState, NotFound, PermissionDenied, ValidationFailed
from app.models import (
    AssignmentStatus, AttendanceCorrection, AttendanceRecord, AttendanceSource, AttendanceStatus, CorrectionStatus, Employee,
    Shift, ShiftAssignment, ShiftStatus,
)
from app.modules.workforce import schemas as s
from app.modules.workforce.core import (
    Scope, at, company_tz, day_start, employee_brief, hm, minutes, require_me, users_managing, workforce_policy,
)
from app.modules.workforce.notify import notify

EARLY_CHECK_IN = timedelta(hours=2)
FIELDS = ("check_in_at", "check_out_at", "break_minutes", "status", "worked_minutes", "late_minutes", "early_leave_minutes")


def _snapshot(r: AttendanceRecord | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return jsonable({k: getattr(r, k) for k in FIELDS})


def _default_break(a: ShiftAssignment) -> int:
    return a.shift.break_minutes if (a.start_at, a.end_at) == (a.shift.start_at, a.shift.end_at) else 0


def compute(db: Session, r: AttendanceRecord, override: AttendanceStatus | None = None) -> None:
    policy = workforce_policy(db, r.employee.department_id)
    worked = 0
    if r.check_in_at and r.check_out_at:
        worked = max(0, minutes(r.check_in_at, r.check_out_at) - (r.break_minutes or 0))
    r.worked_minutes = worked
    r.late_minutes = minutes(r.scheduled_start, r.check_in_at) if r.check_in_at and r.scheduled_start else 0
    r.early_leave_minutes = (minutes(r.check_out_at, r.scheduled_end)
                             if r.check_out_at and r.scheduled_end and r.check_out_at < r.scheduled_end else 0)
    if override is not None:
        r.status = override
    elif not r.check_in_at:
        r.status = AttendanceStatus.ABSENT
    elif r.check_out_at and worked < Decimal(policy.half_day_min_hours) * 60:
        r.status = AttendanceStatus.HALF_DAY
    elif r.late_minutes > policy.late_grace_minutes:
        r.status = AttendanceStatus.LATE
    else:
        r.status = AttendanceStatus.PRESENT


def _record_for(db: Session, a: ShiftAssignment) -> AttendanceRecord | None:
    return db.execute(select(AttendanceRecord).where(AttendanceRecord.assignment_id == a.id)).unique().scalar_one_or_none()


def _new_record(a: ShiftAssignment, source: AttendanceSource, actor: Actor | None) -> AttendanceRecord:
    return AttendanceRecord(employee_id=a.employee_id, employee=a.employee, assignment_id=a.id, work_date=a.shift.shift_date,
                            scheduled_start=a.start_at, scheduled_end=a.end_at, break_minutes=_default_break(a),
                            status=AttendanceStatus.ABSENT, source=source, recorded_by=actor.user_id if actor else None)


def _apply(db: Session, actor: Actor | None, a: ShiftAssignment, *, check_in: datetime | None, check_out: datetime | None,
           break_minutes: int | None, status: AttendanceStatus | None, source: AttendanceSource, reason: str,
           notes: str | None = None, set_fields: set[str] | None = None) -> AttendanceRecord:
    """Create or change the record for an assignment; changes to an existing record are logged as corrections."""
    if a.status == AssignmentStatus.ON_LEAVE and status not in (None, AttendanceStatus.ON_LEAVE):
        raise InvalidState("This employee is on approved leave for this shift")
    if a.status in (AssignmentStatus.RELEASED, AssignmentStatus.CANCELLED):
        raise InvalidState("This assignment is no longer active")
    r = _record_for(db, a)
    before = _snapshot(r)
    if r is None:
        r = _new_record(a, source, actor)
        db.add(r)
    fields = set_fields if set_fields is not None else {"check_in_at", "check_out_at", "break_minutes"}
    if "check_in_at" in fields:
        r.check_in_at = check_in
    if "check_out_at" in fields:
        r.check_out_at = check_out
    if "break_minutes" in fields and break_minutes is not None:
        r.break_minutes = break_minutes
    if r.check_in_at and r.check_out_at and r.check_out_at <= r.check_in_at:
        raise ValidationFailed("Check-out must be after check-in")
    if notes is not None:
        r.notes = notes
    r.source, r.recorded_by = source, actor.user_id if actor else None
    compute(db, r, status)
    db.flush()
    if before is not None and before != _snapshot(r) and source != AttendanceSource.CORRECTION:
        db.add(AttendanceCorrection(employee_id=a.employee_id, attendance_id=r.id, assignment_id=a.id, check_in_at=r.check_in_at,
                                    check_out_at=r.check_out_at, break_minutes=r.break_minutes, status_override=status, reason=reason,
                                    before=before, status=CorrectionStatus.APPROVED, requested_by=actor.user_id if actor else None,
                                    decided_by=actor.user_id if actor else None, decided_at=utcnow(), decision_note="Recorded directly"))
    from app.modules.workforce.overtime import recalculate_week
    recalculate_week(db, a.employee, a.shift.shift_date)
    return r


def _assignment(db: Session, assignment_id: uuid.UUID) -> ShiftAssignment:
    a = db.get(ShiftAssignment, assignment_id)
    if a is None:
        raise NotFound("Assignment not found")
    return a


def record(db: Session, actor: Actor, scope: Scope, data: s.RecordIn) -> AttendanceRecord:
    a = _assignment(db, data.assignment_id)
    scope.require_manage(a.employee, "workforce.attendance.manage")
    fields = {k for k in ("check_in_at", "check_out_at", "break_minutes") if k in data.model_fields_set}
    r = _apply(db, actor, a, check_in=data.check_in_at, check_out=data.check_out_at, break_minutes=data.break_minutes,
               status=data.status, source=AttendanceSource.SUPERVISOR, reason=data.notes or "Recorded by supervisor",
               notes=data.notes, set_fields=fields)
    audit(db, actor, "workforce.attendance.record", "attendance_record", r.id, data.model_dump())
    return r


def _current_assignment(db: Session, emp: Employee, now: datetime, *, for_checkout: bool = False) -> ShiftAssignment:
    A = ShiftAssignment
    if for_checkout:
        rows = db.execute(select(A).join(AttendanceRecord, AttendanceRecord.assignment_id == A.id).where(
            A.employee_id == emp.id, AttendanceRecord.check_in_at.is_not(None), AttendanceRecord.check_out_at.is_(None),
            A.start_at <= now, A.end_at >= now - timedelta(hours=8)).order_by(A.start_at.desc())).unique().scalars().first()
    else:
        rows = db.execute(select(A).where(A.employee_id == emp.id, A.status == AssignmentStatus.ASSIGNED,
                                          A.start_at - EARLY_CHECK_IN <= now, A.end_at >= now).order_by(A.start_at)).unique().scalars().first()
    if rows is None:
        raise NotFound("No shift to check " + ("out of" if for_checkout else "in to") + " right now", code="no_current_shift")
    return rows


def _who(db: Session, actor: Actor, scope: Scope, data: s.CheckIn) -> tuple[Employee, AttendanceSource]:
    if data.employee_id and not scope.is_self(data.employee_id):
        emp = db.get(Employee, data.employee_id)
        if emp is None:
            raise NotFound("Employee not found")
        scope.require_manage(emp, "workforce.attendance.manage")
        return emp, AttendanceSource.SUPERVISOR
    emp = require_me(scope)
    actor.require("workforce.self")
    if not workforce_policy(db, emp.department_id).allow_self_check_in:
        raise PermissionDenied("Self check-in is switched off for your department; your supervisor records attendance",
                               code="self_check_in_disabled")
    return emp, AttendanceSource.SELF


def check_in(db: Session, actor: Actor, scope: Scope, data: s.CheckIn) -> AttendanceRecord:
    emp, source = _who(db, actor, scope, data)
    now = utcnow()
    a = _assignment(db, data.assignment_id) if data.assignment_id else _current_assignment(db, emp, now)
    if a.employee_id != emp.id:
        raise ValidationFailed("That assignment belongs to someone else")
    if a.status != AssignmentStatus.ASSIGNED:
        raise InvalidState("This assignment is not active")
    if not (a.start_at - EARLY_CHECK_IN <= now <= a.end_at):
        raise InvalidState("Check-in opens 2 hours before the shift and closes when it ends")
    existing = _record_for(db, a)
    if existing and existing.check_in_at:
        tz = company_tz(db)
        raise Conflict(f"Already checked in at {hm(existing.check_in_at, tz)}")
    r = _apply(db, actor, a, check_in=now, check_out=None, break_minutes=None, status=None, source=source,
               reason="Check-in", set_fields={"check_in_at"})
    audit(db, actor, "workforce.attendance.check_in", "attendance_record", r.id, {"source": source.value})
    return r


def check_out(db: Session, actor: Actor, scope: Scope, data: s.CheckIn) -> AttendanceRecord:
    emp, source = _who(db, actor, scope, data)
    now = utcnow()
    a = _assignment(db, data.assignment_id) if data.assignment_id else _current_assignment(db, emp, now, for_checkout=True)
    r = _record_for(db, a)
    if r is None or not r.check_in_at:
        raise InvalidState("There is no check-in to close")
    if r.check_out_at:
        raise Conflict("Already checked out")
    r = _apply(db, actor, a, check_in=r.check_in_at, check_out=now, break_minutes=None, status=None, source=source,
               reason="Check-out", set_fields={"check_out_at"})
    audit(db, actor, "workforce.attendance.check_out", "attendance_record", r.id, {"source": source.value})
    return r


# ------------------------------------------------------------------ corrections
def request_correction(db: Session, actor: Actor, scope: Scope, data: s.CorrectionIn) -> AttendanceCorrection:
    a = _assignment(db, data.assignment_id)
    if scope.is_self(a.employee):
        actor.require("workforce.self")
    else:
        scope.require_manage(a.employee, "workforce.attendance.manage")
    if a.start_at > utcnow():
        raise InvalidState("You can only correct attendance for a shift that has started")
    if db.execute(select(AttendanceCorrection.id).where(AttendanceCorrection.assignment_id == a.id,
                                                        AttendanceCorrection.status == CorrectionStatus.PENDING)).first():
        raise Conflict("A correction for this shift is already waiting for a decision")
    r = _record_for(db, a)
    c = AttendanceCorrection(employee_id=a.employee_id, attendance_id=r.id if r else None, assignment_id=a.id,
                             check_in_at=data.check_in_at, check_out_at=data.check_out_at, break_minutes=data.break_minutes,
                             status_override=data.status, reason=data.reason, before=_snapshot(r), requested_by=actor.user_id)
    db.add(c)
    db.flush()
    approvers = users_managing(db, a.employee.department_id, "workforce.attendance.manage", exclude_user=a.employee.user_id, employee=a.employee)
    notify(db, approvers, "attendance_correction", f"Attendance correction: {a.employee.full_name}",
           f"{a.employee.full_name} asks to correct attendance for {a.shift.name} on {a.shift.shift_date:%d %b}. Reason: {data.reason}",
           {"correction_id": str(c.id), "link": "/workforce/attendance?tab=corrections"}, dedupe_key=f"corr:{c.id}")
    audit(db, actor, "workforce.attendance.correction_request", "attendance_correction", c.id, data.model_dump())
    return c


def decide_correction(db: Session, actor: Actor, scope: Scope, correction_id: uuid.UUID, approve: bool, note: str | None) -> AttendanceCorrection:
    db.execute(select(AttendanceCorrection.id).where(AttendanceCorrection.id == correction_id).with_for_update())
    c = db.get(AttendanceCorrection, correction_id)
    if c is None:
        raise NotFound("Correction not found")
    scope.require_manage(c.employee, "workforce.attendance.manage")
    if c.requested_by == actor.user_id and approve:
        raise PermissionDenied("Someone else has to approve a correction you requested", code="self_approval_forbidden")
    if c.status != CorrectionStatus.PENDING:
        raise InvalidState("This correction was already decided")
    if not approve and not note:
        raise ValidationFailed("Give a reason for rejecting", details={"field": "note"})
    if approve:
        a = _assignment(db, c.assignment_id)
        r = _record_for(db, a)
        fields = set()
        if c.check_in_at is not None:
            fields.add("check_in_at")
        if c.check_out_at is not None:
            fields.add("check_out_at")
        if c.break_minutes is not None:
            fields.add("break_minutes")
        rec = _apply(db, actor, a, check_in=c.check_in_at or (r.check_in_at if r else None),
                     check_out=c.check_out_at or (r.check_out_at if r else None), break_minutes=c.break_minutes,
                     status=c.status_override, source=AttendanceSource.CORRECTION, reason=c.reason, set_fields=fields)
        c.attendance_id = rec.id
    c.status = CorrectionStatus.APPROVED if approve else CorrectionStatus.REJECTED
    c.decided_by, c.decided_at, c.decision_note = actor.user_id, utcnow(), note
    if c.employee.user_id and c.requested_by != actor.user_id:
        notify(db, [c.employee.user_id], "attendance_correction", f"Attendance correction {'approved' if approve else 'rejected'}",
               "Your attendance correction was " + ("approved." if approve else f"rejected. Reason: {note}"), {"correction_id": str(c.id)})
    audit(db, actor, f"workforce.attendance.correction_{'approve' if approve else 'reject'}", "attendance_correction", c.id, {"note": note})
    return c


def correction_out(db: Session, c: AttendanceCorrection) -> dict[str, Any]:
    tz = company_tz(db)
    a = db.get(ShiftAssignment, c.assignment_id) if c.assignment_id else None
    return {"id": c.id, "employee": employee_brief(c.employee), "assignment_id": c.assignment_id,
            "shift": {"name": a.shift.name, "date": a.shift.shift_date, "start": hm(a.start_at, tz), "end": hm(a.end_at, tz)} if a else None,
            "check_in_at": c.check_in_at, "check_out_at": c.check_out_at, "break_minutes": c.break_minutes,
            "status_override": c.status_override.value if c.status_override else None, "reason": c.reason, "before": c.before,
            "status": c.status.value, "requested_at": c.created_at, "decided_at": c.decided_at, "decision_note": c.decision_note,
            "requested_by_self": c.requested_by is not None and c.employee.user_id == c.requested_by}


def list_corrections(db: Session, scope: Scope, status: str | None = "pending", mine: bool = False) -> list[dict[str, Any]]:
    stmt = select(AttendanceCorrection).join(Employee, Employee.id == AttendanceCorrection.employee_id)
    if mine or not scope.manages_anyone:
        stmt = stmt.where(AttendanceCorrection.employee_id == (scope.me.id if scope.me else None))
    else:
        stmt = scope.employee_filter(stmt)
    if status:
        stmt = stmt.where(AttendanceCorrection.status == status)
    return [correction_out(db, c) for c in db.execute(stmt.order_by(AttendanceCorrection.created_at.desc()).limit(200)).unique().scalars()]


# ------------------------------------------------------------------ views
def record_out(db: Session, r: AttendanceRecord) -> dict[str, Any]:
    tz = company_tz(db)
    return {"id": r.id, "employee": employee_brief(r.employee), "assignment_id": r.assignment_id, "work_date": r.work_date,
            "shift_name": r.assignment.shift.name if r.assignment else None,
            "scheduled_start": r.scheduled_start, "scheduled_end": r.scheduled_end, "check_in_at": r.check_in_at,
            "check_out_at": r.check_out_at, "check_in": hm(r.check_in_at, tz), "check_out": hm(r.check_out_at, tz),
            "scheduled": f"{hm(r.scheduled_start, tz)}–{hm(r.scheduled_end, tz)}", "break_minutes": r.break_minutes,
            "worked_minutes": r.worked_minutes, "late_minutes": r.late_minutes, "early_leave_minutes": r.early_leave_minutes,
            "status": r.status.value, "source": r.source.value, "notes": r.notes, "updated_at": r.updated_at}


def list_records(db: Session, scope: Scope, *, date_from: date, date_to: date, employee_id: uuid.UUID | None = None,
                 department_id: uuid.UUID | None = None, status: str | None = None, mine: bool = False) -> list[dict[str, Any]]:
    stmt = select(AttendanceRecord).join(Employee, Employee.id == AttendanceRecord.employee_id).where(
        AttendanceRecord.work_date >= date_from, AttendanceRecord.work_date <= date_to)
    if mine or not scope.manages_anyone:
        stmt = stmt.where(AttendanceRecord.employee_id == (scope.me.id if scope.me else None))
    else:
        stmt = scope.employee_filter(stmt)
    if employee_id:
        stmt = stmt.where(AttendanceRecord.employee_id == employee_id)
    if department_id:
        stmt = stmt.where(Employee.department_id == department_id)
    if status:
        stmt = stmt.where(AttendanceRecord.status.in_(status.split(",")))
    return [record_out(db, r) for r in db.execute(stmt.order_by(AttendanceRecord.work_date.desc(), Employee.first_name).limit(1000)).unique().scalars()]


def day_sheet(db: Session, scope: Scope, d: date, department_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """Every assignment on a date with its attendance: what a supervisor works through at the start of a shift."""
    if not scope.manages_anyone:
        raise PermissionDenied("Only supervisors see the day sheet")
    tz = company_tz(db)
    now = utcnow()
    stmt = scope.department_filter(select(ShiftAssignment).join(Shift, Shift.id == ShiftAssignment.shift_id).where(
        Shift.shift_date == d, Shift.status == ShiftStatus.SCHEDULED,
        ShiftAssignment.status.in_([AssignmentStatus.ASSIGNED, AssignmentStatus.ON_LEAVE])), Shift.department_id)
    if department_id:
        stmt = stmt.where(Shift.department_id == department_id)
    out = []
    for a in db.execute(stmt.order_by(Shift.start_at, ShiftAssignment.created_at)).unique().scalars():
        r = _record_for(db, a)
        grace = timedelta(minutes=workforce_policy(db, a.shift.department_id).late_grace_minutes)
        if a.status == AssignmentStatus.ON_LEAVE:
            live = "on_leave"
        elif r is not None and r.check_in_at and not r.check_out_at:
            live = "working"
        elif r is not None:
            live = r.status.value
        elif now > a.start_at + grace:
            live = "not_checked_in"
        else:
            live = "scheduled"
        out.append({"assignment_id": a.id, "employee": employee_brief(a.employee), "shift_id": a.shift_id, "shift_name": a.shift.name,
                    "scheduled": f"{hm(a.start_at, tz)}–{hm(a.end_at, tz)}", "start_at": a.start_at, "end_at": a.end_at,
                    "source": a.source.value, "live_status": live, "record": record_out(db, r) if r else None})
    return out


# ------------------------------------------------------------------ import & housekeeping
def import_csv(db: Session, actor: Actor, scope: Scope, content: str) -> dict[str, Any]:
    """Columns: employee_code, date (YYYY-MM-DD), check_in (HH:MM), check_out (HH:MM), break_minutes (optional).
    Typical source: a biometric terminal's export. A check-out earlier than the check-in is taken as the next day."""
    actor.require("workforce.attendance.manage")
    tz = company_tz(db)
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    need = {"employee_code", "date", "check_in", "check_out"}
    if not reader.fieldnames or not need.issubset({f.strip().lower() for f in reader.fieldnames}):
        raise ValidationFailed("CSV needs the columns: employee_code, date, check_in, check_out (break_minutes optional)")
    results, ok = [], 0
    for i, raw in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        try:
            code = row["employee_code"]
            emp = db.execute(select(Employee).where(func.lower(Employee.employee_code) == code.lower())).unique().scalar_one_or_none()
            if emp is None or not scope.can_see(emp):
                raise ValueError(f"unknown employee {code}")
            if scope.is_self(emp):
                raise ValueError("you cannot import your own attendance")
            d = date.fromisoformat(row["date"])
            cin = at(d, datetime.strptime(row["check_in"], "%H:%M").time(), tz) if row["check_in"] else None
            cout = at(d, datetime.strptime(row["check_out"], "%H:%M").time(), tz) if row["check_out"] else None
            if cin and cout and cout <= cin:
                cout += timedelta(days=1)
            a = db.execute(select(ShiftAssignment).join(Shift, Shift.id == ShiftAssignment.shift_id).where(
                ShiftAssignment.employee_id == emp.id, Shift.shift_date == d, ShiftAssignment.status == AssignmentStatus.ASSIGNED)
                .order_by(ShiftAssignment.start_at)).unique().scalars().first()
            if a is None:
                raise ValueError(f"{code} has no shift on {d}")
            brk = int(row["break_minutes"]) if row.get("break_minutes") else None
            with db.begin_nested():
                _apply(db, actor, a, check_in=cin, check_out=cout, break_minutes=brk, status=None, source=AttendanceSource.IMPORT,
                       reason="CSV import", set_fields={"check_in_at", "check_out_at", "break_minutes"} if brk is not None
                       else {"check_in_at", "check_out_at"})
            ok += 1
            results.append({"line": i, "employee_code": code, "status": "imported"})
        except (ValueError, KeyError) as exc:
            results.append({"line": i, "employee_code": row.get("employee_code"), "status": "error", "error": str(exc)})
        except Exception as exc:  # business errors from _apply carry a message
            results.append({"line": i, "employee_code": row.get("employee_code"), "status": "error", "error": getattr(exc, "message", str(exc))})
    audit(db, actor, "workforce.attendance.import", "attendance_record", None, {"imported": ok, "rows": len(results)})
    return {"imported": ok, "errors": len(results) - ok, "rows": results}


def close_attendance(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Mark ended shifts without a check-in as absent and record approved leave, so 'absent' is never a guess."""
    now = now or utcnow()
    A = ShiftAssignment
    stats = {"absent": 0, "on_leave": 0}
    rows = db.execute(select(A).join(Shift, Shift.id == A.shift_id).outerjoin(AttendanceRecord, AttendanceRecord.assignment_id == A.id).where(
        AttendanceRecord.id.is_(None), Shift.status == ShiftStatus.SCHEDULED, A.status.in_([AssignmentStatus.ASSIGNED, AssignmentStatus.ON_LEAVE]),
        A.end_at < now - timedelta(hours=2), A.end_at > now - timedelta(days=14))).unique().scalars().all()
    for a in rows:
        r = _new_record(a, AttendanceSource.SYSTEM, None)
        r.status = AttendanceStatus.ON_LEAVE if a.status == AssignmentStatus.ON_LEAVE else AttendanceStatus.ABSENT
        r.notes = "Approved leave" if a.status == AssignmentStatus.ON_LEAVE else "No check-in recorded"
        db.add(r)
        stats["on_leave" if a.status == AssignmentStatus.ON_LEAVE else "absent"] += 1
    db.flush()
    return stats
