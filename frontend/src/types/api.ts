/** Types mirroring the backend Pydantic contracts (decimals are strings). */
export type Dec = string;
export type UUID = string;

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Company {
  id: UUID; name: string; legal_name: string | null; registration_number: string | null; tax_id: string | null;
  industry: string | null; address_line1: string | null; address_line2: string | null; city: string | null; state: string | null;
  postal_code: string | null; country: string | null; phone: string | null; email: string | null; website: string | null;
  logo_attachment_id: UUID | null; currency_code: string; timezone: string; date_format: string; number_format: string;
  fiscal_year_start_month: number; tax_config: Record<string, unknown>;
}

export interface RoleBrief { id: UUID; code: string; name: string }
export interface DepartmentBrief { id: UUID; code: string; name: string }

export interface User {
  id: UUID; email: string; full_name: string; phone: string | null; job_title: string | null; is_active: boolean;
  last_login_at: string | null; created_at: string; roles: RoleBrief[]; departments: DepartmentBrief[];
  primary_department_id: UUID | null; warehouse_ids: UUID[];
}

export interface Me extends User { permissions: string[]; company: Company | null }

export interface Role {
  id: UUID; code: string; name: string; description: string | null; is_system: boolean; permissions: string[]; user_count: number;
}
export interface Permission { code: string; name: string; module: string }
export interface Department {
  id: UUID; code: string; name: string; description: string | null; head_user_id: UUID | null; is_active: boolean; member_count: number;
}
export interface Setting { key: string; value: unknown; description: string | null; updated_at: string | null }

export interface Unit { id: UUID; code: string; name: string; category: string; decimals: number; is_active: boolean }
export interface Conversion { id: UUID; from_uom_id: UUID; to_uom_id: UUID; from_uom_code: string; to_uom_code: string; factor: Dec; product_id: UUID | null }
export interface Category { id: UUID; code: string; name: string; parent_id: UUID | null; description: string | null; is_active: boolean; product_count: number }

export interface Supplier {
  id: UUID; code: string; name: string; contact_person: string | null; email: string | null; phone: string | null;
  address: string | null; city: string | null; country: string | null; tax_id: string | null; payment_terms: string | null;
  currency_code: string | null; lead_time_days: number | null; notes: string | null; is_active: boolean; created_at: string;
}

export type StockLevelStatus = "ok" | "low" | "critical" | "out" | "overstock";
export interface StockSummary {
  physical: Dec; available: Dec; reserved: Dec; in_transit: Dec; damaged: Dec; average_cost: Dec; value: Dec; status: StockLevelStatus;
}
export const PRODUCT_TYPES = ["raw_material", "component", "work_in_progress", "finished_good", "spare_part", "consumable"] as const;
export type ProductType = (typeof PRODUCT_TYPES)[number];

export interface Product {
  id: UUID; sku: string; internal_code: string | null; barcode: string | null; name: string; description: string | null;
  category_id: UUID | null; category_name: string | null; product_type: ProductType; uom_id: UUID; uom_code: string;
  manufacturer: string | null; image_attachment_id: UUID | null; purchase_price: Dec; standard_cost: Dec; selling_price: Dec | null;
  min_stock_level: Dec; max_stock_level: Dec | null; reorder_level: Dec; reorder_quantity: Dec; track_batches: boolean;
  track_serials: boolean; track_expiry: boolean; shelf_life_days: number | null; attributes: Record<string, unknown>;
  is_active: boolean; created_at: string; updated_at: string; stock: StockSummary | null;
}
export interface ProductSupplier {
  id: UUID; supplier_id: UUID; supplier_code: string; supplier_name: string; supplier_sku: string | null;
  lead_time_days: number | null; last_purchase_price: Dec | null; is_preferred: boolean;
}

export interface Warehouse {
  id: UUID; code: string; name: string; address: string | null; city: string | null; manager_user_id: UUID | null;
  manager_name: string | null; is_active: boolean; location_count: number; stock_value: Dec; sku_count: number; created_at: string;
}
export interface Location {
  id: UUID; warehouse_id: UUID; parent_id: UUID | null; code: string; name: string | null; location_type: string; path: string;
  is_default: boolean; is_system: boolean; is_active: boolean;
}

