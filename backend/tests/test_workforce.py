"""Workforce & shift management: the ten scenarios from the brief, concurrency, privacy and permissions.

Everything runs through the public API against PostgreSQL, except the low-level concurrency test, which
calls the claim service from separate threads with separate database sessions.
"""
from __future__ import annotations

import threading
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from app.core.database import SessionLocal
from app.core.rate_limit import limiter
from app.core.deps import build_actor
from app.models import (
    AuditLog, Notification, OvertimeClaim, OvertimeOpportunity, ShiftAssignment, User,
)
from tests.conftest import PASSWORD, login, role_id

TZ = ZoneInfo("Asia/Kolkata")
API = "/api/v1"


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def today() -> date:
    return datetime.now(TZ).date()


class Factory:
    """A production department with an Assembly team, two shift templates and employees with logins."""

    def __init__(self, client: TestClient, admin: dict[str, str], n_employees: int = 6):
        self.c, self.admin = client, admin
        depts = {d["code"]: d["id"] for d in client.get(f"{API}/departments", headers=admin).json()}
        self.prod, self.qa = depts["PROD"], depts["QA"]
        self.hr = self.user("hr_admin", [self.prod], "hr")
        self.mgr_email = f"mgr-{uuid.uuid4().hex[:6]}@demo.example.com"
        self.mgr = self.user("shift_manager", [self.prod], email=self.mgr_email)
        self.weld = self.post("/workforce/skills", {"code": "WELD", "name": "Welding"}, self.hr)
        self.team = self.post("/workforce/teams", {"code": "ASM", "name": "Assembly Team", "department_id": self.prod}, self.hr)
        self.morning = self.post("/shift-templates", {"code": "MOR", "name": "Morning", "start_time": "06:00", "end_time": "14:00",
                                                      "break_minutes": 30, "department_id": self.prod, "required_count": 1}, self.hr)
        self.night = self.post("/shift-templates", {"code": "NGT", "name": "Night", "start_time": "22:00", "end_time": "06:00",
                                                    "break_minutes": 30, "department_id": self.prod, "required_count": 1}, self.hr)
        self.emps: dict[str, dict] = {}
        self.h: dict[str, dict[str, str]] = {}
        for name in "ABCDEFGHIJKL"[:n_employees]:
            self.employee(name)
        self.day = today() + timedelta(days=3)

    # -- helpers
    def post(self, path: str, body: dict | None, headers: dict, status: int = 201, extra: dict | None = None) -> dict:
        r = self.c.post(f"{API}{path}", headers={**headers, **(extra or {})}, json=body)
        assert r.status_code == status, f"{path}: {r.status_code} {r.text}"
        return r.json()

    def get(self, path: str, headers: dict, **params) -> dict | list:
        r = self.c.get(f"{API}{path}", headers=headers, params=params)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
        return r.json()

    def user(self, role: str, departments: list[str], prefix: str = "u", email: str | None = None) -> dict[str, str]:
        email = email or f"{prefix}-{uuid.uuid4().hex[:6]}@demo.example.com"
        self.post("/users", {"email": email, "full_name": f"{role} user", "password": PASSWORD,
                             "role_ids": [role_id(self.c, self.admin, role)], "department_ids": departments}, self.admin)
        return login(self.c, email)

    def employee(self, name: str, department: str | None = None, **extra) -> dict:
        body = {"employee_code": f"EMP-{name}", "first_name": f"Worker{name}", "last_name": "Test", "department_id": department or self.prod,
                "team_ids": [self.team["id"]] if (department or self.prod) == self.prod else [], "base_hourly_rate": "200",
                "date_of_joining": "2024-01-01", **extra}
        e = self.post("/employees", body, self.hr)
        self.post(f"/employees/{e['id']}/portal-access", {"password": PASSWORD}, self.hr)
        self.emps[name] = e
        limiter.reset()  # the factory signs in more accounts per minute than the login rate limit allows one client
        self.h[name] = login(self.c, f"EMP-{name}")
        return e

    def shift(self, d: date | None = None, template: dict | None = None, required: int = 1, **extra) -> dict:
        body = {"template_id": (template or self.morning)["id"], "shift_date": str(d or self.day), "required_count": required, **extra}
        return self.post("/shifts", body, self.mgr)

    def assign(self, shift: dict, name: str, headers: dict | None = None, status: int = 201, **extra) -> dict:
        return self.post(f"/shifts/{shift['id']}/assignments", {"employee_id": self.emps[name]["id"], **extra}, headers or self.mgr, status)

    def leave(self, name: str, start: date, end: date | None = None, type_code: str = "CL", **extra) -> dict:
        types = {t["code"]: t["id"] for t in self.get("/leave/types", self.h[name])}
        return self.post("/leave/requests", {"leave_type_id": types[type_code], "start_date": str(start), "end_date": str(end or start),
                                             "reason": "Family function", **extra}, self.h[name])

    def approve(self, req: dict, headers: dict | None = None, status: int = 200) -> dict:
        return self.post(f"/leave/requests/{req['id']}/approve", {}, headers or self.mgr, status)

    def claim(self, opp_id: str, name: str, status: int | None = None, idem: dict | None = None):
        r = self.c.post(f"{API}/overtime/opportunities/{opp_id}/claim", headers={**self.h[name], **(idem or key())})
        if status is not None:
            assert r.status_code == status, r.text
        return r

    def notes(self, name: str, type_: str | None = None) -> list[dict]:
        items = self.get("/notifications", self.h[name], page_size=100)["items"]
        return [n for n in items if type_ is None or n["type"] == type_]

    def mgr_notes(self, type_: str | None = None) -> list[dict]:
        items = self.get("/notifications", self.mgr, page_size=100)["items"]
        return [n for n in items if type_ is None or n["type"] == type_]

    def covered_shift_with_opportunity(self, absent: str = "A") -> tuple[dict, dict]:
        shift = self.shift()
        self.assign(shift, absent)
        res = self.approve(self.leave(absent, self.day))
        assert len(res["opportunities_created"]) == 1
        return shift, res["opportunities_created"][0]


