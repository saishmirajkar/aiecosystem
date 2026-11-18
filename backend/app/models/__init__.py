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
from app.models.purchasing import (
    GoodsReceipt, GoodsReceiptItem, GRNStatus, POStatus, PurchaseOrder, PurchaseOrderItem, PurchaseRequisition,
    PurchaseRequisitionItem, RequisitionStatus,
)
from app.models.system import (
    Attachment, AuditLog, DocumentSequence, IdempotencyRecord, Notification, NotificationPreference, SystemSetting,
)

__all__ = [name for name in dir() if not name.startswith("_")]