export type StockStatus = "unrestricted" | "damaged" | "returned" | "in_transit";
export interface Balance {
  id: UUID; product_id: UUID; product_sku: string; product_name: string; uom_code: string; category_name: string | null;
  warehouse_id: UUID; warehouse_code: string; warehouse_name: string; location_id: UUID; location_path: string;
  batch_id: UUID | null; batch_number: string | null; expiry_date: string | null; stock_status: StockStatus;
  qty_on_hand: Dec; qty_reserved: Dec; qty_available: Dec; average_cost: Dec; value: Dec; last_movement_at: string | null;
}
export interface Movement {
  id: UUID; transaction_id: UUID; transaction_number: string; line_no: number; movement_type: string; product_id: UUID;
  product_sku: string; product_name: string; uom_code: string; batch_number: string | null; warehouse_id: UUID; warehouse_code: string;
  location_id: UUID; location_path: string; counterpart_location_path: string | null; stock_status: StockStatus; quantity: Dec;
  unit_cost: Dec; value: Dec; balance_after: Dec; serial_numbers: string[] | null; reference_type: string | null;
  reference_number: string | null; reason: string | null; performed_by: UUID; performed_by_name: string | null; created_at: string;
}
export interface Transaction {
  id: UUID; transaction_number: string; transaction_type: string; reference_type: string | null; reference_id: UUID | null;
  reference_number: string | null; reason: string | null; posted_at: string; movements: Movement[];
}

export interface TransferItem {
  id: UUID; line_no: number; product_id: UUID; product_sku: string; product_name: string; uom_code: string; batch_id: UUID | null;
  batch_number: string | null; source_location_id: UUID; source_location_path: string; destination_location_id: UUID;
  destination_location_path: string; quantity: Dec; serial_numbers: string[] | null;
}
export interface Transfer {
  id: UUID; transfer_number: string; source_warehouse_id: UUID; source_warehouse_code: string; destination_warehouse_id: UUID;
  destination_warehouse_code: string; status: string; notes: string | null; source: string; created_by: UUID | null; created_at: string;
  submitted_at: string | null; approved_by: UUID | null; approved_at: string | null; dispatched_at: string | null; received_at: string | null;
  cancel_reason: string | null; items: TransferItem[];
}

export interface AdjustmentItem {
  id: UUID; line_no: number; product_id: UUID; product_sku: string; product_name: string; uom_code: string; location_id: UUID;
  location_path: string; batch_id: UUID | null; batch_number: string | null; stock_status: StockStatus; system_quantity: Dec | null;
  counted_quantity: Dec | null; quantity_delta: Dec; unit_cost: Dec | null; reason: string | null;
}
export interface Adjustment {
  id: UUID; adjustment_number: string; adjustment_type: string; warehouse_id: UUID; warehouse_code: string; status: string;
  reason: string; notes: string | null; source: string; created_by: UUID | null; created_at: string; submitted_at: string | null;
  approved_by: UUID | null; approved_at: string | null; rejection_reason: string | null; transaction_id: UUID | null;
  estimated_value: Dec; items: AdjustmentItem[];
}

export interface Reservation {
  id: UUID; product_id: UUID; product_sku: string; product_name: string; warehouse_id: UUID; location_path: string; quantity: Dec;
  status: string; reference_type: string | null; reference_number: string | null; notes: string | null; created_by: UUID;
  created_at: string; released_at: string | null;
}

export interface Alert {
  id: string; type: string; severity: "critical" | "warning" | "info"; title: string; detail: string; entity_type: string;
  entity_id: string; data: Record<string, unknown>;
}

export interface Overview {
  as_of: string;
  metrics: {
    active_products: number; total_stock_value: Dec; warehouse_count: number; low_stock_items: number;
    pending_goods_receipts: number; pos_awaiting_delivery: number; pending_purchase_orders: number; critical_alerts: number;
  };
  value_by_warehouse: { warehouse_id: string; warehouse_code: string; warehouse_name: string; value: Dec }[];
  distribution: { product_type: string; value: Dec }[];
  trend: { date: string; inbound_value: Dec; outbound_value: Dec; movements: number }[];
  recent_movements: Movement[];
  critical_alerts: Alert[];
}

export interface RequisitionItem {
  id: UUID; line_no: number; product_id: UUID; product_sku: string; product_name: string; uom_code: string; quantity: Dec;
  preferred_supplier_id: UUID | null; preferred_supplier_name: string | null; estimated_unit_cost: Dec | null; notes: string | null;
}
export interface Requisition {
  id: UUID; requisition_number: string; status: string; department_id: UUID | null; department_name: string | null;
  required_date: string | null; justification: string | null; source: string; created_by: UUID | null; created_by_name: string | null;
  created_at: string; submitted_at: string | null; approved_by: UUID | null; approved_at: string | null; rejection_reason: string | null;
  purchase_order_id: UUID | null; estimated_total: Dec; items: RequisitionItem[];
}