@pytest.fixture
def wf(client: TestClient, admin_headers: dict) -> Factory:
    return Factory(client, admin_headers)


# ================================================================== scenario 1
def test_leave_approval_creates_and_publishes_opportunity(wf: Factory):
    shift = wf.shift()
    wf.assign(shift, "A")
    req = wf.leave("A", wf.day)
    assert req["status"] == "pending" and req["days"] == "1.0"
    assert [s["name"] for s in req["affected_shifts"]] == ["Morning"]
    assert wf.mgr_notes("leave_submitted"), "the manager is told about the request"

    res = wf.approve(req)
    assert res["status"] == "approved" and len(res["opportunities_created"]) == 1
    opp = wf.get(f"/overtime/opportunities/{res['opportunities_created'][0]['id']}", wf.mgr)
    assert opp["status"] == "open" and opp["positions_total"] == 1 and opp["source"] == "leave"

    detail = wf.get(f"/shifts/{shift['id']}", wf.mgr)
    pos = detail["positions"][0]
    assert pos["vacant"] and pos["on_leave"][0]["employee"]["employee_code"] == "EMP-A", "original kept in history"
    assert pos["opportunity"]["id"] == opp["id"]

    for name in "BCDEF":
        n = wf.notes(name, "overtime_available")
        assert len(n) == 1 and n[0]["title"] == "NEW OVERTIME OPPORTUNITY"
        body = n[0]["body"]
        assert "Shift: Morning" in body and "Time: 6 AM–2 PM" in body and "Duration: 8 hours" in body
        assert "WorkerA" not in body and "Casual" not in body and "Family" not in body, "never reveals who is absent or why"
    assert not wf.notes("A", "overtime_available"), "the absent employee is not offered their own shift"
    assert wf.notes("A", "leave_decided")[0]["title"].startswith("Leave approved")


# ================================================================== scenario 2
def test_first_claim_wins_and_everyone_else_is_told(wf: Factory):
    shift, opp = wf.covered_shift_with_opportunity()
    r = wf.claim(opp["id"], "B", 201)
    assert r.json()["claim"]["outcome"] == "won"
    lost = wf.claim(opp["id"], "C", 409).json()["error"]
    assert lost["code"] == "opportunity_filled"
    assert lost["message"] == "This overtime opportunity has already been claimed by another employee."

    assert wf.notes("B", "overtime_confirmed")[0]["body"].startswith("Your overtime shift has been confirmed.")
    for name in "CDEF":
        assert wf.notes(name, "overtime_filled")[0]["body"] == "This overtime opportunity has been filled."
    assert not wf.notes("B", "overtime_filled")
    assert wf.mgr_notes("overtime_filled")

    mine = wf.get("/workforce/me/schedule", wf.h["B"], date_from=str(wf.day), date_to=str(wf.day))
    assert [s["id"] for s in mine["shifts"]] == [shift["id"]], "the shift appears in the winner's schedule"
    detail = wf.get(f"/overtime/opportunities/{opp['id']}", wf.mgr)
    assert detail["status"] == "filled"
    assert [(c["employee"]["employee_code"], c["outcome"]) for c in detail["claims"]] == [("EMP-B", "won"), ("EMP-C", "lost")]


# ================================================================== scenario 3
def test_simultaneous_claims_through_the_api_have_one_winner(client: TestClient, admin_headers: dict):
    wf = Factory(client, admin_headers, n_employees=10)
    _, opp = wf.covered_shift_with_opportunity()
    names = list("BCDEFGHIJ")
    barrier = threading.Barrier(len(names))
    results: dict[str, int] = {}

    def go(name: str) -> None:
        barrier.wait()
        results[name] = wf.claim(opp["id"], name).status_code

    threads = [threading.Thread(target=go, args=(n,)) for n in names]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results.values()) == [201] + [409] * (len(names) - 1)
    with SessionLocal() as db:
        assigned = db.execute(select(ShiftAssignment).where(ShiftAssignment.opportunity_id == uuid.UUID(opp["id"]),
                                                            ShiftAssignment.status == "assigned")).unique().scalars().all()
        claims = db.execute(select(OvertimeClaim).where(OvertimeClaim.opportunity_id == uuid.UUID(opp["id"]))).unique().scalars().all()
    assert len(assigned) == 1
    assert sorted(c.outcome.value for c in claims) == ["lost"] * (len(names) - 1) + ["won"]


