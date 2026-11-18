"""Pydantic contracts for the ERP core."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import EmailStr, Field, field_validator

from app.core.common import InputSchema, Schema


class CompanyIn(InputSchema):
    name: str = Field(min_length=1, max_length=200)
    legal_name: str | None = Field(None, max_length=200)
    registration_number: str | None = Field(None, max_length=100)
    tax_id: str | None = Field(None, max_length=100)
    industry: str | None = Field(None, max_length=100)
    address_line1: str | None = Field(None, max_length=200)
    address_line2: str | None = Field(None, max_length=200)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    country: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=50)
    email: EmailStr | None = None
    website: str | None = Field(None, max_length=200)
    currency_code: str = Field("INR", pattern=r"^[A-Z]{3}$")
    timezone: str = Field("Asia/Kolkata", max_length=64)
    date_format: str = Field("DD/MM/YYYY", max_length=20)
    number_format: str = Field("en-IN", max_length=20)
    fiscal_year_start_month: int = Field(4, ge=1, le=12)
    tax_config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return v


class CompanyUpdate(CompanyIn):
    name: str | None = Field(None, min_length=1, max_length=200)  # type: ignore[assignment]
    currency_code: str | None = Field(None, pattern=r"^[A-Z]{3}$")  # type: ignore[assignment]
    timezone: str | None = Field(None, max_length=64)  # type: ignore[assignment]
    date_format: str | None = None  # type: ignore[assignment]
    number_format: str | None = None  # type: ignore[assignment]
    fiscal_year_start_month: int | None = Field(None, ge=1, le=12)  # type: ignore[assignment]
    tax_config: dict[str, Any] | None = None  # type: ignore[assignment]

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:  # type: ignore[override]
        return CompanyIn._tz(v) if v else v  # type: ignore[call-arg]


class CompanyOut(Schema):
    id: uuid.UUID
    name: str
    legal_name: str | None
    registration_number: str | None
    tax_id: str | None
    industry: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    country: str | None
    phone: str | None
    email: str | None
    website: str | None
    logo_attachment_id: uuid.UUID | None
    currency_code: str
    timezone: str
    date_format: str
    number_format: str
    fiscal_year_start_month: int
    tax_config: dict[str, Any]


class AdminIn(InputSchema):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class SetupIn(InputSchema):
    company: CompanyIn
    admin: AdminIn
    load_demo_data: bool = False


class LoginIn(InputSchema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RoleBrief(Schema):
    id: uuid.UUID
    code: str
    name: str


class DepartmentBrief(Schema):
    id: uuid.UUID
    code: str
    name: str


class UserOut(Schema):
    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    job_title: str | None
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    roles: list[RoleBrief]
    departments: list[DepartmentBrief] = []
    primary_department_id: uuid.UUID | None = None
    warehouse_ids: list[uuid.UUID] = []


class MeOut(UserOut):
    permissions: list[str]
    company: CompanyOut | None


class LoginOut(Schema):
    user: MeOut
    token: str
    expires_at: datetime


class ProfileUpdate(InputSchema):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    phone: str | None = Field(None, max_length=50)
    job_title: str | None = Field(None, max_length=100)


class ChangePasswordIn(InputSchema):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class ResetRequestIn(InputSchema):
    email: EmailStr


class ResetConfirmIn(InputSchema):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(max_length=256)


class UserCreate(InputSchema):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=256)
    phone: str | None = Field(None, max_length=50)
    job_title: str | None = Field(None, max_length=100)
    role_ids: list[uuid.UUID] = Field(default_factory=list)
    department_ids: list[uuid.UUID] = Field(default_factory=list)
    primary_department_id: uuid.UUID | None = None
    warehouse_ids: list[uuid.UUID] = Field(default_factory=list)


class UserUpdate(InputSchema):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    phone: str | None = Field(None, max_length=50)
    job_title: str | None = Field(None, max_length=100)
    role_ids: list[uuid.UUID] | None = None
    department_ids: list[uuid.UUID] | None = None
    primary_department_id: uuid.UUID | None = None
    warehouse_ids: list[uuid.UUID] | None = None


class SetPasswordIn(InputSchema):
    new_password: str = Field(max_length=256)


class PermissionOut(Schema):
    code: str
    name: str
    module: str


class RoleOut(Schema):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_system: bool
    permissions: list[str]
    user_count: int = 0


class RoleCreate(InputSchema):
    code: str = Field(min_length=2, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=1000)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=1000)
    permissions: list[str] | None = None


class DepartmentOut(Schema):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    head_user_id: uuid.UUID | None
    is_active: bool
    member_count: int = 0


class DepartmentCreate(InputSchema):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=1000)
    head_user_id: uuid.UUID | None = None


class DepartmentUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=1000)
    head_user_id: uuid.UUID | None = None
    is_active: bool | None = None


class SettingOut(Schema):
    key: str
    value: Any
    description: str | None
    updated_at: datetime | None


class SettingsUpdate(InputSchema):
    values: dict[str, Any]


class AuditLogOut(Schema):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_name: str | None = None
    action: str
    entity_type: str | None
    entity_id: str | None
    changes: dict[str, Any] | None
    ip_address: str | None
    created_at: datetime


class NotificationPrefIn(InputSchema):
    alert_type: str = Field(max_length=50)
    in_app: bool = True
    email: bool = False


class NotificationPrefOut(Schema):
    alert_type: str
    in_app: bool
    email: bool


class NotificationOut(Schema):
    id: uuid.UUID
    type: str
    title: str
    body: str | None
    data: dict[str, Any] | None
    read_at: datetime | None
    created_at: datetime


class AttachmentOut(Schema):
    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    entity_type: str | None
    entity_id: str | None
    created_at: datetime
