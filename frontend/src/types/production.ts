/** Company X production flow types (client/company-x). Decimals are strings. */
import type { Dec, UUID } from "./api";

export interface Site { warehouse_id: UUID; kind: "plant" | "vendor"; code: string; name: string; city: string | null; supplier_id: UUID | null; supplier_name: string | null; is_active: boolean }
export interface Customer { id: UUID; code: string; name: string; contact_person: string | null; email: string | null; phone: string | null; address: string | null; city: string | null; gstin: string | null; is_active: boolean }

export interface StructureBom { id: UUID; component_id: UUID; component_sku: string; component_name: string; uom: string; quantity: Dec; notes: string | null; component_is_made: boolean }
export interface StructureStage { id: UUID; sequence: number; name: string; performer: "plant" | "vendor"; site_id: UUID | null; site_name: string | null; rate_per_unit: Dec; planned_days: number; notes: string | null }
export interface Structure { product_id: UUID; sku: string; name: string; uom: string; bom: StructureBom[]; route: StructureStage[] }
export interface StructureRow { product_id: UUID; sku: string; name: string; stages: number; components: number }

export interface VendorHolding { site_id: UUID; site_name: string; units: Dec; sent: Dec; accepted: Dec; scrapped: Dec; rework: Dec; since: string | null; expected_return: string | null }
export interface OrderStage {
  id: UUID; sequence: number; name: string; performer: "plant" | "vendor"; site_id: UUID; site_name: string; rate_per_unit: Dec;
  planned_start: string | null; planned_end: string | null; actual_start: string | null; actual_end: string | null;
  status: "planned" | "in_progress" | "completed"; late: boolean; is_last: boolean; target: Dec; units_completed: Dec; units_scrapped: Dec;
  waiting_at_plant: Dec; at_vendors: VendorHolding[]; rework: Dec; notes: string | null;
}
export interface Position {
  kind: "not_started" | "waiting" | "at_vendor" | "done" | "consumed" | "stock"; label: string; site_name: string; units: Dec; stage?: string | null;
  since?: string | null; rework?: Dec; expected_return?: string | null; overdue?: boolean; required?: Dec; short?: Dec;
}
export interface KitLine { sku: string; name: string; uom: string; needed: Dec; available_at_plant: Dec; short: Dec }
export interface OrderPart {
  id: UUID; parent_id: UUID | null; level: number; product_id: UUID; sku: string; name: string; uom: string; quantity: Dec; qty_per_parent: Dec;
  source: "make" | "buy"; stages: OrderStage[]; positions: Position[]; kit: KitLine[]; progress: number; conversion_cost_per_unit: Dec;
}
export interface ProductionEvent { id: UUID; event_type: string; message: string; units: Dec | null; stage_id: UUID | null; user_name: string | null; created_at: string }
export interface ProductionOrder {
  id: UUID; order_number: string; product_id: UUID; product_sku: string; product_name: string; uom: string; quantity: Dec; customer_id: UUID | null;
  customer_name: string | null; plant_id: UUID; plant_name: string; start_date: string; due_date: string | null;
  status: "planned" | "released" | "in_progress" | "completed" | "cancelled"; notes: string | null; released_at: string | null; completed_at: string | null;
  cancel_reason: string | null; created_at: string; progress: number; planned_finish: string | null; at_risk: boolean; late_stages: number;
  parts: OrderPart[]; events: ProductionEvent[];
}
export type OrderRow = Pick<ProductionOrder, "id" | "order_number" | "product_sku" | "product_name" | "uom" | "quantity" | "customer_name" | "plant_name" | "start_date" | "due_date" | "status" | "progress" | "planned_finish" | "at_risk" | "late_stages" | "created_at">;

export interface Challan {
  id: UUID; challan_number: string; order_id: UUID; order_number: string; part_name: string; part_sku: string; stage_name: string; units: Dec;
  from_site: string; vendor_site_id: UUID; vendor_name: string; vendor_address: string | null; vendor_gstin: string | null; challan_date: string;
  expected_return_date: string | null; vehicle_number: string | null; notes: string | null; created_at: string;
  company: { name: string; address: string; tax_id: string | null } | null;
  lines: { sku: string; name: string; uom: string; quantity: Dec; unit_value: Dec; value: Dec }[]; total_value: Dec;
}
export interface VendorWorkload extends Site {
  open_jobs: { order_id: UUID; order_number: string; part: string; sku: string; stage: string; stage_id: UUID; units: Dec; rework: Dec; since: string | null; expected_return: string | null; overdue: boolean }[];
  units_held: Dec; overdue_jobs: number; material_value: Dec; rejection_rate: number | null; units_processed: Dec; avg_turnaround_days: number | null;
}
export interface ProductionSummary { open_orders: number; late_stages: number; orders_at_risk: number; units_at_vendors: Dec; value_at_vendors: Dec; overdue_vendor_jobs: number }
export interface DispatchNote {
  id: UUID; dispatch_number: string; customer_id: UUID; customer_name: string; order_id: UUID | null; warehouse_id: UUID; warehouse_code: string;
  status: "draft" | "dispatched" | "cancelled"; dispatch_date: string; vehicle_number: string | null; transporter: string | null;
  customer_reference: string | null; notes: string | null; dispatched_at: string | null; created_at: string;
  lines: { id: UUID; product_id: UUID; sku: string; name: string; uom: string; quantity: Dec }[];
}