def test_concurrent_claim_service_respects_position_count(client: TestClient, admin_headers: dict):
    """Separate threads, separate DB sessions, straight into the claim service: 3 positions → exactly 3 winners."""
    from app.modules.workforce import marketplace
    from app.modules.workforce.core import get_scope
    wf = Factory(client, admin_headers, n_employees=12)
    shift = wf.shift(required=3)
    for n in "ABC":
        wf.assign(shift, n)
    for n in "ABC":
        wf.approve(wf.leave(n, wf.day))
    opps = wf.get("/overtime/opportunities", wf.mgr, status="open")["items"]
    assert len(opps) == 1 and opps[0]["positions_total"] == 3, "three absences on one shift = one opportunity with 3 positions"
    opp_id = uuid.UUID(opps[0]["id"])
    names = list("DEFGHIJKL")
    with SessionLocal() as db:
        user_ids = {n: db.execute(select(User.id).where(User.email == f"emp-{n.lower()}@employees.invalid")).scalar_one() for n in names}
    barrier = threading.Barrier(len(names))
    outcomes: dict[str, int] = {}

    def go(name: str) -> None:
        with SessionLocal() as db:
            actor = build_actor(db, db.get(User, user_ids[name]))
            scope = get_scope(db, actor)
            barrier.wait()
            _, status = marketplace.claim(db, actor, scope, opp_id, None)
            db.commit()
            outcomes[name] = status

    threads = [threading.Thread(target=go, args=(n,)) for n in names]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(outcomes.values()) == [201] * 3 + [409] * (len(names) - 3)
    with SessionLocal() as db:
        opp = db.get(OvertimeOpportunity, opp_id)
        assert opp.positions_filled == 3 and opp.status.value == "filled"
        n_assigned = db.execute(text("SELECT count(*) FROM shift_assignments WHERE opportunity_id = :o AND status = 'assigned'"),
                                {"o": opp_id}).scalar_one()
    assert n_assigned == 3, "staffing never exceeds the positions offered"


def test_claim_is_idempotent(wf: Factory):
    _, opp = wf.covered_shift_with_opportunity()
    k = key()
    first = wf.claim(opp["id"], "B", 201, idem=k)
    again = wf.claim(opp["id"], "B", 201, idem=k)
    assert again.headers.get("Idempotent-Replay") == "true" and again.json() == first.json()
    lost_key = key()
    l1 = wf.claim(opp["id"], "C", 409, idem=lost_key)
    l2 = wf.claim(opp["id"], "C", 409, idem=lost_key)
    assert l1.json() == l2.json(), "a lost claim replays as lost"
    with SessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM overtime_claims WHERE opportunity_id = :o"), {"o": opp["id"]}).scalar_one() == 2


# ================================================================== scenario 4
def test_claim_rejected_when_already_scheduled_elsewhere(wf: Factory):
    _, opp = wf.covered_shift_with_opportunity()
    other = wf.post("/shifts", {"shift_date": str(wf.day), "name": "Day", "start_time": "08:00", "end_time": "16:00",
                                "department_id": wf.prod, "required_count": 1}, wf.mgr)
    wf.assign(other, "B")
    err = wf.claim(opp["id"], "B", 409).json()["error"]
    assert err["code"] == "shift_conflict"
    market = wf.get("/overtime/marketplace", wf.h["B"])
    assert market[0]["eligible"] is False and "Already working Day" in market[0]["ineligible_reasons"][0]
    with SessionLocal() as db:
        c = db.execute(select(OvertimeClaim).where(OvertimeClaim.opportunity_id == uuid.UUID(opp["id"]))).unique().scalar_one()
    assert c.outcome.value == "rejected" and c.reason_code == "shift_conflict"


def test_database_blocks_double_booking_across_midnight(wf: Factory):
    night = wf.shift(template=wf.night)                                   # 22:00 day → 06:00 day+1
    morning = wf.shift(d=wf.day + timedelta(days=1))                      # 06:00 day+1 → 14:00 (touches, no overlap)
    early = wf.post("/shifts", {"shift_date": str(wf.day + timedelta(days=1)), "name": "Early", "start_time": "05:00",
                                "end_time": "09:00", "department_id": wf.prod, "required_count": 1}, wf.mgr)
    wf.assign(night, "B")
    assert night["overnight"] is True
    err = wf.assign(early, "B", status=409, override_rules=["rest_period"], reason="test")
    assert err["error"]["code"] == "shift_conflict"
    # Back-to-back is not an overlap, but breaks the rest rule unless a manager overrides it with a reason.
    err = wf.assign(morning, "B", status=409)
    assert err["error"]["code"] == "rules_failed" and err["error"]["details"]["failures"][0]["code"] == "rest_period"
    wf.assign(morning, "B", override_rules=["rest_period"], reason="Line restart, agreed with employee")
    with SessionLocal() as db, pytest.raises(Exception, match="ex_assignments_no_overlap"):
        a = db.execute(select(ShiftAssignment).where(ShiftAssignment.shift_id == uuid.UUID(night["id"]))).unique().scalar_one()
        db.execute(text("INSERT INTO shift_assignments (id, position_id, shift_id, employee_id, start_at, end_at, source, status, created_at) "
                        "SELECT gen_random_uuid(), p.id, p.shift_id, :e, :s, :f, 'manual', 'assigned', now() FROM shift_positions p "
                        "WHERE p.shift_id = :sh"), {"e": a.employee_id, "s": a.start_at + timedelta(hours=1), "f": a.end_at,
                                                    "sh": uuid.UUID(early["id"])})


