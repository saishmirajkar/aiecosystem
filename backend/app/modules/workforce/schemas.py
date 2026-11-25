"""Workforce request contracts (responses are built as plain dicts by the services)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from pydantic import Field, model_validator

from app.core.common import InputSchema
from app.models import (
    AttendanceStatus, DayRule, EmployeeStatus, EmploymentType, HalfDay, RoundingMode, SkillKind,
)

Code = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
Name = Field(min_length=1, max_length=150)
Reason = Field(min_length=3, max_length=1000)
HoursF = Field(ge=0, le=999, max_digits=6, decimal_places=2)


# ------------------------------------------------------------------ reference data
class RefIn(InputSchema):
    code: str = Code
    name: str = Name


class LocationIn(RefIn):
    address: str | None = Field(None, max_length=1000)


class DesignationIn(RefIn):
    department_id: uuid.UUID | None = None


class SkillIn(RefIn):
    kind: SkillKind = SkillKind.SKILL


class TeamIn(RefIn):
    department_id: uuid.UUID
    description: str | None = Field(None, max_length=1000)
    member_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


class TeamUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=150)
    description: str | None = Field(None, max_length=1000)
    is_active: bool | None = None
    member_ids: list[uuid.UUID] | None = Field(None, max_length=500)


class HolidayIn(InputSchema):
    holiday_date: date
    name: str = Name
    work_location_id: uuid.UUID | None = None


class RefUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=150)
    is_active: bool | None = None


# ------------------------------------------------------------------ employees
class EmployeeSkillIn(InputSchema):
    skill_id: uuid.UUID
    level: int = Field(1, ge=1, le=5)
    valid_until: date | None = None


class EmployeeBase(InputSchema):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    email: str | None = Field(None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(None, max_length=50)
    department_id: uuid.UUID
    designation_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None
    status: EmployeeStatus = EmployeeStatus.ACTIVE
    employment_type: EmploymentType = EmploymentType.PERMANENT
    date_of_joining: date | None = None
    date_of_leaving: date | None = None
    work_location_id: uuid.UUID | None = None
    shift_pattern_id: uuid.UUID | None = None
    pattern_anchor_date: date | None = None
    overtime_eligible: bool = True
    weekly_hour_limit: Decimal | None = Field(None, gt=0, le=168, max_digits=6, decimal_places=2)
    base_hourly_rate: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=4)
    overtime_policy_id: uuid.UUID | None = None
    emergency_contact_name: str | None = Field(None, max_length=150)
    emergency_contact_phone: str | None = Field(None, max_length=50)
    address: str | None = Field(None, max_length=1000)
    notes: str | None = Field(None, max_length=2000)
    team_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    skills: list[EmployeeSkillIn] = Field(default_factory=list, max_length=100)


class EmployeeCreate(EmployeeBase):
    employee_code: str = Code


class EmployeeUpdate(InputSchema):
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    email: str | None = Field(None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(None, max_length=50)
    department_id: uuid.UUID | None = None
    designation_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None
    status: EmployeeStatus | None = None
    employment_type: EmploymentType | None = None
    date_of_joining: date | None = None
    date_of_leaving: date | None = None
    work_location_id: uuid.UUID | None = None
    shift_pattern_id: uuid.UUID | None = None
    pattern_anchor_date: date | None = None
    overtime_eligible: bool | None = None
    weekly_hour_limit: Decimal | None = Field(None, gt=0, le=168, max_digits=6, decimal_places=2)
    base_hourly_rate: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=4)
    overtime_policy_id: uuid.UUID | None = None
    emergency_contact_name: str | None = Field(None, max_length=150)
    emergency_contact_phone: str | None = Field(None, max_length=50)
    address: str | None = Field(None, max_length=1000)
    notes: str | None = Field(None, max_length=2000)
    team_ids: list[uuid.UUID] | None = Field(None, max_length=50)
    skills: list[EmployeeSkillIn] | None = Field(None, max_length=100)


class PortalAccessIn(InputSchema):
    password: str = Field(min_length=10, max_length=256)


class LinkUserIn(InputSchema):
    user_id: uuid.UUID


# ------------------------------------------------------------------ shifts
class TemplateIn(InputSchema):
    code: str = Code
    name: str = Field(min_length=1, max_length=100)
    start_time: time
    end_time: time
    break_minutes: int = Field(30, ge=0, le=240)
    department_id: uuid.UUID
    work_location_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    required_count: int = Field(1, ge=1, le=500)
    supervisor_id: uuid.UUID | None = None
    overtime_policy_id: uuid.UUID | None = None
    color: str | None = Field(None, max_length=20)
    skill_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _len(self) -> "TemplateIn":
        if self.start_time == self.end_time:
            raise ValueError("Start and end time must differ")
        return self


class TemplateUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=100)
    start_time: time | None = None
    end_time: time | None = None
    break_minutes: int | None = Field(None, ge=0, le=240)
    work_location_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    required_count: int | None = Field(None, ge=1, le=500)
    supervisor_id: uuid.UUID | None = None
    overtime_policy_id: uuid.UUID | None = None
    color: str | None = Field(None, max_length=20)
    skill_ids: list[uuid.UUID] | None = Field(None, max_length=20)
    is_active: bool | None = None


class PatternIn(InputSchema):
    code: str = Code
    name: str = Name
    cycle_days: int = Field(7, ge=1, le=56)
    days: list[uuid.UUID | None] = Field(min_length=1, max_length=56)  # template per day; null = weekly off

    @model_validator(mode="after")
    def _match(self) -> "PatternIn":
        if len(self.days) != self.cycle_days:
            raise ValueError("Provide exactly one entry per day of the cycle")
        return self


class ShiftCreate(InputSchema):
    template_id: uuid.UUID | None = None
    shift_date: date
    name: str | None = Field(None, min_length=1, max_length=100)
    start_time: time | None = None
    end_time: time | None = None
    break_minutes: int | None = Field(None, ge=0, le=240)
    department_id: uuid.UUID | None = None
    work_location_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    required_count: int | None = Field(None, ge=0, le=500)
    supervisor_id: uuid.UUID | None = None
    skill_ids: list[uuid.UUID] | None = Field(None, max_length=20)
    notes: str | None = Field(None, max_length=2000)


class ShiftUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=100)
    start_time: time | None = None
    end_time: time | None = None
    break_minutes: int | None = Field(None, ge=0, le=240)
    required_count: int | None = Field(None, ge=0, le=500)
    supervisor_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    notes: str | None = Field(None, max_length=2000)


class AssignIn(InputSchema):
    employee_id: uuid.UUID
    position_id: uuid.UUID | None = None
    reason: str | None = Field(None, max_length=1000)
    override_rules: list[str] = Field(default_factory=list, max_length=20)


class ReleaseIn(InputSchema):
    reason: str = Reason
    create_opportunity: bool = False


class GenerateIn(InputSchema):
    date_from: date
    date_to: date
    department_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _range(self) -> "GenerateIn":
        if self.date_to < self.date_from or (self.date_to - self.date_from).days > 62:
            raise ValueError("Choose a range of at most 62 days")
        return self


class CopyWeekIn(InputSchema):
    source_week_start: date
    target_week_start: date
    department_id: uuid.UUID
    include_assignments: bool = True


# ------------------------------------------------------------------ leave
class LeaveTypeIn(InputSchema):
    code: str = Code
    name: str = Field(min_length=1, max_length=100)
    is_paid: bool = True
    annual_days: Decimal = Field(Decimal("0"), ge=0, le=366, max_digits=6, decimal_places=1)
    carry_forward_max: Decimal = Field(Decimal("0"), ge=0, le=366, max_digits=6, decimal_places=1)
    unlimited: bool = False
    requires_document: bool = False
    allow_half_day: bool = True
    color: str | None = Field(None, max_length=20)


class LeaveTypeUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=100)
    is_paid: bool | None = None
    annual_days: Decimal | None = Field(None, ge=0, le=366, max_digits=6, decimal_places=1)
    carry_forward_max: Decimal | None = Field(None, ge=0, le=366, max_digits=6, decimal_places=1)
    unlimited: bool | None = None
    requires_document: bool | None = None
    allow_half_day: bool | None = None
    color: str | None = Field(None, max_length=20)
    is_active: bool | None = None


class LeaveCreate(InputSchema):
    employee_id: uuid.UUID | None = None  # omitted → the signed-in employee
    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    half_day: HalfDay = HalfDay.NONE
    reason: str | None = Field(None, max_length=2000)
    submit: bool = True

    @model_validator(mode="after")
    def _dates(self) -> "LeaveCreate":
        if self.end_date < self.start_date:
            raise ValueError("End date must be on or after the start date")
        if self.half_day != HalfDay.NONE and self.start_date != self.end_date:
            raise ValueError("A half day must start and end on the same date")
        if (self.end_date - self.start_date).days > 365:
            raise ValueError("Leave cannot exceed one year")
        return self


class DecisionIn(InputSchema):
    note: str | None = Field(None, max_length=1000)


class RejectIn(InputSchema):
    note: str = Reason


class CancelIn(InputSchema):
    reason: str = Reason


class BalanceAdjustIn(InputSchema):
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    year: int = Field(ge=2000, le=2100)
    adjustment: Decimal = Field(ge=-366, le=366, max_digits=6, decimal_places=1)
    reason: str = Reason


# ------------------------------------------------------------------ marketplace
class OpportunityCreate(InputSchema):
    shift_id: uuid.UUID
    positions: int | None = Field(None, ge=1, le=100)    # vacant regular positions to offer (default: all vacant)
    cover_half: HalfDay = HalfDay.NONE                    # offer a new half-shift cover position instead
    claim_deadline: datetime | None = None
    claims_open_at: datetime | None = None
    publish: bool = True
    notes: str | None = Field(None, max_length=1000)


class PublishIn(InputSchema):
    claims_open_at: datetime | None = None


class ExtendIn(InputSchema):
    claim_deadline: datetime
    renotify: bool = True


class ManualAssignIn(InputSchema):
    employee_id: uuid.UUID
    reason: str = Reason
    override_rules: list[str] = Field(default_factory=list, max_length=20)


class WithdrawIn(InputSchema):
    reason: str = Reason


class ResolveIn(InputSchema):
    resolution: str = Field(pattern=r"^(keep_replacement|reinstate_original|approve_release|decline)$")
    note: str = Reason


# ------------------------------------------------------------------ attendance
class CheckIn(InputSchema):
    employee_id: uuid.UUID | None = None   # supervisor recording for someone else
    assignment_id: uuid.UUID | None = None


class RecordIn(InputSchema):
    assignment_id: uuid.UUID
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    break_minutes: int | None = Field(None, ge=0, le=600)
    status: AttendanceStatus | None = None
    notes: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def _order(self) -> "RecordIn":
        if self.check_in_at and self.check_out_at and self.check_out_at <= self.check_in_at:
            raise ValueError("Check-out must be after check-in")
        return self


class CorrectionIn(InputSchema):
    assignment_id: uuid.UUID
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    break_minutes: int | None = Field(None, ge=0, le=600)
    status: AttendanceStatus | None = None
    reason: str = Reason

    @model_validator(mode="after")
    def _something(self) -> "CorrectionIn":
        if not any([self.check_in_at, self.check_out_at, self.break_minutes is not None, self.status]):
            raise ValueError("Propose at least one change")
        if self.check_in_at and self.check_out_at and self.check_out_at <= self.check_in_at:
            raise ValueError("Check-out must be after check-in")
        return self


# ------------------------------------------------------------------ policies & payroll
class WorkforcePolicyIn(InputSchema):
    department_id: uuid.UUID | None = None
    auto_publish: bool = True
    claim_deadline_hours: Decimal = HoursF
    min_rest_hours: Decimal = HoursF
    weekly_hour_cap: Decimal | None = Field(None, gt=0, le=168, max_digits=6, decimal_places=2)
    monthly_cover_hours_cap: Decimal | None = Field(None, gt=0, le=744, max_digits=6, decimal_places=2)
    withdrawal_cutoff_hours: Decimal = HoursF
    auto_reopen_on_withdrawal: bool = True
    allow_self_check_in: bool = False
    half_day_auto_cover: bool = False
    show_pay_estimates: bool = True
    late_grace_minutes: int = Field(10, ge=0, le=240)
    half_day_min_hours: Decimal = Field(Decimal("4"), ge=0, le=24, max_digits=6, decimal_places=2)
    weekly_off_days: list[int] = Field(default_factory=lambda: [7], max_length=7)

    @model_validator(mode="after")
    def _days(self) -> "WorkforcePolicyIn":
        if any(d < 1 or d > 7 for d in self.weekly_off_days):
            raise ValueError("Weekly off days are ISO weekdays 1 (Monday) to 7 (Sunday)")
        return self


class OvertimePolicyIn(InputSchema):
    code: str = Code
    name: str = Name
    description: str | None = Field(None, max_length=2000)
    daily_threshold_hours: Decimal = Field(gt=0, le=24, max_digits=6, decimal_places=2)
    weekly_threshold_hours: Decimal = Field(gt=0, le=168, max_digits=6, decimal_places=2)
    ot_multiplier: Decimal = Field(ge=0, le=10, max_digits=6, decimal_places=3)
    weekly_off_rule: DayRule = DayRule.ALL_OVERTIME
    weekly_off_multiplier: Decimal = Field(Decimal("2"), ge=0, le=10, max_digits=6, decimal_places=3)
    holiday_rule: DayRule = DayRule.ALL_OVERTIME
    holiday_multiplier: Decimal = Field(Decimal("2"), ge=0, le=10, max_digits=6, decimal_places=3)
    night_start: time | None = None
    night_end: time | None = None
    night_allowance_per_hour: Decimal = Field(Decimal("0"), ge=0, max_digits=18, decimal_places=4)
    rounding_minutes: int = Field(15, ge=0, le=60)
    rounding_mode: RoundingMode = RoundingMode.DOWN
    min_overtime_minutes: int = Field(30, ge=0, le=240)
    requires_approval: bool = True
    is_default: bool = False


class CalcDecisionIn(InputSchema):
    approved_minutes: int | None = Field(None, ge=0, le=1440)
    note: str | None = Field(None, max_length=1000)


class BulkApproveIn(InputSchema):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class RecalcIn(InputSchema):
    date_from: date
    date_to: date
    department_id: uuid.UUID | None = None


class ExportIn(InputSchema):
    period_start: date
    period_end: date
    department_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _range(self) -> "ExportIn":
        if self.period_end < self.period_start or (self.period_end - self.period_start).days > 92:
            raise ValueError("Choose a period of at most 92 days")
        return self
