/** Workforce & shift management types (decimals are strings, instants ISO-8601). */
import type { Dec, UUID } from "./api";

export interface EmpBrief { id: UUID; employee_code: string; full_name: string; department_id: UUID; department_name: string | null; designation: string | null }

export interface Employee extends EmpBrief {
  first_name: string; last_name: string | null; email: string | null; phone: string | null;
  status: "active" | "on_leave" | "suspended" | "inactive" | "terminated";
  employment_type: string; date_of_joining: string | null; work_location_id: UUID | null; work_location_name: string | null;
  designation_id: UUID | null; manager_id: UUID | null; manager_name: string | null; overtime_eligible: boolean; has_login: boolean;
  teams: { id: UUID; name: string }[]; skills: { skill_id: UUID; code: string; name: string; level: number; valid_until: string | null }[];
  base_hourly_rate?: Dec | null;
  // detail only
  date_of_leaving?: string | null; shift_pattern_id?: UUID | null; shift_pattern_name?: string | null; pattern_anchor_date?: string | null;
  weekly_hour_limit?: Dec | null; overtime_policy_id?: UUID | null; emergency_contact_name?: string | null; emergency_contact_phone?: string | null;
  address?: string | null; notes?: string | null; login_email?: string | null; leave_balances?: LeaveBalance[];
}

export interface Ref { id: UUID; code: string; name: string; is_active: boolean; address?: string | null; department_id?: UUID | null; kind?: string }
export interface Team { id: UUID; code: string; name: string; department_id: UUID; department_name?: string; description?: string | null; is_active?: boolean; member_count?: number; members?: EmpBrief[] }
export interface Holiday { id: UUID; holiday_date: string; name: string; work_location_id: UUID | null }

export interface ShiftTemplate {
  id: UUID; code: string; name: string; start_time: string; end_time: string; overnight: boolean; break_minutes: number; department_id: UUID;
  department_name: string; work_location_id: UUID | null; work_location_name: string | null; team_id: UUID | null; team_name: string | null;
  required_count: number; supervisor_id: UUID | null; overtime_policy_id: UUID | null; color: string | null; is_active: boolean; skills: { id: UUID; name: string }[];
}
export interface ShiftPattern { id: UUID; code: string; name: string; cycle_days: number; is_active: boolean; days: { day_index: number; template_id: UUID | null; template_name: string | null }[] }

export interface Assignment {
  id: UUID; shift_id: UUID; position_id: UUID; employee: EmpBrief; start_at: string; end_at: string; start: string; end: string;
  source: "schedule" | "marketplace" | "manual"; status: "assigned" | "on_leave" | "released" | "cancelled"; opportunity_id: UUID | null;
  leave_request_id: UUID | null; reason: string | null; rule_overrides: string[]; created_at: string; end_reason: string | null;
  shift_name: string; shift_date: string; department_name: string;
  withdraw_cutoff?: string; can_withdraw?: boolean; withdraw_needs_approval?: boolean;
}
export interface Position {
  id: UUID; slot: number; kind: "regular" | "cover"; window_start: string | null; window_end: string | null; assignment: Assignment | null;
  on_leave: { employee: EmpBrief; leave_request_id: UUID | null }[]; vacant: boolean; opportunity: { id: UUID; number: string; status: OppStatus } | null;
}
export interface Shift {
  id: UUID; template_id: UUID | null; name: string; shift_date: string; start_at: string; end_at: string; start: string; end: string; overnight: boolean;
  break_minutes: number; department_id: UUID; department_name: string; work_location_id: UUID | null; work_location_name: string | null;
  team_id: UUID | null; team_name: string | null; required_count: number; supervisor_id: UUID | null; status: string; notes: string | null;
  skills: { id: UUID; name: string }[]; color: string | null; filled: number; vacant: number; positions: Position[];
}
export interface CalendarLeave { id: UUID; employee: EmpBrief; start_date: string; end_date: string; half_day: string }