# ================================================================== scenario 5
def test_weekly_hour_limit_rejects_claim_and_manager_can_override_with_reason(wf: Factory):
    _, opp = wf.covered_shift_with_opportunity()
    other_day = wf.shift(d=wf.day + timedelta(days=1) if (wf.day + timedelta(days=1)).weekday() != 0 else wf.day - timedelta(days=1))
    wf.assign(other_day, "B")
    wf.c.patch(f"{API}/employees/{wf.emps['B']['id']}", headers=wf.hr, json={"weekly_hour_limit": "10"})
    err = wf.claim(opp["id"], "B", 409).json()["error"]
    assert err["code"] == "weekly_hours" and "limit is 10 h" in err["message"]

    blocked = wf.post(f"/overtime/opportunities/{opp['id']}/assign", {"employee_id": wf.emps["B"]["id"], "reason": "Urgent order"},
                      wf.mgr, status=409)
    assert blocked["error"]["code"] == "rules_failed"
    a = wf.post(f"/overtime/opportunities/{opp['id']}/assign",
                {"employee_id": wf.emps["B"]["id"], "reason": "Urgent export order; employee agreed", "override_rules": ["weekly_hours"]}, wf.mgr)
    assert a["source"] == "manual" and a["rule_overrides"] == ["weekly_hours"]


# ================================================================== scenario 6
def test_unclaimed_opportunity_expires_and_manager_can_recover(wf: Factory):
    from app.modules.workforce import marketplace
    _, opp = wf.covered_shift_with_opportunity()
    with SessionLocal() as db:
        db.execute(update(OvertimeOpportunity).where(OvertimeOpportunity.id == uuid.UUID(opp["id"]))
                   .values(claim_deadline=datetime.now(TZ) - timedelta(minutes=1)))
        db.commit()
        stats = marketplace.tick(db)
        db.commit()
    assert stats["expired"] == 1
    assert wf.get(f"/overtime/opportunities/{opp['id']}", wf.mgr)["status"] == "expired"
    note = wf.mgr_notes("overtime_expired")
    assert note and "Extend the deadline" in note[0]["body"]
    err = wf.claim(opp["id"], "B", 409).json()["error"]
    assert err["code"] == "opportunity_expired"

    new_deadline = (datetime.now(TZ) + timedelta(hours=12)).isoformat()
    ext = wf.post(f"/overtime/opportunities/{opp['id']}/extend", {"claim_deadline": new_deadline, "renotify": True}, wf.mgr, 200)
    assert ext["status"] == "open" and ext["round"] == 2
    assert len(wf.notes("C", "overtime_available")) == 2, "a second notification round went out"
    wf.claim(opp["id"], "C", 201)


def test_manual_assignment_after_expiry(wf: Factory):
    _, opp = wf.covered_shift_with_opportunity()
    with SessionLocal() as db:
        db.execute(update(OvertimeOpportunity).where(OvertimeOpportunity.id == uuid.UUID(opp["id"]))
                   .values(claim_deadline=datetime.now(TZ) - timedelta(minutes=1)))
        db.commit()
    a = wf.post(f"/overtime/opportunities/{opp['id']}/assign", {"employee_id": wf.emps["D"]["id"], "reason": "Nobody claimed; D agreed by phone"}, wf.mgr)
    assert a["source"] == "manual"
    assert wf.get(f"/overtime/opportunities/{opp['id']}", wf.mgr)["status"] == "filled"


# ================================================================== scenario 7
def test_winner_withdraws_and_opportunity_reopens(wf: Factory):
    _, opp = wf.covered_shift_with_opportunity()
    won = wf.claim(opp["id"], "B", 201).json()
    res = wf.post(f"/assignments/{won['assignment']['id']}/withdraw", {"reason": "Child unwell"}, wf.h["B"], 200)
    assert res["status"] == "withdrawn" and res["reopened_opportunity_id"] == opp["id"]
    detail = wf.get(f"/overtime/opportunities/{opp['id']}", wf.mgr)
    assert detail["status"] == "open" and detail["positions_filled"] == 0 and detail["round"] == 2
    assert wf.mgr_notes("overtime_withdrawn")
    assert len(wf.notes("C", "overtime_available")) == 2
    assert len(wf.notes("B", "overtime_available")) == 1, "the person who withdrew is not re-offered the shift"
    wf.claim(opp["id"], "C", 201)


