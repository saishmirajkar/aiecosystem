"""Workforce & shift management models (foundation module).

Instants are timezone-aware; shift instants are absolute so overnight shifts are a single range.
Two database guards protect staffing:
  * one active assignment per position (partial unique index), so a shift can never be over-filled;
  * an exclusion constraint so one employee can never hold two overlapping assigned shifts.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Table, Text, Time,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Authored, Base, Money, Timestamps, UUIDPk, utcnow
from app.models.erp import Department, User
from app.models.master import str_enum

Hours = Numeric(6, 2)
Days = Numeric(6, 1)
Multiplier = Numeric(6, 3)


# ------------------------------------------------------------------ enums
class EmployeeStatus(str, enum.Enum):
    ACTIVE = "active"
    ON_LEAVE = "on_leave"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"
    TERMINATED = "terminated"


class EmploymentType(str, enum.Enum):
    PERMANENT = "permanent"
    CONTRACT = "contract"
    TEMPORARY = "temporary"
    APPRENTICE = "apprentice"
    PART_TIME = "part_time"


class SkillKind(str, enum.Enum):
    SKILL = "skill"
    QUALIFICATION = "qualification"
    CERTIFICATION = "certification"


class ShiftStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


class PositionKind(str, enum.Enum):
    REGULAR = "regular"
    COVER = "cover"  # covers part of a shift (e.g. half-day leave)


class AssignmentSource(str, enum.Enum):
    SCHEDULE = "schedule"
    MARKETPLACE = "marketplace"
    MANUAL = "manual"


class AssignmentStatus(str, enum.Enum):
    ASSIGNED = "assigned"
    ON_LEAVE = "on_leave"
    RELEASED = "released"
    CANCELLED = "cancelled"


class HalfDay(str, enum.Enum):
    NONE = "none"
    FIRST_HALF = "first_half"
    SECOND_HALF = "second_half"


class LeaveStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class OpportunityStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"   # visible, claims open later
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class OpportunitySource(str, enum.Enum):
    LEAVE = "leave"
    MANUAL = "manual"
    AI = "ai"
    WITHDRAWAL = "withdrawal"


class ClaimOutcome(str, enum.Enum):
    WON = "won"
    LOST = "lost"
    REJECTED = "rejected"


class ConflictKind(str, enum.Enum):
    LEAVE_CANCELLED_AFTER_COVER = "leave_cancelled_after_cover"
    WITHDRAWAL_REQUEST = "withdrawal_request"


class ConflictStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class AttendanceStatus(str, enum.Enum):
    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    HALF_DAY = "half_day"
    ON_LEAVE = "on_leave"
    HOLIDAY = "holiday"
    WEEKLY_OFF = "weekly_off"


class AttendanceSource(str, enum.Enum):
    SUPERVISOR = "supervisor"
    SELF = "self"
    IMPORT = "import"
    CORRECTION = "correction"
    SYSTEM = "system"


class CorrectionStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DayRule(str, enum.Enum):
    ALL_OVERTIME = "all_overtime"   # every hour worked on such a day is overtime
    NORMAL = "normal"               # treated like any other day


class RoundingMode(str, enum.Enum):
    DOWN = "down"
    NEAREST = "nearest"
    UP = "up"


class CalcStatus(str, enum.Enum):
    CALCULATED = "calculated"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPORTED = "exported"


class DeliveryStatus(str, enum.Enum):
    DELIVERED = "delivered"   # in-app: stored and visible
    PENDING = "pending"
    SENT = "sent"
    LOGGED = "logged"         # console backend: written to the log only
    FAILED = "failed"
    SKIPPED = "skipped"


# ------------------------------------------------------------------ people
class WorkLocation(UUIDPk, Timestamps, Base):
    __tablename__ = "work_locations"
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Designation(UUIDPk, Timestamps, Base):
    __tablename__ = "designations"
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Skill(UUIDPk, Timestamps, Base):
    __tablename__ = "skills"
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    kind: Mapped[SkillKind] = mapped_column(str_enum(SkillKind, "skill_kind"), default=SkillKind.SKILL)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Team(UUIDPk, Timestamps, Base):
    """An eligibility group inside a department (e.g. Assembly Team)."""
    __tablename__ = "teams"
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    department_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    department: Mapped[Department] = relationship(lazy="joined")


team_members = Table(
    "team_members", Base.metadata,
    Column("team_id", UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
    Column("employee_id", UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True),
)


class Employee(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "employees"
    __table_args__ = (
        Index("uq_employees_code_lower", text("lower(employee_code)"), unique=True),
        CheckConstraint("weekly_hour_limit IS NULL OR weekly_hour_limit > 0", name="weekly_limit_positive"),
        CheckConstraint("base_hourly_rate IS NULL OR base_hourly_rate >= 0", name="rate_non_negative"),
        CheckConstraint("manager_id IS NULL OR manager_id <> id", name="not_own_manager"),
    )

    employee_code: Mapped[str] = mapped_column(String(30))
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(50))
    photo_attachment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("attachments.id"))
    department_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"), index=True)
    designation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("designations.id"))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True)
    status: Mapped[EmployeeStatus] = mapped_column(str_enum(EmployeeStatus, "employee_status"), default=EmployeeStatus.ACTIVE)
    employment_type: Mapped[EmploymentType] = mapped_column(str_enum(EmploymentType, "employment_type"), default=EmploymentType.PERMANENT)
    date_of_joining: Mapped[date | None] = mapped_column(Date)
    date_of_leaving: Mapped[date | None] = mapped_column(Date)
    work_location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("work_locations.id"))
    shift_pattern_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shift_patterns.id", use_alter=True))
    pattern_anchor_date: Mapped[date | None] = mapped_column(Date)
    overtime_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    weekly_hour_limit: Mapped[Decimal | None] = mapped_column(Hours)
    base_hourly_rate: Mapped[Decimal | None] = mapped_column(Money)
    overtime_policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("overtime_policies.id", use_alter=True))
    emergency_contact_name: Mapped[str | None] = mapped_column(String(150))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    department: Mapped[Department] = relationship(lazy="joined")
    designation: Mapped[Designation | None] = relationship(lazy="joined")
    location: Mapped[WorkLocation | None] = relationship(lazy="joined")
    manager: Mapped["Employee | None"] = relationship(remote_side="Employee.id", lazy="select")
    user: Mapped[User | None] = relationship(lazy="select", foreign_keys=[user_id])
    teams: Mapped[list[Team]] = relationship(secondary=team_members, lazy="selectin")
    skills: Mapped[list["EmployeeSkill"]] = relationship(lazy="selectin", cascade="all, delete-orphan")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() if self.last_name else self.first_name


class EmployeeSkill(Base):
    __tablename__ = "employee_skills"
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("skills.id"), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    valid_until: Mapped[date | None] = mapped_column(Date)
    skill: Mapped[Skill] = relationship(lazy="joined")


class Holiday(UUIDPk, Timestamps, Base):
    __tablename__ = "holidays"
    __table_args__ = (UniqueConstraint("holiday_date", "work_location_id", name="uq_holidays_date_location"),)
    holiday_date: Mapped[date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(150))
    work_location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("work_locations.id"))


# ------------------------------------------------------------------ policies
class OvertimePolicy(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "overtime_policies"
    __table_args__ = (
        Index("uq_overtime_policies_default", "is_default", unique=True, postgresql_where=text("is_default")),
        CheckConstraint("daily_threshold_hours > 0 AND weekly_threshold_hours > 0", name="thresholds_positive"),
        CheckConstraint("ot_multiplier >= 0 AND weekly_off_multiplier >= 0 AND holiday_multiplier >= 0", name="multipliers_non_negative"),
        CheckConstraint("rounding_minutes >= 0 AND min_overtime_minutes >= 0", name="rounding_non_negative"),
    )
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    daily_threshold_hours: Mapped[Decimal] = mapped_column(Hours, default=Decimal("9"))
    weekly_threshold_hours: Mapped[Decimal] = mapped_column(Hours, default=Decimal("48"))
    ot_multiplier: Mapped[Decimal] = mapped_column(Multiplier, default=Decimal("2"))
    weekly_off_rule: Mapped[DayRule] = mapped_column(str_enum(DayRule, "weekly_off_rule"), default=DayRule.ALL_OVERTIME)
    weekly_off_multiplier: Mapped[Decimal] = mapped_column(Multiplier, default=Decimal("2"))
    holiday_rule: Mapped[DayRule] = mapped_column(str_enum(DayRule, "holiday_rule"), default=DayRule.ALL_OVERTIME)
    holiday_multiplier: Mapped[Decimal] = mapped_column(Multiplier, default=Decimal("2"))
    night_start: Mapped[time | None] = mapped_column(Time)
    night_end: Mapped[time | None] = mapped_column(Time)
    night_allowance_per_hour: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    rounding_minutes: Mapped[int] = mapped_column(Integer, default=15)
    rounding_mode: Mapped[RoundingMode] = mapped_column(str_enum(RoundingMode, "rounding_mode"), default=RoundingMode.DOWN)
    min_overtime_minutes: Mapped[int] = mapped_column(Integer, default=30)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkforcePolicy(UUIDPk, Timestamps, Authored, Base):
    """Company default (department_id NULL) or a department override."""
    __tablename__ = "workforce_policies"
    __table_args__ = (
        Index("uq_workforce_policies_company", text("(department_id IS NULL)"), unique=True, postgresql_where=text("department_id IS NULL")),
        UniqueConstraint("department_id", name="uq_workforce_policies_department"),
        CheckConstraint("claim_deadline_hours >= 0 AND min_rest_hours >= 0 AND withdrawal_cutoff_hours >= 0", name="hours_non_negative"),
        CheckConstraint("weekly_hour_cap IS NULL OR weekly_hour_cap > 0", name="weekly_cap_positive"),
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"))
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=True)
    claim_deadline_hours: Mapped[Decimal] = mapped_column(Hours, default=Decimal("4"))
    min_rest_hours: Mapped[Decimal] = mapped_column(Hours, default=Decimal("8"))
    weekly_hour_cap: Mapped[Decimal | None] = mapped_column(Hours, default=Decimal("60"))
    monthly_cover_hours_cap: Mapped[Decimal | None] = mapped_column(Hours)
    withdrawal_cutoff_hours: Mapped[Decimal] = mapped_column(Hours, default=Decimal("12"))
    auto_reopen_on_withdrawal: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_self_check_in: Mapped[bool] = mapped_column(Boolean, default=False)
    half_day_auto_cover: Mapped[bool] = mapped_column(Boolean, default=False)
    show_pay_estimates: Mapped[bool] = mapped_column(Boolean, default=True)
    late_grace_minutes: Mapped[int] = mapped_column(Integer, default=10)
    half_day_min_hours: Mapped[Decimal] = mapped_column(Hours, default=Decimal("4"))
    weekly_off_days: Mapped[list] = mapped_column(JSONB, default=lambda: [7])  # ISO weekdays used when no pattern


# ------------------------------------------------------------------ shifts
shift_template_skills = Table(
    "shift_template_skills", Base.metadata,
    Column("template_id", UUID(as_uuid=True), ForeignKey("shift_templates.id", ondelete="CASCADE"), primary_key=True),
    Column("skill_id", UUID(as_uuid=True), ForeignKey("skills.id"), primary_key=True),
)
shift_skills = Table(
    "shift_skills", Base.metadata,
    Column("shift_id", UUID(as_uuid=True), ForeignKey("shifts.id", ondelete="CASCADE"), primary_key=True),
    Column("skill_id", UUID(as_uuid=True), ForeignKey("skills.id"), primary_key=True),
)


class ShiftTemplate(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "shift_templates"
    __table_args__ = (
        CheckConstraint("start_time <> end_time", name="non_zero_length"),
        CheckConstraint("break_minutes >= 0 AND required_count >= 1", name="counts_valid"),
    )
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    break_minutes: Mapped[int] = mapped_column(Integer, default=30)
    department_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"))
    work_location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("work_locations.id"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"))
    required_count: Mapped[int] = mapped_column(Integer, default=1)
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"))
    overtime_policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("overtime_policies.id"))
    color: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    department: Mapped[Department] = relationship(lazy="joined")
    team: Mapped[Team | None] = relationship(lazy="joined")
    location: Mapped[WorkLocation | None] = relationship(lazy="joined")
    skills: Mapped[list[Skill]] = relationship(secondary=shift_template_skills, lazy="selectin")


class ShiftPattern(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "shift_patterns"
    __table_args__ = (CheckConstraint("cycle_days BETWEEN 1 AND 56", name="cycle_range"),)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    cycle_days: Mapped[int] = mapped_column(Integer, default=7)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    days: Mapped[list["ShiftPatternDay"]] = relationship(lazy="selectin", cascade="all, delete-orphan",
                                                         order_by="ShiftPatternDay.day_index")


class ShiftPatternDay(Base):
    __tablename__ = "shift_pattern_days"
    pattern_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shift_patterns.id", ondelete="CASCADE"), primary_key=True)
    day_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shift_templates.id"))  # NULL = weekly off
    template: Mapped[ShiftTemplate | None] = relationship(lazy="joined")


class Shift(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "shifts"
    __table_args__ = (
        Index("uq_shifts_template_date", "template_id", "shift_date", unique=True,
              postgresql_where=text("template_id IS NOT NULL AND status = 'scheduled'")),
        Index("ix_shifts_window", "start_at", "end_at"),
        CheckConstraint("end_at > start_at", name="window_valid"),
        CheckConstraint("break_minutes >= 0 AND required_count >= 0", name="counts_valid"),
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shift_templates.id"))
    name: Mapped[str] = mapped_column(String(100))
    shift_date: Mapped[date] = mapped_column(Date, index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    break_minutes: Mapped[int] = mapped_column(Integer, default=30)
    department_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"), index=True)
    work_location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("work_locations.id"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"))
    required_count: Mapped[int] = mapped_column(Integer, default=1)
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"))
    overtime_policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("overtime_policies.id"))
    status: Mapped[ShiftStatus] = mapped_column(str_enum(ShiftStatus, "shift_status"), default=ShiftStatus.SCHEDULED)
    notes: Mapped[str | None] = mapped_column(Text)

    template: Mapped[ShiftTemplate | None] = relationship(lazy="select")
    department: Mapped[Department] = relationship(lazy="joined")
    team: Mapped[Team | None] = relationship(lazy="joined")
    location: Mapped[WorkLocation | None] = relationship(lazy="joined")
    skills: Mapped[list[Skill]] = relationship(secondary=shift_skills, lazy="selectin")
    positions: Mapped[list["ShiftPosition"]] = relationship(lazy="selectin", order_by="ShiftPosition.slot", back_populates="shift")


class ShiftPosition(UUIDPk, Base):
    __tablename__ = "shift_positions"
    __table_args__ = (UniqueConstraint("shift_id", "slot", name="uq_shift_positions_slot"),
                      CheckConstraint("kind = 'regular' OR (window_start IS NOT NULL AND window_end > window_start)", name="cover_window"))
    shift_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id", ondelete="CASCADE"), index=True)
    slot: Mapped[int] = mapped_column(Integer)
    kind: Mapped[PositionKind] = mapped_column(str_enum(PositionKind, "position_kind"), default=PositionKind.REGULAR)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # inactive = removed when required count dropped
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    shift: Mapped[Shift] = relationship(back_populates="positions", lazy="select")


class ShiftAssignment(UUIDPk, Base):
    __tablename__ = "shift_assignments"
    __table_args__ = (
        Index("uq_assignments_active_position", "position_id", unique=True, postgresql_where=text("status = 'assigned'")),
        Index("ix_assignments_employee_window", "employee_id", "start_at"),
        ExcludeConstraint(("employee_id", "="), (text("tstzrange(start_at, end_at, '[)')"), "&&"),
                          name="ex_assignments_no_overlap", using="gist", where=text("status = 'assigned'")),
        CheckConstraint("end_at > start_at", name="window_valid"),
    )
    position_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shift_positions.id"))
    shift_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[AssignmentSource] = mapped_column(str_enum(AssignmentSource, "assignment_source"), default=AssignmentSource.SCHEDULE)
    status: Mapped[AssignmentStatus] = mapped_column(str_enum(AssignmentStatus, "assignment_status"), default=AssignmentStatus.ASSIGNED)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("overtime_opportunities.id", use_alter=True))
    leave_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("leave_requests.id", use_alter=True))
    reason: Mapped[str | None] = mapped_column(Text)               # manual assignment / override reason
    rule_overrides: Mapped[list | None] = mapped_column(JSONB)      # eligibility rules overridden by a manager
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(Text)
    ended_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    position: Mapped[ShiftPosition] = relationship(lazy="joined")
    shift: Mapped[Shift] = relationship(lazy="joined")
    employee: Mapped[Employee] = relationship(lazy="joined")


# ------------------------------------------------------------------ leave
class LeaveType(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "leave_types"
    __table_args__ = (CheckConstraint("annual_days >= 0 AND carry_forward_max >= 0", name="days_non_negative"),)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    is_paid: Mapped[bool] = mapped_column(Boolean, default=True)
    annual_days: Mapped[Decimal] = mapped_column(Days, default=Decimal("0"))
    carry_forward_max: Mapped[Decimal] = mapped_column(Days, default=Decimal("0"))
    unlimited: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_document: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_half_day: Mapped[bool] = mapped_column(Boolean, default=True)
    color: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class LeaveBalance(Base):
    __tablename__ = "leave_balances"
    __table_args__ = (CheckConstraint("used >= 0", name="used_non_negative"),)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    leave_type_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leave_types.id"), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    entitled: Mapped[Decimal] = mapped_column(Days, default=Decimal("0"))
    carried_forward: Mapped[Decimal] = mapped_column(Days, default=Decimal("0"))
    adjusted: Mapped[Decimal] = mapped_column(Days, default=Decimal("0"))
    used: Mapped[Decimal] = mapped_column(Days, default=Decimal("0"))
    leave_type: Mapped[LeaveType] = relationship(lazy="joined")

    @property
    def available(self) -> Decimal:
        return self.entitled + self.carried_forward + self.adjusted - self.used


class LeaveRequest(UUIDPk, Timestamps, Base):
    __tablename__ = "leave_requests"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="dates_ordered"),
        CheckConstraint("half_day = 'none' OR start_date = end_date", name="half_day_single_date"),
        CheckConstraint("days >= 0", name="days_non_negative"),
        Index("ix_leave_requests_employee_dates", "employee_id", "start_date", "end_date"),
    )
    number: Mapped[str] = mapped_column(String(30), unique=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"))
    leave_type_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leave_types.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    half_day: Mapped[HalfDay] = mapped_column(str_enum(HalfDay, "half_day"), default=HalfDay.NONE)
    days: Mapped[Decimal] = mapped_column(Days, default=Decimal("0"))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[LeaveStatus] = mapped_column(str_enum(LeaveStatus, "leave_status"), default=LeaveStatus.DRAFT, index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    employee: Mapped[Employee] = relationship(lazy="joined")
    leave_type: Mapped[LeaveType] = relationship(lazy="joined")


leave_request_shifts = Table(
    "leave_request_shifts", Base.metadata,
    Column("leave_request_id", UUID(as_uuid=True), ForeignKey("leave_requests.id", ondelete="CASCADE"), primary_key=True),
    Column("assignment_id", UUID(as_uuid=True), ForeignKey("shift_assignments.id"), primary_key=True),
)


class LeaveApproval(UUIDPk, Base):
    """Decision log for a leave request (submitted / approved / rejected / cancelled)."""
    __tablename__ = "leave_approvals"
    leave_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leave_requests.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(20))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------------ marketplace
class OvertimeOpportunity(UUIDPk, Timestamps, Base):
    __tablename__ = "overtime_opportunities"
    __table_args__ = (
        CheckConstraint("positions_filled >= 0 AND positions_filled <= positions_total", name="filled_within_total"),
        CheckConstraint("end_at > start_at", name="window_valid"),
        Index("ix_opportunities_status_deadline", "status", "claim_deadline"),
    )
    number: Mapped[str] = mapped_column(String(30), unique=True)
    shift_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"), index=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"))
    title: Mapped[str] = mapped_column(String(200))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    break_minutes: Mapped[int] = mapped_column(Integer, default=0)
    positions_total: Mapped[int] = mapped_column(Integer, default=1)
    positions_filled: Mapped[int] = mapped_column(Integer, default=0)
    claims_open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[OpportunityStatus] = mapped_column(str_enum(OpportunityStatus, "opportunity_status"), default=OpportunityStatus.DRAFT)
    round: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[OpportunitySource] = mapped_column(str_enum(OpportunitySource, "opportunity_source"), default=OpportunitySource.LEAVE)
    notes: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    shift: Mapped[Shift] = relationship(lazy="joined")
    department: Mapped[Department] = relationship(lazy="joined")
    team: Mapped[Team | None] = relationship(lazy="joined")
    positions: Mapped[list["OpportunityPosition"]] = relationship(lazy="selectin", cascade="all, delete-orphan")


class OpportunityPosition(Base):
    __tablename__ = "opportunity_positions"
    opportunity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("overtime_opportunities.id", ondelete="CASCADE"), primary_key=True)
    position_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shift_positions.id"), primary_key=True)
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shift_assignments.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # false when the position was withdrawn from the offer
    position: Mapped[ShiftPosition] = relationship(lazy="joined")


class OpportunityRecipient(Base):
    __tablename__ = "opportunity_recipients"
    opportunity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("overtime_opportunities.id", ondelete="CASCADE"), primary_key=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"), primary_key=True)
    round: Mapped[int] = mapped_column(Integer, primary_key=True)
    notification_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("notifications.id", ondelete="SET NULL"))
    reachable: Mapped[bool] = mapped_column(Boolean, default=True)  # false: no portal login, nothing could be sent
    notified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OvertimeClaim(UUIDPk, Base):
    __tablename__ = "overtime_claims"
    __table_args__ = (
        Index("ix_claims_opportunity_created", "opportunity_id", "created_at"),
        Index("ix_claims_employee", "employee_id", "created_at"),
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("overtime_opportunities.id"))
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"))
    outcome: Mapped[ClaimOutcome] = mapped_column(str_enum(ClaimOutcome, "claim_outcome"))
    reason_code: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(Text)
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shift_assignments.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    # Server clock at the moment the claim was processed inside its transaction (not the client's clock).
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())
    employee: Mapped[Employee] = relationship(lazy="joined")


class WorkforceConflict(UUIDPk, Timestamps, Base):
    __tablename__ = "workforce_conflicts"
    kind: Mapped[ConflictKind] = mapped_column(str_enum(ConflictKind, "conflict_kind"))
    status: Mapped[ConflictStatus] = mapped_column(str_enum(ConflictStatus, "conflict_status"), default=ConflictStatus.OPEN, index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"))
    shift_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"))
    leave_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("leave_requests.id"))
    original_assignment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shift_assignments.id"))
    replacement_assignment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shift_assignments.id"))
    summary: Mapped[str] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(String(40))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shift: Mapped[Shift | None] = relationship(lazy="joined")


# ------------------------------------------------------------------ attendance
class AttendanceRecord(UUIDPk, Timestamps, Base):
    __tablename__ = "attendance_records"
    __table_args__ = (
        Index("uq_attendance_assignment", "assignment_id", unique=True, postgresql_where=text("assignment_id IS NOT NULL")),
        Index("ix_attendance_employee_date", "employee_id", "work_date"),
        CheckConstraint("check_out_at IS NULL OR check_in_at IS NULL OR check_out_at > check_in_at", name="times_ordered"),
        CheckConstraint("worked_minutes >= 0 AND break_minutes >= 0", name="minutes_non_negative"),
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"))
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shift_assignments.id"))
    work_date: Mapped[date] = mapped_column(Date)
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    break_minutes: Mapped[int] = mapped_column(Integer, default=0)
    worked_minutes: Mapped[int] = mapped_column(Integer, default=0)
    late_minutes: Mapped[int] = mapped_column(Integer, default=0)
    early_leave_minutes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[AttendanceStatus] = mapped_column(str_enum(AttendanceStatus, "attendance_status"))
    source: Mapped[AttendanceSource] = mapped_column(str_enum(AttendanceSource, "attendance_source"))
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    employee: Mapped[Employee] = relationship(lazy="joined")
    assignment: Mapped[ShiftAssignment | None] = relationship(lazy="joined")


class AttendanceCorrection(UUIDPk, Timestamps, Base):
    __tablename__ = "attendance_corrections"
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"), index=True)
    attendance_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("attendance_records.id"))
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shift_assignments.id"))
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    break_minutes: Mapped[int | None] = mapped_column(Integer)
    status_override: Mapped[AttendanceStatus | None] = mapped_column(str_enum(AttendanceStatus, "correction_attendance_status"))
    reason: Mapped[str] = mapped_column(Text)
    before: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[CorrectionStatus] = mapped_column(str_enum(CorrectionStatus, "correction_status"), default=CorrectionStatus.PENDING, index=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    employee: Mapped[Employee] = relationship(lazy="joined")


# ------------------------------------------------------------------ overtime & payroll
class PayrollExportBatch(UUIDPk, Base):
    __tablename__ = "payroll_export_batches"
    number: Mapped[str] = mapped_column(String(30), unique=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"))
    employee_count: Mapped[int] = mapped_column(Integer, default=0)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    total_minutes: Mapped[int] = mapped_column(Integer, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OvertimeCalculation(UUIDPk, Timestamps, Base):
    __tablename__ = "overtime_calculations"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", "revision", name="uq_overtime_calc_employee_date_rev"),
        Index("ix_overtime_calc_status_date", "status", "work_date"),
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id"))
    work_date: Mapped[date] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    is_correction: Mapped[bool] = mapped_column(Boolean, default=False)
    department_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"))
    day_type: Mapped[str] = mapped_column(String(20))  # regular | weekly_off | holiday
    scheduled_minutes: Mapped[int] = mapped_column(Integer, default=0)
    worked_minutes: Mapped[int] = mapped_column(Integer, default=0)
    regular_minutes: Mapped[int] = mapped_column(Integer, default=0)
    ot_daily_minutes: Mapped[int] = mapped_column(Integer, default=0)
    ot_weekly_minutes: Mapped[int] = mapped_column(Integer, default=0)
    ot_weekly_off_minutes: Mapped[int] = mapped_column(Integer, default=0)
    ot_holiday_minutes: Mapped[int] = mapped_column(Integer, default=0)
    night_minutes: Mapped[int] = mapped_column(Integer, default=0)
    payable_minutes: Mapped[int] = mapped_column(Integer, default=0)
    approved_minutes: Mapped[int | None] = mapped_column(Integer)
    base_rate: Mapped[Decimal | None] = mapped_column(Money)
    multiplier: Mapped[Decimal] = mapped_column(Multiplier, default=Decimal("0"))
    night_allowance: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("overtime_policies.id"))
    policy_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[CalcStatus] = mapped_column(str_enum(CalcStatus, "calc_status"), default=CalcStatus.CALCULATED)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    export_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("payroll_export_batches.id"))
    employee: Mapped[Employee] = relationship(lazy="joined")


# ------------------------------------------------------------------ notification delivery
class NotificationDelivery(UUIDPk, Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (Index("ix_deliveries_pending", "status", "next_attempt_at"),)
    notification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("notifications.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[DeliveryStatus] = mapped_column(str_enum(DeliveryStatus, "delivery_status"))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
