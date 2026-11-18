"""Production / job-work / dispatch contracts."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field, model_validator

from app.core.common import InputSchema
from app.models import Performer

Units = Field(gt=0, max_digits=18, decimal_places=4)
NonNegUnits = Field(Decimal("0"), ge=0, max_digits=18, decimal_places=4)


class PlantIn(InputSchema):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Z0-9][A-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=150)
    city: str | None = Field(None, max_length=100)


class VendorSiteIn(InputSchema):
    supplier_id: uuid.UUID


class DesignateIn(InputSchema):
    warehouse_id: uuid.UUID


class CustomerIn(InputSchema):
    code: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    name: str = Field(min_length=1, max_length=200)
    contact_person: str | None = Field(None, max_length=150)
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=50)
    address: str | None = Field(None, max_length=1000)
    city: str | None = Field(None, max_length=100)
    gstin: str | None = Field(None, max_length=20)


class CustomerUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=200)
    contact_person: str | None = Field(None, max_length=150)
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=50)
    address: str | None = Field(None, max_length=1000)
    city: str | None = Field(None, max_length=100)
    gstin: str | None = Field(None, max_length=20)
    is_active: bool | None = None


class BomLineIn(InputSchema):
    component_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    notes: str | None = Field(None, max_length=300)


class RouteStageIn(InputSchema):
    name: str = Field(min_length=1, max_length=120)
    performer: Performer
    site_id: uuid.UUID | None = None
    rate_per_unit: Decimal = Field(Decimal("0"), ge=0, max_digits=18, decimal_places=6)
    planned_days: int = Field(1, ge=0, le=365)
    notes: str | None = Field(None, max_length=300)


class StructureIn(InputSchema):
    bom: list[BomLineIn] = Field(default_factory=list, max_length=100)
    route: list[RouteStageIn] = Field(default_factory=list, max_length=30)


class OrderCreate(InputSchema):
    product_id: uuid.UUID
    quantity: Decimal = Units
    plant_id: uuid.UUID
    customer_id: uuid.UUID | None = None
    start_date: date | None = None
    due_date: date | None = None
    notes: str | None = Field(None, max_length=2000)


class StageUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=120)
    performer: Performer | None = None
    site_id: uuid.UUID | None = None
    rate_per_unit: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=6)
    planned_start: date | None = None
    planned_end: date | None = None
    notes: str | None = Field(None, max_length=300)


class StageAdd(RouteStageIn):
    after_sequence: int = Field(ge=0)


class ChallanIn(InputSchema):
    vendor_site_id: uuid.UUID | None = None  # defaults to the stage's vendor
    units: Decimal = Units
    expected_return_date: date | None = None
    vehicle_number: str | None = Field(None, max_length=30)
    notes: str | None = Field(None, max_length=1000)


class ReceiptIn(InputSchema):
    vendor_site_id: uuid.UUID
    units_accepted: Decimal = NonNegUnits
    units_rework: Decimal = NonNegUnits
    units_scrapped: Decimal = NonNegUnits
    scrap_reason: str | None = Field(None, max_length=1000)
    direct_to_next: bool = False
    receive_to_site_id: uuid.UUID | None = None
    vendor_document_number: str | None = Field(None, max_length=60)
    received_date: date | None = None
    notes: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def _units(self) -> "ReceiptIn":
        if self.units_accepted + self.units_rework + self.units_scrapped <= 0:
            raise ValueError("Enter accepted, rework or scrapped units")
        if self.units_scrapped > 0 and not self.scrap_reason:
            raise ValueError("A reason is required for scrapped units")
        return self


class CompletionIn(InputSchema):
    units_completed: Decimal = NonNegUnits
    units_scrapped: Decimal = NonNegUnits
    scrap_reason: str | None = Field(None, max_length=1000)
    notes: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def _units(self) -> "CompletionIn":
        if self.units_completed + self.units_scrapped <= 0:
            raise ValueError("Enter completed or scrapped units")
        if self.units_scrapped > 0 and not self.scrap_reason:
            raise ValueError("A reason is required for scrapped units")
        return self


class DispatchLineIn(InputSchema):
    product_id: uuid.UUID
    quantity: Decimal = Units


class DispatchCreate(InputSchema):
    customer_id: uuid.UUID
    warehouse_id: uuid.UUID
    order_id: uuid.UUID | None = None
    dispatch_date: date | None = None
    vehicle_number: str | None = Field(None, max_length=30)
    transporter: str | None = Field(None, max_length=100)
    customer_reference: str | None = Field(None, max_length=60)
    notes: str | None = Field(None, max_length=1000)
    lines: list[DispatchLineIn] = Field(min_length=1, max_length=100)


class ReasonIn(InputSchema):
    reason: str = Field(min_length=3, max_length=1000)