def test_late_withdrawal_needs_manager_decision(wf: Factory):
    soon = datetime.now(TZ) + timedelta(hours=6)
    shift = wf.post("/shifts", {"shift_date": str(soon.date()), "name": "Urgent", "start_time": soon.strftime("%H:%M"),
                                "end_time": (soon + timedelta(hours=8)).strftime("%H:%M"), "department_id": wf.prod,
                                "required_count": 1}, wf.mgr)
    opp = wf.post("/overtime/opportunities", {"shift_id": shift["id"]}, wf.mgr)
    won = wf.claim(opp["id"], "B", 201).json()
    res = wf.post(f"/assignments/{won['assignment']['id']}/withdraw", {"reason": "Bus strike"}, wf.h["B"], 200)
    assert res["status"] == "pending_manager"
    conflicts = wf.get("/workforce/conflicts", wf.mgr)
    assert conflicts[0]["kind"] == "withdrawal_request"
    wf.post(f"/workforce/conflicts/{conflicts[0]['id']}/resolve", {"resolution": "approve_release", "note": "Understood, re-offering"}, wf.mgr, 200)
    assert wf.get(f"/overtime/opportunities/{opp['id']}", wf.mgr)["status"] == "open"


# ================================================================== scenario 8
def test_attendance_drives_overtime_approval_and_payroll_export(wf: Factory):
    shift, opp = wf.covered_shift_with_opportunity()
    won = wf.claim(opp["id"], "B", 201).json()
    a_id = won["assignment"]["id"]
    with SessionLocal() as db:  # move the covered shift into the past so attendance can be recorded
        past = today() - timedelta(days=2)
        delta = datetime.combine(past, time(6), TZ) - datetime.fromisoformat(shift["start_at"])
        db.execute(text("UPDATE shifts SET shift_date = :d, start_at = start_at + :x, end_at = end_at + :x WHERE id = :s"),
                   {"d": past, "x": delta, "s": shift["id"]})
        db.execute(text("UPDATE shift_assignments SET start_at = start_at + :x, end_at = end_at + :x WHERE shift_id = :s"),
                   {"x": delta, "s": shift["id"]})
        db.commit()
    cin = datetime.combine(past, time(6, 0), TZ)
    rec = wf.post("/attendance/record", {"assignment_id": a_id, "check_in_at": cin.isoformat(),
                                         "check_out_at": (cin + timedelta(hours=11)).isoformat()}, wf.mgr, 200)
    assert rec["worked_minutes"] == 630 and rec["status"] == "present"  # 11 h − 30 min break

    calcs = wf.get("/overtime/calculations", wf.hr, date_from=str(past), date_to=str(past))
    assert len(calcs) == 1
    c = calcs[0]
    assert c["payable_minutes"] == 90 and c["ot_daily_minutes"] == 90 and c["status"] == "calculated"  # 10.5 h − 9 h threshold
    assert Decimal(c["amount"]) == Decimal("600.00")  # 1.5 h × 200 × 2
    mine = wf.get("/overtime/calculations", wf.h["B"], date_from=str(past), date_to=str(past), mine=True)
    assert mine[0]["amount"] == c["amount"], "employees see their own estimate"
    assert "amount" not in wf.get("/overtime/calculations", wf.mgr, date_from=str(past), date_to=str(past))[0], "rates are HR-only"

    wf.post(f"/overtime/calculations/{c['id']}/approve", {}, wf.mgr, 200)
    batch = wf.post("/payroll/exports", {"period_start": str(past.replace(day=1)), "period_end": str(today())}, wf.hr, extra=key())
    assert batch["line_count"] == 1 and batch["total_minutes"] == 90 and Decimal(batch["total_amount"]) == Decimal("600.00")
    csv_text = wf.c.get(f"{API}/payroll/exports/{batch['id']}/download", headers=wf.hr).text
    assert "EMP-B" in csv_text and "1.50" in csv_text and "600.00" in csv_text
    xlsx = wf.c.get(f"{API}/payroll/exports/{batch['id']}/download", headers=wf.hr, params={"format": "xlsx"})
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    err = wf.post("/payroll/exports", {"period_start": str(past.replace(day=1)), "period_end": str(today())}, wf.hr, 409, extra=key())
    assert err["error"]["code"] == "nothing_to_export", "an exported record cannot be exported twice"

    # A later correction never edits the exported line; it adds a correction revision for the next batch.
    wf.post("/attendance/record", {"assignment_id": a_id, "check_out_at": (cin + timedelta(hours=12)).isoformat()}, wf.mgr, 200)
    rows = wf.get("/overtime/calculations", wf.hr, date_from=str(past), date_to=str(past))
    by_rev = {r["revision"]: r for r in rows}
    assert by_rev[0]["status"] == "exported" and by_rev[0]["payable_minutes"] == 90
    assert by_rev[1]["is_correction"] and by_rev[1]["payable_minutes"] == 60 and by_rev[1]["status"] == "calculated"

    # Dashboards and reports aggregate the same records.
    dash = wf.get("/workforce/dashboard", wf.mgr, day=str(past))
    assert dash["overtime_by_employee"][0]["employee"]["employee_code"] == "EMP-B"
    rep = wf.get("/workforce/reports/overtime", wf.hr, date_from=str(past), date_to=str(past))
    assert rep["rows"][0]["payable_minutes"] == 150 and rep["totals"]["amount"]
    assert "amount" not in wf.get("/workforce/reports/overtime", wf.mgr, date_from=str(past), date_to=str(past))["totals"]
    assert wf.get("/workforce/reports/attendance", wf.mgr, date_from=str(past), date_to=str(past))["rows"][0]["present"] == 1
    assert wf.get("/workforce/reports/workforce", wf.mgr, date_from=str(past), date_to=str(past))["overtime_marketplace"]["filled"] == 1


