"""Import all models so SQLAlchemy metadata and Alembic see every table."""
from app.models.ai import ActionApproval, Agent, AgentExecutionLog, AgentTool, ApprovalStatus, Conversation, Message
from app.models.erp import (
    Company, Department, PasswordResetToken, Permission, Role, User, UserDepartment, UserSession,
    role_permissions, user_roles, user_warehouse_access,
)
from app.models.inventory import (
    AdjustmentStatus, AdjustmentType, InventoryBalance, InventoryReservation, InventoryTransaction, MovementType,
    ProductBatch, ProductSerialNumber, ProductValuation, ReservationStatus, SerialStatus, StockAdjustment,
    StockAdjustmentItem, StockMovement, StockStatus, StockTransfer, StockTransferItem, TransferStatus,
)
from app.models.master import (
    LocationType, Product, ProductCategory, ProductSupplier, ProductType, StorageLocation, Supplier,
    UnitConversion, UnitOfMeasure, UomCategory, Warehouse,
)
from app.models.production import (
    BomLine, Customer, DispatchLine, DispatchNote, DispatchStatus, JobWorkChallan, JobWorkChallanLine, JobWorkReceipt,
    OrderPart, OrderStage, OrderStatus, PartSource, Performer, ProductionEvent, ProductionOrder, ProductionSite, RouteStage,
    SiteKind, StageCompletion, StageStatus,
)
from app.models.purchasing import (
    GoodsReceipt, GoodsReceiptItem, GRNStatus, POStatus, PurchaseOrder, PurchaseOrderItem, PurchaseRequisition,
    PurchaseRequisitionItem, RequisitionStatus,
)
from app.models.system import (
    Attachment, AuditLog, DocumentSequence, IdempotencyRecord, Notification, NotificationPreference, SystemSetting,
)
from app.models.workforce import (
    AssignmentSource, AssignmentStatus, AttendanceCorrection, AttendanceRecord, AttendanceSource, AttendanceStatus,
    CalcStatus, ClaimOutcome, ConflictKind, ConflictStatus, CorrectionStatus, DayRule, DeliveryStatus, Designation,
    Employee, EmployeeSkill, EmployeeStatus, EmploymentType, HalfDay, Holiday, LeaveApproval, LeaveBalance, LeaveRequest,
    LeaveStatus, LeaveType, NotificationDelivery, OpportunityPosition, OpportunityRecipient, OpportunitySource,
    OpportunityStatus, OvertimeCalculation, OvertimeClaim, OvertimeOpportunity, OvertimePolicy, PayrollExportBatch,
    PositionKind, RoundingMode, Shift, ShiftAssignment, ShiftPattern, ShiftPatternDay, ShiftPosition, ShiftStatus,
    ShiftTemplate, Skill, SkillKind, Team, WorkforceConflict, WorkforcePolicy, WorkLocation, leave_request_shifts,
    shift_skills, shift_template_skills, team_members,
)

__all__ = [name for name in dir() if not name.startswith("_")]
