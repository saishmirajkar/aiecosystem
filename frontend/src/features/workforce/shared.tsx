import { useQuery } from "@tanstack/react-query";
import { Clock, MapPin, Users } from "lucide-react";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui/primitives";
import { useAuth } from "@/hooks/auth";
import { useDepartments } from "@/hooks/data";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { LeaveType, Opportunity, Ref, ShiftTemplate, Team } from "@/types/workforce";

/** Today's date in the company's time zone (YYYY-MM-DD). */
export function useToday(): string {
  const tz = useAuth().user?.company?.timezone;
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz || undefined }).format(new Date());
}

export function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function mondayOf(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return addDays(iso, -((d.getUTCDay() + 6) % 7));
}

export function dayLabel(iso: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" }): string {
  return new Intl.DateTimeFormat("en-IN", { ...opts, timeZone: "UTC" }).format(new Date(`${iso}T00:00:00Z`));
}

export function hrs(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return "—";
  const h = minutes / 60;
  return `${Number.isInteger(h) ? h : h.toFixed(1)} h`;
}

/** Local wall time in the company zone, e.g. for datetime-local inputs. */
export function toLocalInput(iso: string | null | undefined, tz?: string): string {
  if (!iso) return "";
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(new Date(iso));
  const g = (t: string) => parts.find((p) => p.type === t)?.value ?? "00";
  return `${g("year")}-${g("month")}-${g("day")}T${g("hour")}:${g("minute")}`;
}

/** Interpret a datetime-local value in the company zone and return an ISO instant. */
export function fromLocalInput(value: string, tz?: string): string | null {
  if (!value) return null;
  const guess = new Date(`${value}:00Z`);
  const shown = toLocalInput(guess.toISOString(), tz);
  const drift = new Date(`${shown}:00Z`).getTime() - guess.getTime();
  return new Date(guess.getTime() - drift).toISOString();
}

export function useCompanyTz(): string | undefined {
  return useAuth().user?.company?.timezone;
}

export const useTemplates = (departmentId?: string) =>
  useQuery({ queryKey: ["shift-templates", departmentId], queryFn: () => api.get<ShiftTemplate[]>("/shift-templates", { department_id: departmentId }), staleTime: 60_000 });
export const useTeams = () => useQuery({ queryKey: ["wf-teams"], queryFn: () => api.get<Team[]>("/workforce/teams"), staleTime: 60_000 });
export const useLeaveTypes = () => useQuery({ queryKey: ["leave-types"], queryFn: () => api.get<LeaveType[]>("/leave/types"), staleTime: 300_000 });
export const useRefs = (kind: "locations" | "designations" | "skills") =>
  useQuery({ queryKey: ["wf-ref", kind], queryFn: () => api.get<Ref[]>(`/workforce/${kind}`), staleTime: 60_000 });

export function DepartmentSelect({ value, onChange, id, allLabel = "All departments", onlyIds }: {
  value: string; onChange: (v: string) => void; id?: string; allLabel?: string | null; onlyIds?: string[];
}) {
  const { data } = useDepartments();
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)} aria-label="Department"
      className="h-9 rounded-lg border border-line bg-surface px-2.5 text-sm outline-none focus:border-accent">
      {allLabel !== null && <option value="">{allLabel}</option>}
      {data?.filter((d) => !onlyIds || onlyIds.includes(d.id)).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
    </select>
  );
}

const OPP_STATUS_TEXT: Record<string, string> = {
  draft: "Draft", published: "Opens soon", open: "Open", partially_filled: "Partly filled", filled: "Filled", expired: "Expired", cancelled: "Cancelled",
};
export function OppStatus({ status }: { status: string }) {
  const tone = ({ open: "accent", partially_filled: "accent", filled: "ok", expired: "bad", draft: "neutral", published: "info", cancelled: "neutral" } as const)[status as "open"] ?? "neutral";
  return <Badge tone={tone}>{OPP_STATUS_TEXT[status] ?? status}</Badge>;
}

export function Meta({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return <span className="inline-flex items-center gap-1 text-xs text-muted [&_svg]:size-3.5">{icon}{children}</span>;
}

export function OppSummary({ o, showEstimate = true }: { o: Opportunity; showEstimate?: boolean }) {
  const est = o.pay_estimate;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-[15px] font-semibold tracking-tight">{dayLabel(o.date, { weekday: "long", day: "numeric", month: "long" })}</span>
        <span className="num text-sm text-fg-2">{o.start}–{o.end}</span>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        <Meta icon={<Clock />}>{o.shift_name} · {hrs(o.total_minutes)}{o.break_minutes ? ` incl. ${o.break_minutes} min break` : ""}</Meta>
        <Meta icon={<Users />}>{o.team_name ?? o.department_name}</Meta>
        {o.work_location_name && <Meta icon={<MapPin />}>{o.work_location_name}</Meta>}
      </div>
      {o.required_skills.length > 0 && <div className="flex flex-wrap gap-1">{o.required_skills.map((s) => <Badge key={s}>{s}</Badge>)}</div>}
      {showEstimate && est && (
        <p className="text-xs text-fg-2">
          Paid hours {hrs(est.payable_minutes)}
          {est.estimated_overtime_minutes > 0 ? <> · about <b>{hrs(est.estimated_overtime_minutes)}</b> likely at the overtime rate</> : <> · likely regular time this week</>}
          {est.estimated_amount && <> · estimated <b>{money(est.estimated_amount)}</b></>}
        </p>
      )}
    </div>
  );
}

export function Pill({ children, tone = "neutral", className }: { children: ReactNode; tone?: "neutral" | "ok" | "warn" | "bad" | "accent"; className?: string }) {
  const map = { neutral: "bg-surface-2 text-fg-2", ok: "bg-ok-soft text-ok", warn: "bg-warn-soft text-warn", bad: "bg-bad-soft text-bad", accent: "bg-accent-soft text-accent" };
  return <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[11.5px] font-medium", map[tone], className)}>{children}</span>;
}

export const SOURCE_TEXT: Record<string, string> = { schedule: "Rostered", marketplace: "Overtime (claimed)", manual: "Overtime (assigned)" };