def test_regular_hours_are_not_overtime(wf: Factory):
    past = today() - timedelta(days=2)
    a = wf.assign(wf.shift(d=past), "B")
    cin = datetime.combine(past, time(6, 0), TZ)
    wf.post("/attendance/record", {"assignment_id": a["id"], "check_in_at": cin.isoformat(),
                                   "check_out_at": (cin + timedelta(hours=8)).isoformat()}, wf.mgr, 200)
    assert wf.get("/overtime/calculations", wf.hr, date_from=str(past), date_to=str(past)) == [], "7.5 h worked: no overtime"


# ================================================================== scenario 9
def test_leave_cancelled_after_cover_raises_conflict_and_manager_resolves(wf: Factory):
    shift, opp = wf.covered_shift_with_opportunity()
    wf.claim(opp["id"], "B", 201)
    req = wf.get("/leave/requests", wf.h["A"], mine=True)["items"][0]
    res = wf.post(f"/leave/requests/{req['id']}/cancel", {"reason": "Function postponed"}, wf.h["A"], 200)
    assert res["status"] == "cancelled" and len(res["conflicts"]) == 1 and res["restored_shifts"] == 0
    pos = wf.get(f"/shifts/{shift['id']}", wf.mgr)["positions"][0]
    assert pos["assignment"]["employee"]["employee_code"] == "EMP-B", "the replacement is not removed silently"
    assert wf.mgr_notes("workforce_conflict")

    conflict = res["conflicts"][0]
    wf.post(f"/workforce/conflicts/{conflict['id']}/resolve", {"resolution": "reinstate_original", "note": "A is back; B released"}, wf.mgr, 200)
    pos = wf.get(f"/shifts/{shift['id']}", wf.mgr)["positions"][0]
    assert pos["assignment"]["employee"]["employee_code"] == "EMP-A"
    assert any("no longer needed" in n["body"] for n in wf.notes("B", "shift_changed"))
    balances = {b["code"]: b for b in wf.get("/leave/balances", wf.h["A"])}
    assert balances["CL"]["used"] == "0.0", "cancelled leave is returned to the balance"


def test_leave_cancelled_before_cover_restores_shift_and_withdraws_offer(wf: Factory):
    shift, opp = wf.covered_shift_with_opportunity()
    req = wf.get("/leave/requests", wf.h["A"], mine=True)["items"][0]
    res = wf.post(f"/leave/requests/{req['id']}/cancel", {"reason": "Plans changed"}, wf.h["A"], 200)
    assert res["restored_shifts"] == 1 and res["conflicts"] == []
    assert wf.get(f"/overtime/opportunities/{opp['id']}", wf.mgr)["status"] == "cancelled"
    pos = wf.get(f"/shifts/{shift['id']}", wf.mgr)["positions"][0]
    assert pos["assignment"]["employee"]["employee_code"] == "EMP-A"
    assert wf.claim(opp["id"], "B", 409).json()["error"]["code"] == "opportunity_cancelled"


# ================================================================== scenario 10
def test_manual_assignment_saves_reason_and_audit(wf: Factory):
    _, opp = wf.covered_shift_with_opportunity()
    reason = "Only certified welder available; agreed with supervisor"
    a = wf.post(f"/overtime/opportunities/{opp['id']}/assign", {"employee_id": wf.emps["E"]["id"], "reason": reason}, wf.mgr)
    assert a["source"] == "manual" and a["reason"] == reason
    with SessionLocal() as db:
        log = db.execute(select(AuditLog).where(AuditLog.action == "workforce.opportunity.manual_assign")).scalar_one()
    assert log.changes["reason"] == reason and log.entity_id == opp["id"]
    assert wf.notes("E", "overtime_confirmed")[0]["body"].endswith("Assigned by your manager.")
    assert wf.claim(opp["id"], "B", 409).json()["error"]["code"] == "opportunity_filled"


