"""Overtime marketplace: vacancies become opportunities; the first eligible claim to commit wins.

Concurrency: a claim locks the employee row, then the opportunity row (always in that order, so claims never
deadlock). Claims on one opportunity therefore run one at a time; the order in which they commit decides the
winner. The partial unique index (one active assignment per position) and the exclusion constraint (no
overlapping shifts per employee) re-check the result inside the database. The client clock plays no part.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.common import audit, next_number
from app.core.database import utcnow
from app.core.deps import Actor
from app.core.errors import (
    BusinessRuleViolation, Conflict, InvalidState, NotFound, ValidationFailed,
)
from app.models import (
    AssignmentSource, AssignmentStatus, ClaimOutcome, ConflictKind, ConflictStatus, Employee, EmployeeStatus, HalfDay,
    OpportunityPosition, OpportunityRecipient, OpportunitySource, OpportunityStatus, OvertimeClaim, OvertimeOpportunity,
    PositionKind, Shift, ShiftAssignment, ShiftPosition, ShiftStatus, WorkforceConflict, team_members,
)
from app.modules.workforce import schemas as s
from app.modules.workforce.core import (
    Scope, company_tz, employee_brief, friendly_date, friendly_time, get_employee, hm, minutes, overtime_policy, require_me,
    users_managing, workforce_policy,
)
from app.modules.workforce.eligibility import RULE_LABELS, Window, evaluate, scheduled_minutes_in_week
from app.modules.workforce.notify import notify

ACTIVE = (OpportunityStatus.DRAFT, OpportunityStatus.PUBLISHED, OpportunityStatus.OPEN, OpportunityStatus.PARTIALLY_FILLED)
CLAIMABLE = (OpportunityStatus.OPEN, OpportunityStatus.PARTIALLY_FILLED)
TAKEN_MESSAGE = "This overtime opportunity has already been claimed by another employee."


# ------------------------------------------------------------------ helpers
def _hours(td_minutes: int) -> str:
    h = Decimal(td_minutes) / 60
    return f"{h.normalize():f}" if h == h.to_integral() else f"{h.quantize(Decimal('0.1')):f}"


def window_of(opp: OvertimeOpportunity) -> Window:
    return Window(opp.start_at, opp.end_at, opp.break_minutes, opp.shift.shift_date, opp.department_id, opp.team_id,
                  [k.id for k in opp.shift.skills], opp.id)


def default_deadline(db: Session, department_id: uuid.UUID, start_at: datetime, now: datetime | None = None) -> datetime:
    now = now or utcnow()
    policy = workforce_policy(db, department_id)
    deadline = start_at - timedelta(hours=float(policy.claim_deadline_hours or 0))
    return deadline if deadline > now else start_at  # urgent vacancy: claims stay open until the shift starts


def effective_status(opp: OvertimeOpportunity, now: datetime | None = None) -> OpportunityStatus:
    now = now or utcnow()
    st = opp.status
    if st == OpportunityStatus.PUBLISHED and opp.claims_open_at and opp.claims_open_at <= now:
        st = OpportunityStatus.OPEN if opp.positions_filled == 0 else OpportunityStatus.PARTIALLY_FILLED
    if st in CLAIMABLE and now >= opp.claim_deadline:
        st = OpportunityStatus.EXPIRED
    return st


def _fill_status(opp: OvertimeOpportunity) -> OpportunityStatus:
    if opp.positions_filled >= opp.positions_total:
        return OpportunityStatus.FILLED
    return OpportunityStatus.PARTIALLY_FILLED if opp.positions_filled else OpportunityStatus.OPEN


def _managers(db: Session, opp: OvertimeOpportunity) -> list[uuid.UUID]:
    return users_managing(db, opp.department_id, "workforce.overtime.manage")


def refresh(db: Session, opp: OvertimeOpportunity, now: datetime | None = None) -> bool:
    """Apply time-based transitions (published → open, open → expired). Safe to call repeatedly."""
    now = now or utcnow()
    changed = False
    if opp.status == OpportunityStatus.PUBLISHED and opp.claims_open_at and opp.claims_open_at <= now:
        opp.status = _fill_status(opp)
        notify_round(db, opp)
        changed = True
    if opp.status in CLAIMABLE and now >= opp.claim_deadline:
        opp.status, opp.expired_at = OpportunityStatus.EXPIRED, now
        tz = company_tz(db)
        notify(db, _managers(db, opp), "overtime_expired", f"Unfilled: {opp.title}",
               f"{opp.number} closed at {hm(opp.claim_deadline, tz)} with {opp.positions_filled} of {opp.positions_total} position(s) filled. "
               "Extend the deadline, notify again, or assign someone manually.",
               {"opportunity_id": str(opp.id), "link": f"/workforce/overtime/{opp.id}"}, dedupe_key=f"otexpired:{opp.id}:{opp.round}")
        changed = True
    if changed:
        db.flush()
    return changed


def tick(db: Session) -> dict[str, int]:
    """Scheduler entry point: open scheduled opportunities and expire past-deadline ones."""
    now = utcnow()
    rows = db.execute(select(OvertimeOpportunity).where(or_(
        (OvertimeOpportunity.status == OpportunityStatus.PUBLISHED) & (OvertimeOpportunity.claims_open_at <= now),
        OvertimeOpportunity.status.in_(CLAIMABLE) & (OvertimeOpportunity.claim_deadline <= now)))
        .with_for_update(skip_locked=True, of=OvertimeOpportunity)).unique().scalars().all()
    stats = {"opened": 0, "expired": 0}
    for o in rows:
        if refresh(db, o, now):
            stats["expired" if o.status == OpportunityStatus.EXPIRED else "opened"] += 1
    return stats


# ------------------------------------------------------------------ creation
def add_cover_position(db: Session, shift: Shift, half: HalfDay, leave_request_id: uuid.UUID | None = None) -> ShiftPosition:
    mid = shift.start_at + (shift.end_at - shift.start_at) / 2
    ws, we = (shift.start_at, mid) if half == HalfDay.FIRST_HALF else (mid, shift.end_at)
    slot = (db.execute(select(func.max(ShiftPosition.slot)).where(ShiftPosition.shift_id == shift.id)).scalar_one() or 0) + 1
    pos = ShiftPosition(shift_id=shift.id, slot=slot, kind=PositionKind.COVER, window_start=ws, window_end=we)
    db.add(pos)
    db.flush()
    return pos


def _title(db: Session, shift: Shift, start_at: datetime, end_at: datetime) -> str:
    tz = company_tz(db)
    partial = (start_at, end_at) != (shift.start_at, shift.end_at)
    return f"{shift.name}{' (part)' if partial else ''} · {shift.department.name} · {shift.shift_date:%a %d %b} {hm(start_at, tz)}–{hm(end_at, tz)}"


def create_for_positions(db: Session, actor: Actor | None, shift: Shift, positions: list[ShiftPosition], *, source: str = "leave",
                         claim_deadline: datetime | None = None, claims_open_at: datetime | None = None,
                         publish_now: bool | None = None, notes: str | None = None,
                         exclude_employee_ids: tuple[uuid.UUID, ...] = ()) -> OvertimeOpportunity:
    """Offer vacant positions of one shift. Positions with the same window join an existing active opportunity."""
    if not positions:
        raise BusinessRuleViolation("No vacant positions to offer")
    now = utcnow()
    if shift.start_at <= now:
        raise InvalidState("This shift has already started")
    p0 = positions[0]
    start_at, end_at = p0.window_start or shift.start_at, p0.window_end or shift.end_at
    full = (start_at, end_at) == (shift.start_at, shift.end_at)
    existing = db.execute(select(OvertimeOpportunity).where(
        OvertimeOpportunity.shift_id == shift.id, OvertimeOpportunity.status.in_(ACTIVE),
        OvertimeOpportunity.start_at == start_at, OvertimeOpportunity.end_at == end_at)
        .with_for_update(of=OvertimeOpportunity)).unique().scalars().first()
    if existing is not None:
        offered = {op.position_id for op in existing.positions if op.is_active}
        fresh = [p for p in positions if p.id not in offered]
        for p in fresh:
            existing.positions.append(OpportunityPosition(position_id=p.id))
        existing.positions_total += len(fresh)
        if existing.status == OpportunityStatus.FILLED:
            existing.status = _fill_status(existing)
        if fresh and existing.status in CLAIMABLE:
            existing.round += 1
            db.flush()
            notify_round(db, existing, exclude_employee_ids)
        audit(db, actor, "workforce.opportunity.extend_positions", "overtime_opportunity", existing.id, {"added": len(fresh)})
        return existing
    policy = workforce_policy(db, shift.department_id)
    deadline = claim_deadline or default_deadline(db, shift.department_id, start_at, now)
    if deadline <= now or deadline > start_at:
        raise ValidationFailed("The claim deadline must be in the future and no later than the shift start")
    do_publish = policy.auto_publish if publish_now is None else publish_now
    opp = OvertimeOpportunity(
        number=next_number(db, "overtime_opportunity"), shift_id=shift.id, department_id=shift.department_id, team_id=shift.team_id,
        title=_title(db, shift, start_at, end_at), start_at=start_at, end_at=end_at, break_minutes=shift.break_minutes if full else 0,
        positions_total=len(positions), positions_filled=0, claim_deadline=deadline, status=OpportunityStatus.DRAFT,
        source=OpportunitySource(source), notes=notes, created_by=actor.user_id if actor else None)
    opp.positions = [OpportunityPosition(position_id=p.id) for p in positions]
    db.add(opp)
    db.flush()
    db.refresh(opp)
    audit(db, actor, "workforce.opportunity.create", "overtime_opportunity", opp.id,
          {"shift_id": shift.id, "positions": len(positions), "source": source, "deadline": deadline})
    if do_publish:
        publish(db, actor, opp, claims_open_at=claims_open_at, exclude_employee_ids=exclude_employee_ids)
    else:
        tz = company_tz(db)
        notify(db, _managers(db, opp), "overtime_available", f"Review and publish: {opp.title}",
               f"{opp.number}: {len(positions)} vacant position(s) on {shift.name} {shift.shift_date:%d %b} "
               f"({hm(start_at, tz)}–{hm(end_at, tz)}). Your department reviews vacancies before publishing.",
               {"opportunity_id": str(opp.id), "link": f"/workforce/overtime/{opp.id}"}, dedupe_key=f"otdraft:{opp.id}")
    return opp


def publish(db: Session, actor: Actor | None, opp: OvertimeOpportunity, *, claims_open_at: datetime | None = None,
            exclude_employee_ids: tuple[uuid.UUID, ...] = ()) -> OvertimeOpportunity:
    if opp.status != OpportunityStatus.DRAFT:
        raise InvalidState(f"Only a draft can be published (this one is {opp.status.value})")
    now = utcnow()
    if opp.claim_deadline <= now:
        raise InvalidState("The claim deadline has passed; extend it first")
    if claims_open_at and claims_open_at >= opp.claim_deadline:
        raise ValidationFailed("Claims must open before the deadline")
    opp.published_at, opp.round = now, opp.round + 1
    if claims_open_at and claims_open_at > now:
        opp.status, opp.claims_open_at = OpportunityStatus.PUBLISHED, claims_open_at
    else:
        opp.status, opp.claims_open_at = OpportunityStatus.OPEN, now
        db.flush()
        notify_round(db, opp, exclude_employee_ids)
    audit(db, actor, "workforce.opportunity.publish", "overtime_opportunity", opp.id, {"claims_open_at": opp.claims_open_at})
    return opp


def candidates(db: Session, opp: OvertimeOpportunity) -> list[Employee]:
    stmt = select(Employee).where(Employee.status == EmployeeStatus.ACTIVE, Employee.overtime_eligible.is_(True))
    if opp.team_id:
        stmt = stmt.where(Employee.id.in_(select(team_members.c.employee_id).where(team_members.c.team_id == opp.team_id)))
    else:
        stmt = stmt.where(Employee.department_id == opp.department_id)
    return list(db.execute(stmt.order_by(Employee.employee_code)).unique().scalars())


def notify_round(db: Session, opp: OvertimeOpportunity, exclude_employee_ids: tuple[uuid.UUID, ...] | list = ()) -> dict[str, int]:
    """Notify every currently eligible employee once for this round. The absent colleague is never named."""
    tz = company_tz(db)
    policy = workforce_policy(db, opp.department_id)
    w = window_of(opp)
    eligible = [e for e in candidates(db, opp) if e.id not in exclude_employee_ids and not evaluate(db, e, w, policy)]
    total_min = minutes(opp.start_at, opp.end_at)
    body = ("An additional shift is available.\n"
            f"Date: {friendly_date(opp.shift.shift_date)}\n"
            f"Department: {opp.department.name}\n"
            f"Shift: {opp.shift.name}\n"
            f"Time: {friendly_time(opp.start_at, tz)}–{friendly_time(opp.end_at, tz)}\n"
            f"Duration: {_hours(total_min)} hours\n"
            f"Positions: {opp.positions_total - opp.positions_filled}\n"
            f"Claim by: {opp.claim_deadline.astimezone(tz):%d %b %H:%M}\n"
            "Interested? Claim this shift in the employee portal.\n"
            "First eligible employee to claim gets the opportunity.")
    reachable = 0
    for e in eligible:
        nid = None
        if e.user_id:
            created = notify(db, [e.user_id], "overtime_available", "NEW OVERTIME OPPORTUNITY", body,
                             {"opportunity_id": str(opp.id), "link": f"/me/overtime/{opp.id}"}, dedupe_key=f"ot:{opp.id}:r{opp.round}")
            nid = created.get(e.user_id)
            reachable += 1
        db.execute(pg_insert(OpportunityRecipient).values(opportunity_id=opp.id, employee_id=e.id, round=opp.round,
                                                          notification_id=nid, reachable=e.user_id is not None,
                                                          notified_at=utcnow()).on_conflict_do_nothing())
    db.flush()
    return {"eligible": len(eligible), "reachable": reachable}


def withdraw_position(db: Session, position: ShiftPosition, *, reason: str) -> None:
    """Take an unfilled position back out of any active opportunity (e.g. the absent employee is back)."""
    for op in db.execute(select(OpportunityPosition).join(OvertimeOpportunity, OvertimeOpportunity.id == OpportunityPosition.opportunity_id)
                         .where(OpportunityPosition.position_id == position.id, OpportunityPosition.is_active.is_(True),
                                OpportunityPosition.assignment_id.is_(None),
                                OvertimeOpportunity.status.in_((*ACTIVE, OpportunityStatus.EXPIRED)))).scalars().all():
        opp = db.get(OvertimeOpportunity, op.opportunity_id)
        op.is_active = False
        opp.positions_total -= 1
        if opp.positions_total <= 0:
            opp.positions_total = max(opp.positions_total, opp.positions_filled)
            opp.status, opp.cancelled_at, opp.cancel_reason = OpportunityStatus.CANCELLED, utcnow(), reason
            _tell_recipients(db, opp, "overtime_cancelled", f"No longer needed: {opp.title}",
                             "This overtime opportunity is no longer available.")
        elif opp.status in CLAIMABLE or opp.status == OpportunityStatus.FILLED:
            opp.status = _fill_status(opp)
        audit(db, None, "workforce.opportunity.position_withdrawn", "overtime_opportunity", opp.id, {"reason": reason})
    db.flush()


def _tell_recipients(db: Session, opp: OvertimeOpportunity, type_: str, title: str, body: str,
                     exclude: set[uuid.UUID] | None = None) -> None:
    rows = db.execute(select(Employee.user_id).join(OpportunityRecipient, OpportunityRecipient.employee_id == Employee.id)
                      .where(OpportunityRecipient.opportunity_id == opp.id, Employee.user_id.is_not(None)).distinct()).scalars().all()
    users = [u for u in rows if not exclude or u not in exclude]
    notify(db, users, type_, title, body, {"opportunity_id": str(opp.id)}, dedupe_key=f"{type_}:{opp.id}:{opp.positions_filled}")


# ------------------------------------------------------------------ the claim
def _lock_opportunity(db: Session, opp_id: uuid.UUID) -> OvertimeOpportunity:
    if db.execute(select(OvertimeOpportunity.id).where(OvertimeOpportunity.id == opp_id).with_for_update()).first() is None:
        raise NotFound("Overtime opportunity not found")
    return db.execute(select(OvertimeOpportunity).where(OvertimeOpportunity.id == opp_id)
                      .execution_options(populate_existing=True)).unique().scalar_one()


def _free_slot(db: Session, opp: OvertimeOpportunity) -> OpportunityPosition | None:
    from app.modules.workforce.shifts import occupant
    for op in sorted((op for op in opp.positions if op.is_active and op.assignment_id is None), key=lambda x: x.position.slot):
        if occupant(db, op.position_id) is None:
            return op
    return None


def _after_fill(db: Session, opp: OvertimeOpportunity, a: ShiftAssignment, *, manual: bool) -> None:
    tz = company_tz(db)
    now = utcnow()
    emp = a.employee
    if emp.user_id:
        notify(db, [emp.user_id], "overtime_confirmed", "Your overtime shift has been confirmed.",
               f"Your overtime shift has been confirmed.\nDate: {friendly_date(opp.shift.shift_date)}\nShift: {opp.shift.name}\n"
               f"Time: {friendly_time(a.start_at, tz)}–{friendly_time(a.end_at, tz)}." +
               ("\nAssigned by your manager." if manual else ""),
               {"opportunity_id": str(opp.id), "assignment_id": str(a.id), "link": "/me/shifts"}, dedupe_key=f"otwon:{a.id}")
    if opp.status == OpportunityStatus.FILLED:
        opp.filled_at = now
        winners = set(db.execute(select(Employee.user_id).join(ShiftAssignment, ShiftAssignment.employee_id == Employee.id)
                                 .where(ShiftAssignment.opportunity_id == opp.id, ShiftAssignment.status == AssignmentStatus.ASSIGNED)).scalars())
        _tell_recipients(db, opp, "overtime_filled", "Overtime opportunity filled", "This overtime opportunity has been filled.",
                         exclude={u for u in winners if u})
    notify(db, _managers(db, opp), "overtime_filled",
           f"{'Assigned' if manual else 'Claimed'}: {opp.title}",
           f"{emp.full_name} {'was assigned' if manual else 'claimed'} {opp.number} "
           f"({opp.positions_filled}/{opp.positions_total} filled).", {"opportunity_id": str(opp.id), "link": f"/workforce/overtime/{opp.id}"},
           dedupe_key=f"otmgr:{a.id}")


def claim(db: Session, actor: Actor, scope: Scope, opp_id: uuid.UUID, idempotency_key: str | None) -> tuple[dict[str, Any], int]:
    """Atomically claim one position. Returns (body, http_status); losing attempts are recorded too."""
    from app.modules.workforce.shifts import insert_assignment
    actor.require("workforce.self")
    emp = require_me(scope)
    db.execute(select(Employee.id).where(Employee.id == emp.id).with_for_update())  # 1. this person's claims, one at a time
    opp = _lock_opportunity(db, opp_id)                                              # 2. this opportunity's claims, one at a time
    refresh(db, opp)

    def outcome(kind: ClaimOutcome, code: str, message: str, details: dict | None = None) -> tuple[dict[str, Any], int]:
        c = OvertimeClaim(opportunity_id=opp.id, employee_id=emp.id, outcome=kind, reason_code=code, reason=message,
                          idempotency_key=idempotency_key)
        db.add(c)
        db.flush()
        db.refresh(c)
        audit(db, actor, f"workforce.claim.{kind.value}", "overtime_opportunity", opp.id, {"reason": code})
        return ({"error": {"code": code, "message": message, "details": {**(details or {}), "claim_id": str(c.id),
                                                                         "claimed_at": c.created_at.isoformat()},
                           "request_id": actor.request_id}}, 409)

    if opp.status == OpportunityStatus.FILLED:
        return outcome(ClaimOutcome.LOST, "opportunity_filled", TAKEN_MESSAGE)
    if opp.status == OpportunityStatus.EXPIRED:
        return outcome(ClaimOutcome.REJECTED, "opportunity_expired", "The claim deadline for this opportunity has passed.")
    if opp.status == OpportunityStatus.CANCELLED:
        return outcome(ClaimOutcome.REJECTED, "opportunity_cancelled", "This overtime opportunity was cancelled.")
    if opp.status in (OpportunityStatus.DRAFT, OpportunityStatus.PUBLISHED):
        return outcome(ClaimOutcome.REJECTED, "not_open", "This opportunity is not open for claims yet.")

    failures = evaluate(db, emp, window_of(opp), workforce_policy(db, opp.department_id))
    if failures:
        f = failures[0]
        return outcome(ClaimOutcome.REJECTED, f.code, f"You can't claim this shift: {f.message}.",
                       {"failures": [x.as_dict() for x in failures]})
    slot = _free_slot(db, opp)
    if slot is None:
        return outcome(ClaimOutcome.LOST, "opportunity_filled", TAKEN_MESSAGE)
    try:
        a = insert_assignment(db, position=slot.position, shift=opp.shift, employee=emp, source=AssignmentSource.MARKETPLACE,
                              start_at=opp.start_at, end_at=opp.end_at, actor=actor, opportunity_id=opp.id)
    except Conflict as exc:
        code = exc.code if exc.code != "position_taken" else "opportunity_filled"
        return outcome(ClaimOutcome.LOST if code == "opportunity_filled" else ClaimOutcome.REJECTED, code,
                       TAKEN_MESSAGE if code == "opportunity_filled" else exc.message)
    slot.assignment_id = a.id
    opp.positions_filled += 1
    opp.status = _fill_status(opp)
    c = OvertimeClaim(opportunity_id=opp.id, employee_id=emp.id, outcome=ClaimOutcome.WON, assignment_id=a.id,
                      idempotency_key=idempotency_key)
    db.add(c)
    db.flush()
    db.refresh(c)
    _after_fill(db, opp, a, manual=False)
    audit(db, actor, "workforce.claim.won", "overtime_opportunity", opp.id, {"assignment_id": a.id, "claim_id": c.id})
    from app.modules.workforce.shifts import assignment_out
    return ({"claim": {"id": c.id, "outcome": "won", "claimed_at": c.created_at},
             "assignment": assignment_out(db, a), "opportunity": opportunity_out(db, opp, scope, for_employee=emp)}, 201)


# ------------------------------------------------------------------ manager actions
def get_opportunity(db: Session, scope: Scope, opp_id: uuid.UUID, *, manage: bool = False, lock: bool = False) -> OvertimeOpportunity:
    opp = _lock_opportunity(db, opp_id) if lock else db.get(OvertimeOpportunity, opp_id)
    if opp is None:
        raise NotFound("Overtime opportunity not found")
    if manage:
        scope.require_department(opp.department_id, "workforce.overtime.manage")
    elif not scope.manages_department(opp.department_id) and not _visible_to(opp, scope.me):
        raise NotFound("Overtime opportunity not found")
    return opp


def _visible_to(opp: OvertimeOpportunity, emp: Employee | None) -> bool:
    if emp is None:
        return False
    if opp.team_id:
        return any(t.id == opp.team_id for t in emp.teams)
    return opp.department_id == emp.department_id


def create_manual(db: Session, actor: Actor, scope: Scope, data: s.OpportunityCreate, source: str = "manual") -> OvertimeOpportunity:
    from app.modules.workforce.shifts import get_shift, occupant
    shift = get_shift(db, data.shift_id)
    scope.require_department(shift.department_id, "workforce.overtime.manage")
    if shift.status != ShiftStatus.SCHEDULED or shift.start_at <= utcnow():
        raise InvalidState("Overtime can only be offered for a future, scheduled shift")
    if data.cover_half != HalfDay.NONE:
        positions = [add_cover_position(db, shift, data.cover_half)]
    else:
        offered = set(db.execute(select(OpportunityPosition.position_id).join(OvertimeOpportunity, OvertimeOpportunity.id == OpportunityPosition.opportunity_id)
                                 .where(OvertimeOpportunity.shift_id == shift.id, OvertimeOpportunity.status.in_(ACTIVE),
                                        OpportunityPosition.is_active.is_(True))).scalars())
        vacant = [p for p in shift.positions if p.is_active and p.kind == PositionKind.REGULAR and p.id not in offered and occupant(db, p.id) is None]
        if not vacant:
            raise BusinessRuleViolation("This shift has no vacant positions that aren't already on offer", code="no_vacancy")
        positions = vacant[: data.positions or len(vacant)]
    return create_for_positions(db, actor, shift, positions, source=source, claim_deadline=data.claim_deadline,
                                claims_open_at=data.claims_open_at, publish_now=data.publish, notes=data.notes)


def extend(db: Session, actor: Actor, scope: Scope, opp_id: uuid.UUID, data: s.ExtendIn) -> OvertimeOpportunity:
    opp = get_opportunity(db, scope, opp_id, manage=True, lock=True)
    refresh(db, opp)
    if opp.status in (OpportunityStatus.FILLED, OpportunityStatus.CANCELLED):
        raise InvalidState(f"This opportunity is {opp.status.value}")
    now = utcnow()
    if data.claim_deadline <= now or data.claim_deadline > opp.start_at:
        raise ValidationFailed("The new deadline must be in the future and no later than the shift start")
    old = opp.claim_deadline
    opp.claim_deadline = data.claim_deadline
    if opp.status == OpportunityStatus.EXPIRED:
        opp.status, opp.expired_at = _fill_status(opp), None
    if data.renotify and opp.status in CLAIMABLE:
        opp.round += 1
        db.flush()
        notify_round(db, opp)
    audit(db, actor, "workforce.opportunity.extend", "overtime_opportunity", opp.id, {"from": old, "to": data.claim_deadline,
                                                                                       "renotify": data.renotify})
    return opp


def renotify(db: Session, actor: Actor, scope: Scope, opp_id: uuid.UUID) -> dict[str, int]:
    opp = get_opportunity(db, scope, opp_id, manage=True, lock=True)
    refresh(db, opp)
    if opp.status not in CLAIMABLE:
        raise InvalidState(f"Only open opportunities can be re-sent (this one is {opp.status.value})")
    opp.round += 1
    db.flush()
    stats = notify_round(db, opp)
    audit(db, actor, "workforce.opportunity.renotify", "overtime_opportunity", opp.id, {"round": opp.round, **stats})
    return stats


def publish_existing(db: Session, actor: Actor, scope: Scope, opp_id: uuid.UUID, data: s.PublishIn) -> OvertimeOpportunity:
    opp = get_opportunity(db, scope, opp_id, manage=True, lock=True)
    return publish(db, actor, opp, claims_open_at=data.claims_open_at)


def cancel(db: Session, actor: Actor, scope: Scope, opp_id: uuid.UUID, reason: str) -> OvertimeOpportunity:
    opp = get_opportunity(db, scope, opp_id, manage=True, lock=True)
    refresh(db, opp)
    if opp.status in (OpportunityStatus.FILLED, OpportunityStatus.CANCELLED):
        raise InvalidState(f"This opportunity is {opp.status.value}; release individual assignments instead")
    opp.status, opp.cancelled_at, opp.cancel_reason = OpportunityStatus.CANCELLED, utcnow(), reason
    _tell_recipients(db, opp, "overtime_cancelled", f"No longer available: {opp.title}", "This overtime opportunity is no longer available.")
    audit(db, actor, "workforce.opportunity.cancel", "overtime_opportunity", opp.id, {"reason": reason})
    db.flush()
    return opp


def manual_assign(db: Session, actor: Actor, scope: Scope, opp_id: uuid.UUID, data: s.ManualAssignIn) -> ShiftAssignment:
    """Authorised manual assignment when the marketplace did not fill the shift. Reason always recorded."""
    from app.modules.workforce.shifts import insert_assignment
    emp = get_employee(db, data.employee_id)
    db.execute(select(Employee.id).where(Employee.id == emp.id).with_for_update())
    opp = get_opportunity(db, scope, opp_id, manage=True, lock=True)
    refresh(db, opp)
    if opp.status in (OpportunityStatus.FILLED, OpportunityStatus.CANCELLED):
        raise InvalidState(f"This opportunity is {opp.status.value}")
    if opp.start_at <= utcnow():
        raise InvalidState("This shift has already started")
    if not (scope.can_see(emp) or emp.department_id == opp.department_id):
        raise NotFound("Employee not found")
    failures = evaluate(db, emp, window_of(opp), workforce_policy(db, opp.department_id))
    hard = [f for f in failures if f.hard]
    if hard:
        raise BusinessRuleViolation(hard[0].message, code=hard[0].code, details={"failures": [f.as_dict() for f in failures]})
    not_overridden = [f for f in failures if f.code not in data.override_rules]
    if not_overridden:
        raise BusinessRuleViolation("This assignment breaks rules; confirm each override to continue", code="rules_failed",
                                    details={"failures": [f.as_dict() for f in failures]})
    slot = _free_slot(db, opp)
    if slot is None:
        raise BusinessRuleViolation(TAKEN_MESSAGE, code="opportunity_filled")
    a = insert_assignment(db, position=slot.position, shift=opp.shift, employee=emp, source=AssignmentSource.MANUAL,
                          start_at=opp.start_at, end_at=opp.end_at, actor=actor, opportunity_id=opp.id, reason=data.reason,
                          overrides=[f.code for f in failures])
    slot.assignment_id = a.id
    opp.positions_filled += 1
    opp.status = _fill_status(opp)
    if opp.status == OpportunityStatus.FILLED:
        opp.expired_at = None
    db.flush()
    _after_fill(db, opp, a, manual=True)
    audit(db, actor, "workforce.opportunity.manual_assign", "overtime_opportunity", opp.id,
          {"employee_id": emp.id, "assignment_id": a.id, "reason": data.reason, "overrides": [f.code for f in failures]})
    return a


# ------------------------------------------------------------------ withdrawals & conflicts
def _release_and_reopen(db: Session, actor: Actor, a: ShiftAssignment, reason: str) -> OvertimeOpportunity | None:
    now = utcnow()
    a.status, a.ended_at, a.end_reason, a.ended_by = AssignmentStatus.RELEASED, now, reason, actor.user_id
    db.flush()
    policy = workforce_policy(db, a.shift.department_id)
    reopened = None
    if policy.auto_reopen_on_withdrawal and a.start_at > now:
        opp = db.get(OvertimeOpportunity, a.opportunity_id) if a.opportunity_id else None
        if opp is not None and opp.status != OpportunityStatus.CANCELLED:
            op = next((x for x in opp.positions if x.assignment_id == a.id), None)
            if op is not None:
                op.assignment_id = None
                opp.positions_filled -= 1
            if opp.claim_deadline <= now:
                opp.claim_deadline = default_deadline(db, opp.department_id, opp.start_at, now)
            opp.status, opp.filled_at, opp.expired_at, opp.source = _fill_status(opp), None, None, OpportunitySource.WITHDRAWAL
            opp.round += 1
            db.flush()
            notify_round(db, opp, exclude_employee_ids=(a.employee_id,))
            reopened = opp
        else:
            reopened = create_for_positions(db, actor, a.shift, [a.position], source="withdrawal", publish_now=True,
                                            exclude_employee_ids=(a.employee_id,))
    return reopened


def withdraw(db: Session, actor: Actor, scope: Scope, assignment_id: uuid.UUID, reason: str) -> dict[str, Any]:
    emp = require_me(scope)
    a = db.get(ShiftAssignment, assignment_id)
    if a is None or a.employee_id != emp.id:
        raise NotFound("Assignment not found")
    if a.status != AssignmentStatus.ASSIGNED or a.source not in (AssignmentSource.MARKETPLACE, AssignmentSource.MANUAL):
        raise InvalidState("Only an active overtime assignment can be withdrawn; ask your manager about regular shifts")
    now = utcnow()
    if a.start_at <= now:
        raise InvalidState("This shift has already started")
    policy = workforce_policy(db, a.shift.department_id)
    cutoff = a.start_at - timedelta(hours=float(policy.withdrawal_cutoff_hours or 0))
    tz = company_tz(db)
    managers = users_managing(db, a.shift.department_id, "workforce.overtime.manage", exclude_user=actor.user_id)
    if now > cutoff:
        c = WorkforceConflict(kind=ConflictKind.WITHDRAWAL_REQUEST, department_id=a.shift.department_id, shift_id=a.shift_id,
                              original_assignment_id=a.id,
                              summary=f"{emp.full_name} asks to withdraw from {a.shift.name} on {a.shift.shift_date:%d %b} "
                                      f"({hm(a.start_at, tz)}–{hm(a.end_at, tz)}), inside the {policy.withdrawal_cutoff_hours:f} h cut-off. "
                                      f"Reason: {reason}")
        db.add(c)
        db.flush()
        notify(db, managers, "workforce_conflict", f"Late withdrawal request: {emp.full_name}", c.summary,
               {"conflict_id": str(c.id), "link": "/workforce/overtime?tab=conflicts"}, dedupe_key=f"conflict:{c.id}")
        audit(db, actor, "workforce.assignment.withdraw_requested", "shift_assignment", a.id, {"reason": reason})
        return {"status": "pending_manager", "conflict_id": c.id,
                "message": "It's within the withdrawal cut-off, so your manager has to approve it. You stay scheduled until then."}
    reopened = _release_and_reopen(db, actor, a, f"Withdrawn by employee: {reason}")
    notify(db, managers, "overtime_withdrawn", f"Withdrawn: {emp.full_name} from {a.shift.name} {a.shift.shift_date:%d %b}",
           f"{emp.full_name} withdrew from {a.shift.name} on {a.shift.shift_date:%d %b}. Reason: {reason}. " +
           (f"{reopened.number} is open for claims again." if reopened else "The position is vacant."),
           {"assignment_id": str(a.id), **({"opportunity_id": str(reopened.id)} if reopened else {})}, dedupe_key=f"otwithdraw:{a.id}")
    audit(db, actor, "workforce.assignment.withdraw", "shift_assignment", a.id, {"reason": reason, "reopened": reopened.id if reopened else None})
    return {"status": "withdrawn", "reopened_opportunity_id": reopened.id if reopened else None,
            "message": "You've been removed from the shift." + (" It has been offered to your colleagues again." if reopened else "")}


def conflict_out(db: Session, c: WorkforceConflict) -> dict[str, Any]:
    from app.modules.workforce.shifts import assignment_out
    orig = db.get(ShiftAssignment, c.original_assignment_id) if c.original_assignment_id else None
    repl = db.get(ShiftAssignment, c.replacement_assignment_id) if c.replacement_assignment_id else None
    return {"id": c.id, "kind": c.kind.value, "status": c.status.value, "summary": c.summary, "department_id": c.department_id,
            "shift": {"id": c.shift.id, "name": c.shift.name, "date": c.shift.shift_date} if c.shift else None,
            "original": assignment_out(db, orig) if orig else None, "replacement": assignment_out(db, repl) if repl else None,
            "resolution": c.resolution, "resolution_note": c.resolution_note, "resolved_at": c.resolved_at, "created_at": c.created_at,
            "options": ["keep_replacement", "reinstate_original"] if c.kind == ConflictKind.LEAVE_CANCELLED_AFTER_COVER
            else ["approve_release", "decline"]}


def list_conflicts(db: Session, scope: Scope, status: str | None = "open") -> list[dict[str, Any]]:
    stmt = scope.department_filter(select(WorkforceConflict), WorkforceConflict.department_id)
    if status:
        stmt = stmt.where(WorkforceConflict.status == status)
    return [conflict_out(db, c) for c in db.execute(stmt.order_by(WorkforceConflict.created_at.desc())).unique().scalars()]


def resolve_conflict(db: Session, actor: Actor, scope: Scope, conflict_id: uuid.UUID, data: s.ResolveIn) -> WorkforceConflict:
    db.execute(select(WorkforceConflict.id).where(WorkforceConflict.id == conflict_id).with_for_update())
    c = db.get(WorkforceConflict, conflict_id)
    if c is None:
        raise NotFound("Conflict not found")
    scope.require_department(c.department_id, "workforce.overtime.manage")
    if c.status != ConflictStatus.OPEN:
        raise InvalidState("This conflict is already resolved")
    allowed = conflict_out(db, c)["options"]
    if data.resolution not in allowed:
        raise ValidationFailed(f"Choose one of: {', '.join(allowed)}")
    tz = company_tz(db)
    orig = db.get(ShiftAssignment, c.original_assignment_id) if c.original_assignment_id else None
    repl = db.get(ShiftAssignment, c.replacement_assignment_id) if c.replacement_assignment_id else None
    now = utcnow()
    if c.kind == ConflictKind.LEAVE_CANCELLED_AFTER_COVER:
        if data.resolution == "keep_replacement":
            if orig is not None and orig.status == AssignmentStatus.ON_LEAVE:
                orig.status, orig.ended_at, orig.ended_by = AssignmentStatus.RELEASED, now, actor.user_id
                orig.end_reason = f"Leave cancelled; cover kept by manager decision: {data.note}"
                if orig.employee.user_id:
                    notify(db, [orig.employee.user_id], "shift_changed", f"Not scheduled: {orig.shift.name} {orig.shift.shift_date:%d %b}",
                           f"Your leave was cancelled, but {orig.shift.name} on {orig.shift.shift_date:%d %b} stays covered by a colleague. "
                           f"You are not scheduled for it. Note from your manager: {data.note}", {"shift_id": str(orig.shift_id)})
        else:
            if repl is None or repl.status != AssignmentStatus.ASSIGNED:
                raise InvalidState("The replacement is no longer assigned")
            if repl.start_at <= now:
                raise InvalidState("The shift has started; it can no longer be switched")
            repl.status, repl.ended_at, repl.ended_by = AssignmentStatus.RELEASED, now, actor.user_id
            repl.end_reason = f"Original employee reinstated: {data.note}"
            db.flush()
            if orig is not None:
                try:
                    with db.begin_nested():
                        orig.status, orig.ended_at, orig.end_reason, orig.ended_by, orig.leave_request_id = AssignmentStatus.ASSIGNED, None, None, None, None
                        db.flush()
                except IntegrityError:
                    raise Conflict(f"{orig.employee.full_name} now works an overlapping shift; keep the replacement instead") from None
                if orig.employee.user_id:
                    notify(db, [orig.employee.user_id], "shift_changed", f"Back on schedule: {orig.shift.name} {orig.shift.shift_date:%d %b}",
                           f"You are scheduled again for {orig.shift.name} on {orig.shift.shift_date:%d %b} "
                           f"({hm(orig.start_at, tz)}–{hm(orig.end_at, tz)}).", {"shift_id": str(orig.shift_id)})
            else:
                repl.position.is_active = False
            if repl.employee.user_id:
                notify(db, [repl.employee.user_id], "shift_changed", f"Overtime cancelled: {repl.shift.name} {repl.shift.shift_date:%d %b}",
                       f"Your overtime shift {repl.shift.name} on {repl.shift.shift_date:%d %b} is no longer needed because the "
                       f"scheduled employee is back. Note from your manager: {data.note}", {"shift_id": str(repl.shift_id)})
    else:  # withdrawal request
        if orig is None:
            raise InvalidState("The assignment no longer exists")
        if data.resolution == "approve_release":
            if orig.status != AssignmentStatus.ASSIGNED:
                raise InvalidState("The employee is no longer assigned")
            _release_and_reopen(db, actor, orig, f"Late withdrawal approved: {data.note}")
            msg = f"Your withdrawal from {orig.shift.name} on {orig.shift.shift_date:%d %b} was approved."
        else:
            msg = f"Your withdrawal from {orig.shift.name} on {orig.shift.shift_date:%d %b} was declined; you remain scheduled. Note: {data.note}"
        if orig.employee.user_id:
            notify(db, [orig.employee.user_id], "shift_changed", "Withdrawal request decided", msg, {"assignment_id": str(orig.id)})
    c.status, c.resolution, c.resolution_note, c.resolved_by, c.resolved_at = ConflictStatus.RESOLVED, data.resolution, data.note, actor.user_id, now
    audit(db, actor, "workforce.conflict.resolve", "workforce_conflict", c.id, {"resolution": data.resolution, "note": data.note})
    return c


# ------------------------------------------------------------------ views
def pay_estimate(db: Session, emp: Employee, opp: OvertimeOpportunity) -> dict[str, Any] | None:
    """Estimate for the employee themself: which part of the shift would likely be overtime, and roughly what it pays.
    Final pay always comes from attendance, policy and approval."""
    policy = workforce_policy(db, opp.department_id)
    otp = overtime_policy(db, emp, opp.shift.overtime_policy_id)
    if otp is None:
        return None
    paid = window_of(opp).paid_minutes
    from app.modules.workforce.leave import is_day_off
    day_type = is_day_off(db, emp, opp.shift.shift_date)
    if day_type == "holiday" and otp.holiday_rule.value == "all_overtime":
        ot, mult = paid, otp.holiday_multiplier
    elif day_type == "weekly_off" and otp.weekly_off_rule.value == "all_overtime":
        ot, mult = paid, otp.weekly_off_multiplier
    else:
        week = scheduled_minutes_in_week(db, emp, opp.shift.shift_date)
        weekly_excess = max(0, week + paid - int(Decimal(otp.weekly_threshold_hours) * 60))
        daily_excess = max(0, paid - int(Decimal(otp.daily_threshold_hours) * 60))
        ot, mult = min(paid, max(weekly_excess, daily_excess)), otp.ot_multiplier
    out: dict[str, Any] = {"payable_minutes": paid, "estimated_overtime_minutes": ot, "regular_minutes": paid - ot,
                           "day_type": day_type or "regular", "multiplier": mult, "estimated_amount": None}
    if policy.show_pay_estimates and emp.base_hourly_rate is not None:
        rate = Decimal(emp.base_hourly_rate)
        amount = rate * Decimal(paid - ot) / 60 + rate * Decimal(mult) * Decimal(ot) / 60
        out["estimated_amount"] = amount.quantize(Decimal("0.01"))
    return out


def opportunity_out(db: Session, opp: OvertimeOpportunity, scope: Scope, *, for_employee: Employee | None = None,
                    detail: bool = False) -> dict[str, Any]:
    tz = company_tz(db)
    status = effective_status(opp)
    total = minutes(opp.start_at, opp.end_at)
    out: dict[str, Any] = {
        "id": opp.id, "number": opp.number, "title": opp.title, "status": status.value, "source": opp.source.value,
        "shift_id": opp.shift_id, "shift_name": opp.shift.name, "date": opp.shift.shift_date,
        "department_id": opp.department_id, "department_name": opp.department.name,
        "team_id": opp.team_id, "team_name": opp.team.name if opp.team else None,
        "work_location_name": opp.shift.location.name if opp.shift.location else None,
        "start_at": opp.start_at, "end_at": opp.end_at, "start": hm(opp.start_at, tz), "end": hm(opp.end_at, tz),
        "total_minutes": total, "break_minutes": opp.break_minutes, "payable_minutes": max(0, total - opp.break_minutes),
        "positions_total": opp.positions_total, "positions_filled": opp.positions_filled,
        "positions_left": max(0, opp.positions_total - opp.positions_filled),
        "required_skills": [k.name for k in opp.shift.skills], "claim_deadline": opp.claim_deadline,
        "claims_open_at": opp.claims_open_at, "round": opp.round, "created_at": opp.created_at, "notes": opp.notes,
    }
    if for_employee is not None:
        mine = db.execute(select(ShiftAssignment.id).where(ShiftAssignment.opportunity_id == opp.id, ShiftAssignment.employee_id == for_employee.id,
                                                           ShiftAssignment.status == AssignmentStatus.ASSIGNED)).scalar_one_or_none()
        failures = [] if mine else evaluate(db, for_employee, window_of(opp), workforce_policy(db, opp.department_id))
        out.update({"claimed_by_me": mine is not None, "my_assignment_id": mine,
                    "eligible": not failures, "ineligible_reasons": [f.message for f in failures],
                    "pay_estimate": pay_estimate(db, for_employee, opp)})
    if detail and scope.manages_department(opp.department_id):
        from app.modules.workforce.shifts import assignment_out
        claims = db.execute(select(OvertimeClaim).where(OvertimeClaim.opportunity_id == opp.id).order_by(OvertimeClaim.created_at)).unique().scalars().all()
        rec = db.execute(select(OpportunityRecipient.round, func.count(), func.count().filter(OpportunityRecipient.reachable.is_(True)))
                         .where(OpportunityRecipient.opportunity_id == opp.id).group_by(OpportunityRecipient.round)
                         .order_by(OpportunityRecipient.round)).all()
        assignments = db.execute(select(ShiftAssignment).where(ShiftAssignment.opportunity_id == opp.id).order_by(ShiftAssignment.created_at)).unique().scalars().all()
        out.update({
            "claims": [{"id": c.id, "employee": employee_brief(c.employee), "outcome": c.outcome.value, "reason_code": c.reason_code,
                        "reason": c.reason, "claimed_at": c.created_at} for c in claims],
            "recipients": [{"round": r, "notified": n, "reachable": reach} for r, n, reach in rec],
            "assignments": [assignment_out(db, a) for a in assignments],
            "expired_at": opp.expired_at, "filled_at": opp.filled_at, "cancelled_at": opp.cancelled_at, "cancel_reason": opp.cancel_reason,
            "published_at": opp.published_at,
            "can_manage": scope.actor.has("workforce.overtime.manage"),
        })
    return out


def list_for_manager(db: Session, scope: Scope, *, status: str | None = None, date_from=None, date_to=None,
                     department_id: uuid.UUID | None = None, page: int = 1, page_size: int = 25) -> dict[str, Any]:
    stmt = scope.department_filter(select(OvertimeOpportunity).join(Shift, Shift.id == OvertimeOpportunity.shift_id),
                                   OvertimeOpportunity.department_id)
    if status:
        stmt = stmt.where(OvertimeOpportunity.status.in_(status.split(",")))
    if department_id:
        stmt = stmt.where(OvertimeOpportunity.department_id == department_id)
    if date_from:
        stmt = stmt.where(Shift.shift_date >= date_from)
    if date_to:
        stmt = stmt.where(Shift.shift_date <= date_to)
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = db.execute(stmt.order_by(OvertimeOpportunity.start_at).limit(page_size).offset((page - 1) * page_size)).unique().scalars().all()
    items = []
    for o in rows:
        d = opportunity_out(db, o, scope)
        d["claim_attempts"] = db.execute(select(func.count()).where(OvertimeClaim.opportunity_id == o.id)).scalar_one()
        d["filled_by"] = [employee_brief(a.employee) for a in db.execute(select(ShiftAssignment).where(
            ShiftAssignment.opportunity_id == o.id, ShiftAssignment.status == AssignmentStatus.ASSIGNED)).unique().scalars()]
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def marketplace_for_employee(db: Session, scope: Scope, *, date_from=None, date_to=None, location: str | None = None,
                             shift_name: str | None = None, min_hours: float | None = None, max_hours: float | None = None,
                             eligible_only: bool = False) -> list[dict[str, Any]]:
    emp = require_me(scope)
    team_ids = [t.id for t in emp.teams]
    now = utcnow()
    stmt = select(OvertimeOpportunity).join(Shift, Shift.id == OvertimeOpportunity.shift_id).where(
        OvertimeOpportunity.status.in_([OpportunityStatus.OPEN, OpportunityStatus.PARTIALLY_FILLED, OpportunityStatus.PUBLISHED]),
        OvertimeOpportunity.claim_deadline > now,
        or_((OvertimeOpportunity.team_id.is_(None)) & (OvertimeOpportunity.department_id == emp.department_id),
            OvertimeOpportunity.team_id.in_(team_ids) if team_ids else False))
    if date_from:
        stmt = stmt.where(Shift.shift_date >= date_from)
    if date_to:
        stmt = stmt.where(Shift.shift_date <= date_to)
    if shift_name:
        stmt = stmt.where(func.lower(Shift.name) == shift_name.lower())
    out = []
    for o in db.execute(stmt.order_by(OvertimeOpportunity.start_at)).unique().scalars():
        d = opportunity_out(db, o, scope, for_employee=emp)
        hrs = d["payable_minutes"] / 60
        if (min_hours is not None and hrs < min_hours) or (max_hours is not None and hrs > max_hours):
            continue
        if location and (d["work_location_name"] or "").lower() != location.lower():
            continue
        if eligible_only and not d["eligible"]:
            continue
        out.append(d)
    return out


def my_assignments(db: Session, scope: Scope, *, upcoming_only: bool = False) -> list[dict[str, Any]]:
    from app.modules.workforce.shifts import assignment_out
    emp = require_me(scope)
    now = utcnow()
    stmt = select(ShiftAssignment).where(ShiftAssignment.employee_id == emp.id,
                                         ShiftAssignment.source.in_([AssignmentSource.MARKETPLACE, AssignmentSource.MANUAL]))
    if upcoming_only:
        stmt = stmt.where(ShiftAssignment.end_at > now, ShiftAssignment.status == AssignmentStatus.ASSIGNED)
    rows = db.execute(stmt.order_by(ShiftAssignment.start_at.desc()).limit(100)).unique().scalars().all()
    policy = workforce_policy(db, emp.department_id)
    out = []
    for a in rows:
        d = assignment_out(db, a)
        cutoff = a.start_at - timedelta(hours=float(policy.withdrawal_cutoff_hours or 0))
        d["withdraw_cutoff"] = cutoff
        d["can_withdraw"] = a.status == AssignmentStatus.ASSIGNED and a.start_at > now
        d["withdraw_needs_approval"] = now > cutoff
        out.append(d)
    return out


def vacancies(db: Session, scope: Scope, date_from, date_to, department_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    from app.modules.workforce.shifts import shift_out
    stmt = scope.department_filter(select(Shift).where(Shift.status == ShiftStatus.SCHEDULED, Shift.shift_date >= date_from,
                                                       Shift.shift_date <= date_to), Shift.department_id)
    if department_id:
        stmt = stmt.where(Shift.department_id == department_id)
    out = []
    for sh in db.execute(stmt.order_by(Shift.start_at)).unique().scalars():
        d = shift_out(db, sh)
        vac = [p for p in d["positions"] if p["vacant"]]
        if vac:
            out.append({"shift": {k: d[k] for k in ("id", "name", "shift_date", "start", "end", "start_at", "end_at", "department_id",
                                                    "department_name", "required_count", "filled")},
                        "vacant_positions": len(vac), "on_offer": sum(1 for p in vac if p["opportunity"]),
                        "opportunities": list({p["opportunity"]["id"]: p["opportunity"] for p in vac if p["opportunity"]}.values()),
                        "absent": [x["employee"] for p in vac for x in p["on_leave"]] if scope.manages_department(sh.department_id) else []})
    return out


def opportunity_brief_for_assignment(db: Session, a: ShiftAssignment) -> dict[str, Any] | None:
    row = db.execute(select(OvertimeOpportunity).join(OpportunityPosition, OpportunityPosition.opportunity_id == OvertimeOpportunity.id)
                     .where(OpportunityPosition.position_id == a.position_id).order_by(OvertimeOpportunity.created_at.desc())).unique().scalars().first()
    if row is None:
        return None
    return {"id": row.id, "number": row.number, "status": effective_status(row).value,
            "positions_filled": row.positions_filled, "positions_total": row.positions_total}


def rule_labels() -> dict[str, str]:
    return RULE_LABELS