export type OppStatus = "draft" | "published" | "open" | "partially_filled" | "filled" | "expired" | "cancelled";
export interface PayEstimate { payable_minutes: number; estimated_overtime_minutes: number; regular_minutes: number; day_type: string; multiplier: Dec; estimated_amount: Dec | null }
export interface Opportunity {
  id: UUID; number: string; title: string; status: OppStatus; source: string; shift_id: UUID; shift_name: string; date: string;
  department_id: UUID; department_name: string; team_id: UUID | null; team_name: string | null; work_location_name: string | null;
  start_at: string; end_at: string; start: string; end: string; total_minutes: number; break_minutes: number; payable_minutes: number;
  positions_total: number; positions_filled: number; positions_left: number; required_skills: string[]; claim_deadline: string;
  claims_open_at: string | null; round: number; created_at: string; notes: string | null;
  claimed_by_me?: boolean; my_assignment_id?: UUID | null; eligible?: boolean; ineligible_reasons?: string[]; pay_estimate?: PayEstimate | null;
  claims?: { id: UUID; employee: EmpBrief; outcome: "won" | "lost" | "rejected"; reason_code: string | null; reason: string | null; claimed_at: string }[];
  recipients?: { round: number; notified: number; reachable: number }[]; assignments?: Assignment[];
  expired_at?: string | null; filled_at?: string | null; cancelled_at?: string | null; cancel_reason?: string | null; published_at?: string | null;
  can_manage?: boolean; claim_attempts?: number; filled_by?: EmpBrief[];
}
export interface Vacancy {
  shift: { id: UUID; name: string; shift_date: string; start: string; end: string; start_at: string; end_at: string; department_id: UUID; department_name: string; required_count: number; filled: number };
  vacant_positions: number; on_offer: number; opportunities: { id: UUID; number: string; status: OppStatus }[]; absent: EmpBrief[];
}
export interface Conflict {
  id: UUID; kind: "leave_cancelled_after_cover" | "withdrawal_request"; status: string; summary: string; department_id: UUID;
  shift: { id: UUID; name: string; date: string } | null; original: Assignment | null; replacement: Assignment | null;
  resolution: string | null; resolution_note: string | null; resolved_at: string | null; created_at: string; options: string[];
}

export interface LeaveType { id: UUID; code: string; name: string; is_paid: boolean; annual_days: Dec; carry_forward_max: Dec; unlimited: boolean; requires_document: boolean; allow_half_day: boolean; color: string | null; is_active: boolean }
export interface LeaveBalance { leave_type_id: UUID; code: string; name: string; color: string | null; unlimited: boolean; year: number; entitled: Dec; carried_forward: Dec; adjusted: Dec; used: Dec; pending: Dec; available: Dec | null }
export interface LeaveRequest {
  id: UUID; number: string; employee: EmpBrief; leave_type_id: UUID; leave_type: string; leave_type_code: string; start_date: string; end_date: string;
  half_day: "none" | "first_half" | "second_half"; days: Dec; reason: string | null; status: "draft" | "pending" | "approved" | "rejected" | "cancelled";
  submitted_at: string | null; decided_at: string | null; decision_note: string | null; cancelled_at: string | null; cancel_reason: string | null; created_at: string;
  requires_document: boolean; attachments: { id: UUID; filename: string; size_bytes: number }[];
  affected_shifts: { assignment_id: UUID; shift_id: UUID; name: string; date: string; start: string; end: string; assignment_status: string; opportunity: { id: UUID; number: string; status: OppStatus; positions_filled: number; positions_total: number } | null }[];
  history: { action: string; note: string | null; at: string }[]; can_decide: boolean; can_cancel: boolean;
  opportunities_created?: { id: UUID; number: string; status: string; title: string }[];
}

export interface AttendanceRecord {
  id: UUID; employee: EmpBrief; assignment_id: UUID | null; work_date: string; shift_name: string | null; scheduled_start: string | null; scheduled_end: string | null;
  check_in_at: string | null; check_out_at: string | null; check_in: string; check_out: string; scheduled: string; break_minutes: number; worked_minutes: number;
  late_minutes: number; early_leave_minutes: number; status: string; source: string; notes: string | null; updated_at: string;
}
export interface DaySheetRow { assignment_id: UUID; employee: EmpBrief; shift_id: UUID; shift_name: string; scheduled: string; start_at: string; end_at: string; source: string; live_status: string; record: AttendanceRecord | null }
export interface Correction {
  id: UUID; employee: EmpBrief; assignment_id: UUID | null; shift: { name: string; date: string; start: string; end: string } | null;
  check_in_at: string | null; check_out_at: string | null; break_minutes: number | null; status_override: string | null; reason: string;
  before: Record<string, unknown> | null; status: string; requested_at: string; decided_at: string | null; decision_note: string | null; requested_by_self: boolean;
}