# ================================================================== rules, privacy, permissions
def test_eligibility_skills_team_and_overtime_flag(wf: Factory):
    t = wf.post("/shift-templates", {"code": "WLD", "name": "Welding", "start_time": "14:00", "end_time": "22:00", "department_id": wf.prod,
                                     "required_count": 1, "team_id": wf.team["id"], "skill_ids": [wf.weld["id"]]}, wf.hr)
    s = wf.shift(template=t)
    wf.assign(s, "A", override_rules=["missing_skill"], reason="Training shift")
    wf.c.patch(f"{API}/employees/{wf.emps['B']['id']}", headers=wf.hr, json={"skills": [{"skill_id": wf.weld["id"]}]})
    wf.c.patch(f"{API}/employees/{wf.emps['C']['id']}", headers=wf.hr, json={"skills": [{"skill_id": wf.weld["id"]}], "overtime_eligible": False})
    res = wf.approve(wf.leave("A", wf.day))
    opp_id = res["opportunities_created"][0]["id"]
    assert wf.notes("B", "overtime_available") and not wf.notes("D", "overtime_available") and not wf.notes("C", "overtime_available")
    assert wf.claim(opp_id, "D", 409).json()["error"]["code"] == "missing_skill"
    assert wf.claim(opp_id, "C", 409).json()["error"]["code"] == "not_overtime_eligible"
    wf.claim(opp_id, "B", 201)


def test_employee_privacy_and_scope(wf: Factory):
    other = wf.employee("Q", department=wf.qa)
    page = wf.get("/employees", wf.h["B"])
    assert [e["employee_code"] for e in page["items"]] == ["EMP-B"], "employees see only themselves"
    assert wf.c.get(f"{API}/employees/{wf.emps['C']['id']}", headers=wf.h["B"]).status_code == 404
    assert wf.c.get(f"{API}/workforce/dashboard", headers=wf.h["B"]).status_code == 403
    assert wf.c.get(f"{API}/employees/{other['id']}", headers=wf.mgr).status_code == 404, "managers see only their departments"
    assert "base_hourly_rate" not in wf.get(f"/employees/{wf.emps['B']['id']}", wf.mgr)
    assert wf.get(f"/employees/{wf.emps['B']['id']}", wf.hr)["base_hourly_rate"] == "200.0000"

    req = wf.leave("A", wf.day)
    assert wf.c.get(f"{API}/leave/requests/{req['id']}", headers=wf.h["B"]).status_code == 404
    assert wf.c.post(f"{API}/leave/requests/{req['id']}/approve", headers=wf.h["A"], json={}).status_code == 403
    assert wf.get(f"/leave/requests/{req['id']}", wf.mgr)["reason"] == "Family function"

    qa_shift = wf.post("/shifts", {"shift_date": str(wf.day), "name": "QA", "start_time": "09:00", "end_time": "17:00",
                                   "department_id": wf.qa, "required_count": 1}, wf.hr)
    qa_opp = wf.post("/overtime/opportunities", {"shift_id": qa_shift["id"]}, wf.hr)
    assert all(o["id"] != qa_opp["id"] for o in wf.get("/overtime/marketplace", wf.h["B"]))
    assert wf.c.get(f"{API}/overtime/opportunities/{qa_opp['id']}", headers=wf.h["B"]).status_code == 404
    assert wf.c.post(f"{API}/shifts", headers=wf.mgr, json={"shift_date": str(wf.day), "name": "X", "start_time": "09:00",
                                                           "end_time": "17:00", "department_id": wf.qa}).status_code == 403


def test_manager_cannot_approve_own_leave(wf: Factory):
    mgr_emp = wf.post("/employees", {"employee_code": "EMP-M", "first_name": "Manager", "department_id": wf.prod}, wf.hr)
    with SessionLocal() as db:
        uid = db.execute(select(User.id).where(User.email == wf.mgr_email)).scalar_one()
    wf.post(f"/employees/{mgr_emp['id']}/link-user", {"user_id": str(uid)}, wf.hr, 200)
    types = {t["code"]: t["id"] for t in wf.get("/leave/types", wf.mgr)}
    req = wf.post("/leave/requests", {"leave_type_id": types["CL"], "start_date": str(wf.day), "end_date": str(wf.day)}, wf.mgr)
    err = wf.post(f"/leave/requests/{req['id']}/approve", {}, wf.mgr, 403)
    assert err["error"]["code"] == "self_approval_forbidden"
    wf.approve(req, wf.hr)


def test_leave_balance_documents_and_half_days(wf: Factory):
    sick = wf.leave("B", wf.day, type_code="SL", submit=False)
    assert sick["status"] == "draft"
    err = wf.post(f"/leave/requests/{sick['id']}/submit", None, wf.h["B"], 422)
    assert err["error"]["code"] == "document_required"
    pdf = b"%PDF-1.4\n%test\n"
    r = wf.c.post(f"{API}/leave/requests/{sick['id']}/attachments", headers=wf.h["B"], files={"file": ("note.pdf", pdf, "application/pdf")})
    assert r.status_code == 201
    att_id = r.json()["id"]
    wf.post(f"/leave/requests/{sick['id']}/submit", None, wf.h["B"], 200)
    assert wf.c.get(f"{API}/leave/requests/{sick['id']}/attachments/{att_id}", headers=wf.h["C"]).status_code == 404
    assert wf.c.get(f"{API}/attachments/{att_id}", headers=wf.h["C"]).status_code == 403, "the generic route never serves leave documents"
    assert wf.c.get(f"{API}/leave/requests/{sick['id']}/attachments/{att_id}", headers=wf.mgr).status_code == 200

    half = wf.leave("C", wf.day, half_day="first_half")
    assert half["days"] == "0.5"
    too_much = wf.post("/leave/requests", {"leave_type_id": half["leave_type_id"], "start_date": str(wf.day + timedelta(days=7)),
                                           "end_date": str(wf.day + timedelta(days=30))}, wf.h["D"], 409)
    assert too_much["error"]["code"] == "insufficient_balance"
    wf.approve(half)
    bal = {b["code"]: b for b in wf.get("/leave/balances", wf.h["C"])}
    assert bal["CL"]["used"] == "0.5"