export interface POItem {
  id: UUID; line_no: number; product_id: UUID; product_sku: string; product_name: string; uom_code: string; description: string | null;
  quantity_ordered: Dec; quantity_received: Dec; quantity_rejected: Dec; quantity_outstanding: Dec; unit_price: Dec; tax_rate: Dec; line_total: Dec;
}
export interface PurchaseOrder {
  id: UUID; po_number: string; supplier_id: UUID; supplier_code: string; supplier_name: string; status: string; order_date: string;
  expected_delivery_date: string | null; is_overdue: boolean; delivery_warehouse_id: UUID; delivery_warehouse_code: string;
  currency_code: string; supplier_reference: string | null; payment_terms: string | null; notes: string | null; subtotal: Dec;
  tax_amount: Dec; total_amount: Dec; created_by: UUID | null; created_at: string; submitted_at: string | null; approved_by: UUID | null;
  approved_at: string | null; rejection_reason: string | null; sent_at: string | null; closed_at: string | null; cancel_reason: string | null;
  requisition_numbers: string[]; items: POItem[];
}

export interface GRNItem {
  id: UUID; line_no: number; purchase_order_item_id: UUID; product_id: UUID; product_sku: string; product_name: string; uom_code: string;
  location_id: UUID; location_path: string; quantity_received: Dec; quantity_damaged: Dec; quantity_rejected: Dec; unit_cost: Dec;
  batch_number: string | null; expiry_date: string | null; serial_numbers: string[] | null; rejection_reason: string | null;
}
export interface GoodsReceipt {
  id: UUID; grn_number: string; purchase_order_id: UUID; po_number: string; supplier_id: UUID; supplier_name: string; warehouse_id: UUID;
  warehouse_code: string; status: string; received_date: string; delivery_note_number: string | null; invoice_number: string | null;
  transporter: string | null; notes: string | null; over_receipt_reason: string | null; created_by: UUID | null; created_at: string;
  confirmed_by: UUID | null; confirmed_at: string | null; transaction_id: UUID | null; total_value: Dec; items: GRNItem[];
}

export interface ReportMeta { id: string; title: string; description: string; filters: string[]; columns: { key: string; label: string }[] }
export interface ReportResult {
  id: string; title: string; columns: { key: string; label: string }[]; rows: Record<string, unknown>[]; total: number;
  page: number; page_size: number; generated_at: string;
}

// ---------------------------------------------------------------- AI
export type Provenance = "verified" | "calculated" | "recommendation" | "draft" | "completed";
export interface CardSource { type: string; id: string; label: string; as_of: string }
export interface AICard {
  type: "table" | "metrics" | "approval" | "notice"; title: string; provenance: Provenance;
  columns?: { key: string; label: string }[]; rows?: Record<string, unknown>[];
  metrics?: { label: string; value: string; unit?: string }[]; sources: CardSource[]; note?: string | null; as_of: string;
  approval_id?: string; status?: string; action_type?: string; summary?: string; expires_at?: string;
}
export interface Approval {
  id: UUID; action_type: string; params: Record<string, unknown>; summary: string; preview: { rows?: Record<string, unknown>[] } | null;
  status: "pending" | "executed" | "rejected" | "failed" | "expired"; required_permission: string; requested_by: UUID;
  requested_via: string; agent_id: string | null; conversation_id: UUID | null; decided_by: UUID | null; decided_at: string | null;
  decision_note: string | null; result: { record_type?: string; record_id?: string; number?: string; status?: string; link?: string } | null;
  error: string | null; expires_at: string; created_at: string; requested_by_name?: string; decided_by_name?: string; can_decide?: boolean;
}
export interface AIPayload {
  text: string; engine: "claude" | "offline" | null; status: string;
  routing: { agents: { id: string; name: string }[]; reason: string; unconnected: string[] };
  cards: AICard[]; approvals: Approval[]; tool_calls: { tool: string; status: string; duration_ms: number; error: string | null; agent?: string }[];
  notices: string[];
}
export interface AIMessage {
  id: string; conversation_id: string; role: "user" | "assistant"; content: string; payload: AIPayload | null; status: string; created_at: string;
}
export interface Conversation { id: string; title: string; created_at: string; updated_at: string }
export interface AgentInfo {
  id: string; name: string; department: string; description: string; version: string; status: string; required_permissions: string[];
  tools: { name: string; description: string; kind: string; permission: string }[]; suggested_questions: string[]; available_to_you: boolean;
}
export interface Notification { id: UUID; type: string; title: string; body: string | null; data: Record<string, unknown> | null; read_at: string | null; created_at: string }
export interface AuditEntry {
  id: UUID; actor_user_id: UUID | null; actor_name: string | null; action: string; entity_type: string | null; entity_id: string | null;
  changes: Record<string, unknown> | null; ip_address: string | null; created_at: string;
}
