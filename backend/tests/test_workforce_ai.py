"""Workforce Agent: answers come from real records, employees stay inside their own data, actions need approval."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.test_workforce import API, Factory, today


def ask(c: TestClient, h: dict, message: str, path: str = "/ai/chat") -> dict:
    r = c.post(f"{API}{path}", headers=h, json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()["message"]


@pytest.fixture
def wf(client: TestClient, admin_headers: dict) -> Factory:
    return Factory(client, admin_headers)


def test_who_is_absent_today_uses_leave_records(wf: Factory):
    shift = wf.shift(d=today())
    wf.assign(shift, "A")
    wf.approve(wf.leave("A", today()))
    msg = ask(wf.c, wf.mgr, "Who is absent today?")
    assert msg["payload"]["routing"]["agents"][0]["id"] == "workforce"
    assert "1 on approved leave" in msg["content"] and "WorkerA Test" in msg["content"]
    card = msg["payload"]["cards"][0]
    assert card["provenance"] == "verified" and card["rows"][0]["employee"] == "WorkerA Test"


def test_vacant_shifts_and_availability(wf: Factory):
    tomorrow = today() + timedelta(days=1)
    shift = wf.shift(d=tomorrow, required=2)
    wf.assign(shift, "A")
    msg = ask(wf.c, wf.mgr, "Which shifts are vacant tomorrow?")
    assert "1 vacant position(s)" in msg["content"] and "not offered" in msg["content"]
    msg = ask(wf.c, wf.mgr, "How many people are available for tomorrow's morning shift?")
    assert "could work Morning" in msg["content"]
    rows = msg["payload"]["cards"][0]["rows"]
    assert next(r for r in rows if r["code"] == "EMP-A")["available"] == "no", "A already works it"
    assert sum(r["available"] == "yes" for r in rows) == 5


def test_overtime_opportunity_draft_needs_approval_then_publishes(wf: Factory):
    tomorrow = today() + timedelta(days=1)
    shift = wf.shift(d=tomorrow, required=2)
    wf.assign(shift, "A")
    msg = ask(wf.c, wf.mgr, "Create an overtime opportunity for tomorrow's morning shift")
    approvals = msg["payload"]["approvals"]
    assert len(approvals) == 1 and approvals[0]["status"] == "pending"
    assert "nothing has been published yet" in msg["content"]
    assert wf.get("/overtime/opportunities", wf.mgr)["total"] == 0, "nothing changes before approval"
    r = wf.c.post(f"{API}/approvals/{approvals[0]['id']}/approve", headers=wf.mgr, json={})
    assert r.status_code == 200, r.text
    opps = wf.get("/overtime/opportunities", wf.mgr)["items"]
    assert len(opps) == 1 and opps[0]["status"] == "open" and opps[0]["source"] == "ai"
    assert wf.notes("B", "overtime_available"), "approved opportunity is published to eligible employees"


def test_employee_stays_inside_own_data(wf: Factory):
    shift = wf.shift(d=today())
    wf.assign(shift, "A")
    wf.approve(wf.leave("A", today()))
    msg = ask(wf.c, wf.h["B"], "Who is absent today?")
    assert "lacks permission 'workforce.view'" in msg["content"] and "WorkerA" not in msg["content"]
    wf.assign(wf.shift(d=today() + timedelta(days=2)), "B")
    msg = ask(wf.c, wf.h["B"], "When do I work? Show my shifts")
    assert "WorkerB Test has 1 shift(s)" in msg["content"]
    msg = ask(wf.c, wf.h["B"], "Show my shifts", path="/ai/workforce/chat")
    assert "WorkerB" in msg["content"] and "WorkerA" not in msg["content"]
    from app.ai_tools.base import ToolContext, ToolExecutor
    from app.core.database import SessionLocal
    from app.core.deps import build_actor
    from app.models import User
    from sqlalchemy import select
    with SessionLocal() as db:  # even when asked by name, the tool refuses someone else's record
        actor = build_actor(db, db.execute(select(User).where(User.email == "emp-b@employees.invalid")).scalar_one())
        r = ToolExecutor(ToolContext(db, actor, "workforce", "offline"), ["get_employee_schedule"]).execute(
            "get_employee_schedule", {"employee": "EMP-A"})
        db.rollback()
    assert r.status == "invalid" and "allowed to see" in r.error


def test_overtime_questions_are_answered_from_attendance(wf: Factory):
    _, opp = wf.covered_shift_with_opportunity()
    wf.claim(opp["id"], "B", 201)
    msg = ask(wf.c, wf.mgr, "Who has accepted overtime this week?" if wf.day <= today() + timedelta(days=(6 - today().weekday()))
              else "Who has accepted overtime next week?")
    assert "WorkerB Test" in msg["content"] and "claimed" in msg["content"]
    msg = ask(wf.c, wf.mgr, "How many overtime hours did each employee work this month?")
    assert "from verified attendance" in msg["content"]
    assert msg["payload"]["cards"][0]["provenance"] == "calculated"


def test_direct_workforce_chat_requires_access(wf: Factory, make_user):
    staff = make_user("inventory_staff")
    r = wf.c.post(f"{API}/ai/workforce/chat", headers=staff, json={"message": "Who is absent today?"})
    assert r.status_code == 403
    msg = ask(wf.c, wf.mgr, "staffing today", path="/ai/workforce/chat")
    assert msg["payload"]["routing"]["agents"][0]["id"] == "workforce"