def test_self_check_in_follows_policy(wf: Factory):
    soon = datetime.now(TZ) + timedelta(minutes=30)
    s = wf.post("/shifts", {"shift_date": str(soon.date()), "name": "Now", "start_time": soon.strftime("%H:%M"),
                            "end_time": (soon + timedelta(hours=8)).strftime("%H:%M"), "department_id": wf.prod, "required_count": 1}, wf.mgr)
    wf.assign(s, "B")
    r = wf.c.post(f"{API}/attendance/check-in", headers={**wf.h["B"], **key()}, json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "self_check_in_disabled"
    pol = wf.get("/workforce/policies", wf.hr)[0]
    wf.c.put(f"{API}/workforce/policies", headers=wf.hr, json={**{k: v for k, v in pol.items() if k not in ("id", "department_name")},
                                                               "allow_self_check_in": True})
    r = wf.c.post(f"{API}/attendance/check-in", headers={**wf.h["B"], **key()}, json={})
    assert r.status_code == 201 and r.json()["source"] == "self"
    again = wf.c.post(f"{API}/attendance/check-in", headers={**wf.h["B"], **key()}, json={})
    assert again.status_code == 409


def test_pattern_generation_and_copy_week(wf: Factory):
    monday = wf.day - timedelta(days=wf.day.weekday()) + timedelta(days=7)
    pattern = wf.post("/shift-patterns", {"code": "5X2", "name": "Mornings, weekends off", "cycle_days": 7,
                                          "days": [wf.morning["id"]] * 5 + [None, None]}, wf.hr)
    for n in "BC":
        wf.c.patch(f"{API}/employees/{wf.emps[n]['id']}", headers=wf.hr,
                   json={"shift_pattern_id": pattern["id"], "pattern_anchor_date": str(monday)})
    wf.c.patch(f"{API}/shift-templates/{wf.morning['id']}", headers=wf.hr, json={"required_count": 2})
    res = wf.post("/shifts/generate", {"date_from": str(monday), "date_to": str(monday + timedelta(days=6)), "department_id": wf.prod}, wf.mgr, 200)
    assert res["shifts_created"] == 5 and res["assignments_created"] == 10 and res["skipped"] == []
    again = wf.post("/shifts/generate", {"date_from": str(monday), "date_to": str(monday + timedelta(days=6))}, wf.mgr, 200)
    assert again["assignments_created"] == 0, "generation never double-books"
    copied = wf.post("/shifts/copy-week", {"source_week_start": str(monday), "target_week_start": str(monday + timedelta(days=7)),
                                           "department_id": wf.prod}, wf.mgr, 200)
    assert copied["shifts_created"] == 5 and copied["assignments_created"] == 10
    # A weekly-off day does not count as leave.
    days = wf.post("/leave/requests", {"leave_type_id": {t["code"]: t["id"] for t in wf.get("/leave/types", wf.h["B"])}["AL"],
                                       "start_date": str(monday), "end_date": str(monday + timedelta(days=6))}, wf.h["B"])["days"]
    assert days == "5.0"


def test_notifications_are_deduplicated_and_delivery_logged(wf: Factory, db):
    from app.modules.workforce.notify import notify
    with SessionLocal() as s:
        uid = s.execute(select(User.id).where(User.email == "emp-b@employees.invalid")).scalar_one()
        first = notify(s, [uid], "overtime_available", "T", "B", dedupe_key="unit-test")
        second = notify(s, [uid], "overtime_available", "T", "B", dedupe_key="unit-test")
        s.commit()
        assert len(first) == 1 and second == {}
        n = s.execute(select(Notification).where(Notification.dedupe_key == f"unit-test:{uid}")).scalar_one()
        st = s.execute(text("SELECT channel, status FROM notification_deliveries WHERE notification_id = :n"), {"n": n.id}).all()
    assert st == [("in_app", "delivered")]
    channels = {c["channel"]: c for c in wf.get("/workforce/notification-channels", wf.h["B"])}
    assert channels["whatsapp"]["connected"] is False and channels["push"]["connected"] is False


def test_employee_code_login(wf: Factory, client: TestClient):
    limiter.reset()
    r = client.post(f"{API}/auth/login", json={"email": "emp-b", "password": PASSWORD})
    assert r.status_code == 200, "employee codes are case-insensitive"
    me = client.get(f"{API}/workforce/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).json()
    assert me["employee"]["employee_code"] == "EMP-B"
    assert client.post(f"{API}/auth/login", json={"email": "EMP-B", "password": "wrong-password"}).status_code == 401