export interface OvertimeCalc {
  id: UUID; employee: EmpBrief; work_date: string; revision: number; is_correction: boolean; day_type: string; scheduled_minutes: number; worked_minutes: number;
  regular_minutes: number; ot_daily_minutes: number; ot_weekly_minutes: number; ot_weekly_off_minutes: number; ot_holiday_minutes: number; night_minutes: number;
  payable_minutes: number; approved_minutes: number | null; multiplier: Dec; status: "calculated" | "approved" | "rejected" | "exported"; decision_note: string | null;
  decided_at: string | null; export_batch_id: UUID | null; policy_code: string | null; can_decide: boolean;
  base_rate?: Dec | null; amount?: Dec; night_allowance?: Dec; approved_amount?: Dec | null;
}
export interface PayrollBatch { id: UUID; number: string; period_start: string; period_end: string; department_id: UUID | null; department_name: string | null; employee_count: number; line_count: number; total_minutes: number; total_hours: Dec; total_amount: Dec; created_at: string }

export interface WorkforcePolicy {
  id: UUID; department_id: UUID | null; department_name: string | null; auto_publish: boolean; claim_deadline_hours: Dec; min_rest_hours: Dec; weekly_hour_cap: Dec | null;
  monthly_cover_hours_cap: Dec | null; withdrawal_cutoff_hours: Dec; auto_reopen_on_withdrawal: boolean; allow_self_check_in: boolean; half_day_auto_cover: boolean;
  show_pay_estimates: boolean; late_grace_minutes: number; half_day_min_hours: Dec; weekly_off_days: number[];
}
export interface OvertimePolicy {
  id: UUID; code: string; name: string; description: string | null; daily_threshold_hours: Dec; weekly_threshold_hours: Dec; ot_multiplier: Dec;
  weekly_off_rule: "all_overtime" | "normal"; weekly_off_multiplier: Dec; holiday_rule: "all_overtime" | "normal"; holiday_multiplier: Dec;
  night_start: string | null; night_end: string | null; night_allowance_per_hour: Dec; rounding_minutes: number; rounding_mode: "down" | "nearest" | "up";
  min_overtime_minutes: number; requires_approval: boolean; is_default: boolean; is_active: boolean;
}
export interface Channel { channel: string; provider: string; connected: boolean; detail: string }

export interface MeHome {
  employee: EmpBrief & { team_names: string[] }; today: Assignment[]; upcoming: Assignment[]; opportunities: Opportunity[]; my_overtime: Assignment[];
  leave_balances: LeaveBalance[]; leave_requests: { id: UUID; number: string; leave_type: string; start_date: string; end_date: string; days: Dec; status: string }[];
  attendance_month: Record<string, number>; overtime_month: { worked_minutes: number; pending_minutes: number; approved_minutes: number; estimated_amount: Dec | null };
  self_check_in: boolean; show_pay_estimates: boolean;
}

export interface ManagerDashboard {
  date: string;
  scheduled_today: { employee: EmpBrief; shift: string; scheduled: string; source: string }[];
  on_leave_today: { employee: EmpBrief; leave_type: string | null; start_date: string; end_date: string; half_day: string; request_id: UUID }[];
  absent_today: { employee: EmpBrief; shift: string; scheduled: string; assignment_id: UUID; recorded: boolean }[];
  vacancies: Vacancy[]; opportunities: Opportunity[]; filled_last_7_days: number;
  pending: { leave: number; corrections: number; overtime: number; conflicts: number };
  overtime_by_employee: { employee: EmpBrief; hours: Dec; approved_hours: Dec }[];
  coverage: { department_id: UUID; department_name: string; required: number; assigned: number; shifts: number; vacant: number; coverage_pct: number }[];
  recent_claims: { employee: EmpBrief; opportunity: string; title: string; outcome: string; reason: string | null; at: string }[];
}

export interface Candidate { employee: EmpBrief; eligible: boolean; failures: { code: string; message: string; hard: boolean }[]; same_department: boolean }
