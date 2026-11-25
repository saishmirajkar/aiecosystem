import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, CalendarPlus, ChevronLeft, ChevronRight, Copy, Megaphone, Plus, RefreshCw, UserPlus, Users, Wand2, Zap } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ConfirmDialog, Dialog, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/overlay";
import { EmptyState, ErrorState, PageHeader, Stat, StatusBadge } from "@/components/ui/page";
import { Badge, Button, Card, CardHeader, Checkbox, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { dateTime, relative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Page } from "@/types/api";
import type { Candidate, CalendarLeave, Conflict, LeaveRequest, ManagerDashboard, Opportunity, Position, Shift, Vacancy } from "@/types/workforce";
import { addDays, dayLabel, DepartmentSelect, fromLocalInput, hrs, mondayOf, OppStatus, Pill, SOURCE_TEXT, toLocalInput, useCompanyTz, useTemplates, useToday } from "./shared";

const ALL_INV = [["wf-dash"], ["wf-cal"], ["wf-opps"], ["wf-opp"], ["wf-vac"], ["leave-requests"], ["wf-conflicts"]];

// ------------------------------------------------------------------ dashboard
export function WorkforceDashboardPage() {
  const [dept, setDept] = useState("");
  const d = useQuery({ queryKey: ["wf-dash", dept], queryFn: () => api.get<ManagerDashboard>("/workforce/dashboard", { department_id: dept || undefined }), refetchInterval: 15_000 });
  if (d.error) return <ErrorState error={d.error} onRetry={d.refetch} />;
  const x = d.data;
  const vacant = x?.vacancies.reduce((a, v) => a + v.vacant_positions, 0) ?? 0;
  const live = x?.opportunities.filter((o) => ["open", "partially_filled"].includes(o.status)) ?? [];
  const maxOt = Math.max(1, ...(x?.overtime_by_employee.map((r) => Number(r.hours)) ?? [1]));
  return (
    <div className="space-y-4">
      <PageHeader title="Workforce" description={x ? `Shifts, cover and overtime for ${dayLabel(x.date, { weekday: "long", day: "numeric", month: "long" })} · updates every 15 seconds` : "Loading…"}
        actions={<DepartmentSelect value={dept} onChange={setDept} />} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {!x ? Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-[104px] rounded-xl" />) : <>
          <Stat label="Working today" value={x.scheduled_today.length} icon={<Users />} to="/workforce/attendance" />
          <Stat label="On leave today" value={x.on_leave_today.length} tone={x.on_leave_today.length ? "warn" : undefined} to="/workforce/leave?status=approved" />
          <Stat label="Absent today" value={x.absent_today.length} tone={x.absent_today.length ? "bad" : "ok"} hint="No check-in after grace" to="/workforce/attendance" />
          <Stat label="Vacant · next 7 days" value={vacant} tone={vacant ? "bad" : "ok"} to="/workforce/overtime?tab=vacancies" />
          <Stat label="Open overtime" value={live.length} hint={`${x.filled_last_7_days} filled in the last 7 days`} icon={<Zap />} to="/workforce/overtime" />
          <Stat label="Waiting for you" value={x.pending.leave + x.pending.corrections + x.pending.overtime + x.pending.conflicts}
            hint={`${x.pending.leave} leave · ${x.pending.corrections} corrections · ${x.pending.overtime} overtime · ${x.pending.conflicts} conflicts`}
            tone={x.pending.conflicts ? "bad" : undefined} to="/workforce/leave" />
        </>}
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Overtime opportunities" description="Live claim progress. First eligible claim wins." actions={<Link to="/workforce/overtime" className="text-xs text-accent hover:underline">Open board</Link>} />
          <div className="divide-y divide-line">
            {!x ? <Skeleton className="m-4 h-32" /> : !x.opportunities.length ? <EmptyState title="No open opportunities" description="Approved leave creates them automatically." icon={<Zap />} /> :
              x.opportunities.slice(0, 8).map((o) => (
                <Link key={o.id} to={`/workforce/overtime/${o.id}`} className="flex items-center gap-4 px-5 py-3 hover:bg-surface-2/50">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{o.shift_name} · {dayLabel(o.date)} <span className="num font-normal text-fg-2">{o.start}–{o.end}</span></div>
                    <div className="text-xs text-muted">{o.team_name ?? o.department_name} · {o.number} · claim by {dateTime(o.claim_deadline)}</div>
                  </div>
                  <FillBar filled={o.positions_filled} total={o.positions_total} />
                  <OppStatus status={o.status} />
                </Link>
              ))}
          </div>
        </Card>
        <Card>
          <CardHeader title="Latest claims" />
          <ul className="max-h-[360px] divide-y divide-line overflow-y-auto">
            {!x?.recent_claims.length && <li className="px-5 py-6 text-center text-sm text-muted">No claims yet.</li>}
            {x?.recent_claims.map((c, i) => (
              <li key={i} className="px-5 py-2.5 text-sm">
                <div className="flex items-center justify-between gap-2"><span className="font-medium">{c.employee.full_name}</span><StatusBadge status={c.outcome} /></div>
                <div className="text-xs text-muted">{c.opportunity} · {relative(c.at)}{c.outcome === "rejected" && c.reason ? ` · ${c.reason}` : ""}</div>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="Coverage today" description="Assigned vs required positions" />
          <div className="space-y-3 px-5 pb-5">
            {!x?.coverage.length && <p className="text-sm text-muted">No shifts today.</p>}
            {x?.coverage.map((c) => (
              <div key={c.department_id}>
                <div className="flex justify-between text-sm"><span>{c.department_name}</span><span className="num text-fg-2">{c.assigned}/{c.required}</span></div>
                <div className="mt-1 h-2 overflow-hidden rounded-full bg-surface-2"><div className={cn("h-full rounded-full", c.coverage_pct >= 100 ? "bg-ok" : c.coverage_pct >= 80 ? "bg-warn" : "bg-bad")}
                  style={{ width: `${Math.min(100, c.coverage_pct)}%` }} /></div>
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <CardHeader title="Away today" />
          <ul className="divide-y divide-line">
            {!x?.on_leave_today.length && !x?.absent_today.length && <li className="px-5 py-6 text-center text-sm text-muted">Everyone scheduled is in.</li>}
            {x?.absent_today.map((a) => <li key={a.assignment_id} className="flex items-center justify-between px-5 py-2.5 text-sm"><span>{a.employee.full_name}<span className="block text-xs text-muted">{a.shift} {a.scheduled}</span></span><Pill tone="bad">Absent</Pill></li>)}
            {x?.on_leave_today.map((a) => <li key={a.request_id} className="flex items-center justify-between px-5 py-2.5 text-sm"><span>{a.employee.full_name}<span className="block text-xs text-muted">{a.leave_type ?? "Leave"}{a.half_day !== "none" ? " · half day" : ""}</span></span><Pill tone="warn">On leave</Pill></li>)}
          </ul>
        </Card>
        <Card>
          <CardHeader title="Overtime this month" description="Hours from verified attendance" />
          <div className="space-y-2.5 px-5 pb-5">
            {!x?.overtime_by_employee.length && <p className="text-sm text-muted">No overtime recorded this month.</p>}
            {x?.overtime_by_employee.map((r) => (
              <div key={r.employee.id}>
                <div className="flex justify-between text-sm"><span className="truncate">{r.employee.full_name}</span><span className="num text-fg-2">{Number(r.hours)} h</span></div>
                <div className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-surface-2">
                  <div className="h-full bg-ok" style={{ width: `${(Number(r.approved_hours) / maxOt) * 100}%` }} />
                  <div className="h-full bg-warn" style={{ width: `${((Number(r.hours) - Number(r.approved_hours)) / maxOt) * 100}%` }} />
                </div>
              </div>
            ))}
            {!!x?.overtime_by_employee.length && <p className="pt-1 text-[11px] text-muted"><span className="text-ok">■</span> approved <span className="ml-2 text-warn">■</span> awaiting approval</p>}
          </div>
        </Card>
      </div>
    </div>
  );
}

function FillBar({ filled, total }: { filled: number; total: number }) {
  return (
    <div className="hidden w-28 items-center gap-2 sm:flex">
      <div className="flex h-1.5 flex-1 gap-0.5">{Array.from({ length: total }).map((_, i) => <span key={i} className={cn("flex-1 rounded-full", i < filled ? "bg-ok" : "bg-surface-2")} />)}</div>
      <span className="num text-xs text-fg-2">{filled}/{total}</span>
    </div>
  );
}

// ------------------------------------------------------------------ schedule
type View = "week" | "day" | "month";

export function SchedulePage() {
  const today = useToday();
  const [view, setView] = useState<View>("week");
  const [anchor, setAnchor] = useState(mondayOf(today));
  const [dept, setDept] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"new" | "generate" | "copy" | null>(null);
  const start = view === "week" ? mondayOf(anchor) : view === "day" ? anchor : `${anchor.slice(0, 8)}01`;
  const end = view === "week" ? addDays(start, 6) : view === "day" ? anchor : addDays(addDays(`${start.slice(0, 8)}28`, 4).slice(0, 8) + "01", -1);
  const cal = useQuery({ queryKey: ["wf-cal", start, end, dept], refetchInterval: 20_000,
    queryFn: () => api.get<{ shifts: Shift[]; leave: CalendarLeave[] }>("/shifts/calendar", { date_from: start, date_to: end, department_id: dept || undefined }) });
  const days = useMemo(() => { const out = []; for (let d = start; d <= end; d = addDays(d, 1)) out.push(d); return out; }, [start, end]);
  const rows = useMemo(() => {
    const keys = new Map<string, { key: string; name: string; time: string; dept: string; color: string | null }>();
    cal.data?.shifts.forEach((s) => { const k = `${s.department_name}|${s.name}|${s.start}`; if (!keys.has(k)) keys.set(k, { key: k, name: s.name, time: `${s.start}–${s.end}`, dept: s.department_name, color: s.color }); });
    return [...keys.values()].sort((a, b) => a.dept.localeCompare(b.dept) || a.time.localeCompare(b.time));
  }, [cal.data]);
  const shift = cal.data?.shifts.find((s) => s.id === open);
  const step = (n: number) => setAnchor(view === "month" ? addDays(`${start.slice(0, 8)}15`, n * 30).slice(0, 8) + "01" : addDays(anchor, n * (view === "week" ? 7 : 1)));
  const totals = cal.data?.shifts.reduce((a, s) => ({ req: a.req + s.positions.length, vac: a.vac + s.vacant }), { req: 0, vac: 0 });
  return (
    <div className="space-y-4">
      <PageHeader title="Shift schedule" description={totals ? `${totals.req} positions, ${totals.vac} vacant in this ${view}.` : "Plan shifts, see vacancies and who is covering."}
        actions={<Can permission="workforce.schedule">
          <Button onClick={() => setDialog("generate")}><Wand2 /> From patterns</Button>
          {view === "week" && <Button onClick={() => setDialog("copy")}><Copy /> Copy week</Button>}
          <Button variant="primary" onClick={() => setDialog("new")}><CalendarPlus /> New shift</Button>
        </Can>} />
      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          <div className="flex items-center">
            <Button variant="ghost" size="icon" onClick={() => step(-1)} aria-label="Previous"><ChevronLeft /></Button>
            <Button variant="ghost" size="sm" onClick={() => setAnchor(view === "week" ? mondayOf(today) : today)}>Today</Button>
            <Button variant="ghost" size="icon" onClick={() => step(1)} aria-label="Next"><ChevronRight /></Button>
          </div>
          <span className="text-sm font-medium">{view === "month" ? dayLabel(start, { month: "long", year: "numeric" }) : view === "day" ? dayLabel(start, { weekday: "long", day: "numeric", month: "long" }) : `${dayLabel(start)} – ${dayLabel(end)}`}</span>
          <div className="flex-1" />
          <DepartmentSelect value={dept} onChange={setDept} />
          <div className="flex rounded-lg border border-line p-0.5">{(["day", "week", "month"] as View[]).map((v) => (
            <button key={v} onClick={() => { setView(v); if (v === "day") setAnchor(today); }} className={cn("rounded-md px-2.5 py-1 text-xs capitalize", view === v ? "bg-surface-2 font-medium" : "text-muted")}>{v}</button>))}</div>
        </div>
        {cal.error ? <ErrorState error={cal.error} onRetry={cal.refetch} /> : !cal.data ? <Skeleton className="m-4 h-80" /> :
          view === "month" ? <MonthGrid days={days} shifts={cal.data.shifts} onPick={(d) => { setView("day"); setAnchor(d); }} /> :
          view === "day" ? <DayList shifts={cal.data.shifts} leave={cal.data.leave} day={start} onOpen={setOpen} /> :
            !rows.length ? <EmptyState title="No shifts this week" description="Create shifts, generate them from rotation patterns, or copy a previous week." icon={<CalendarClock />} /> : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[920px] table-fixed border-collapse text-xs">
                  <thead><tr><th className="w-44 border-b border-line bg-surface-2/60 px-3 py-2 text-left font-medium text-muted">Shift</th>
                    {days.map((d) => <th key={d} className={cn("border-b border-line bg-surface-2/60 px-2 py-2 text-left font-medium", d === today ? "text-accent" : "text-muted")}>{dayLabel(d)}</th>)}</tr></thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.key} className="align-top">
                        <td className="border-b border-line px-3 py-2"><div className="flex items-center gap-1.5 text-[13px] font-medium"><span className="size-2 rounded-full" style={{ background: r.color ?? "var(--muted)" }} />{r.name}</div>
                          <div className="num text-muted">{r.time}</div><div className="text-muted">{r.dept}</div></td>
                        {days.map((d) => {
                          const s = cal.data!.shifts.find((x) => x.shift_date === d && `${x.department_name}|${x.name}|${x.start}` === r.key);
                          return <td key={d} className="border-b border-l border-line p-1.5">{s && <ShiftCell s={s} onOpen={() => setOpen(s.id)} />}</td>;
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
      </Card>
      {shift && <ShiftDialog s={shift} onClose={() => setOpen(null)} />}
      {dialog === "new" && <NewShiftDialog onClose={() => setDialog(null)} defaultDate={view === "day" ? start : today} />}
      {dialog === "generate" && <GenerateDialog onClose={() => setDialog(null)} start={start} end={view === "day" ? addDays(start, 6) : end} />}
      {dialog === "copy" && <CopyWeekDialog onClose={() => setDialog(null)} source={start} />}
    </div>
  );
}

function ShiftCell({ s, onOpen }: { s: Shift; onOpen: () => void }) {
  return (
    <button onClick={onOpen} className={cn("w-full rounded-md border px-1.5 py-1 text-left transition-colors hover:border-fg-2/50", s.vacant ? "border-bad/40 bg-bad-soft/40" : "border-line bg-surface")}>
      <div className="mb-0.5 flex items-center justify-between"><span className="num text-[10.5px] text-muted">{s.filled}/{s.positions.length}</span>{s.vacant > 0 && <span className="text-[10.5px] font-semibold text-bad">{s.vacant} vacant</span>}</div>
      {s.positions.map((p) => <PositionLine key={p.id} p={p} />)}
    </button>
  );
}

function PositionLine({ p }: { p: Position }) {
  if (p.assignment) return (
    <div className="truncate leading-5"><span className={cn(p.assignment.source !== "schedule" && "font-medium text-accent")}>{p.assignment.employee.full_name}</span>
      {p.on_leave.length > 0 && <span className="text-muted"> for {p.on_leave[0]!.employee.full_name.split(" ")[0]}</span>}</div>
  );
  return (
    <div className="truncate leading-5 text-bad">{p.on_leave.length ? <><s className="text-muted">{p.on_leave[0]!.employee.full_name}</s> </> : null}
      {p.opportunity ? <span>offered · {p.opportunity.status.replace("_", " ")}</span> : <span>vacant</span>}</div>
  );
}

function MonthGrid({ days, shifts, onPick }: { days: string[]; shifts: Shift[]; onPick: (d: string) => void }) {
  const lead = (new Date(`${days[0]}T00:00:00Z`).getUTCDay() + 6) % 7;
  return (
    <div className="grid grid-cols-7 gap-px bg-line p-px text-xs">
      {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="bg-surface-2/60 px-2 py-1.5 font-medium text-muted">{d}</div>)}
      {Array.from({ length: lead }).map((_, i) => <div key={`l${i}`} className="bg-surface" />)}
      {days.map((d) => {
        const day = shifts.filter((s) => s.shift_date === d);
        const req = day.reduce((a, s) => a + s.positions.length, 0);
        const vac = day.reduce((a, s) => a + s.vacant, 0);
        return (
          <button key={d} onClick={() => onPick(d)} className="min-h-20 bg-surface p-2 text-left hover:bg-surface-2/60">
            <div className="num font-medium">{Number(d.slice(8))}</div>
            {req > 0 && <div className="mt-1 text-muted">{day.length} shift(s) · {req - vac}/{req}</div>}
            {vac > 0 && <div className="mt-0.5 font-semibold text-bad">{vac} vacant</div>}
          </button>
        );
      })}
    </div>
  );
}

function DayList({ shifts, leave, day, onOpen }: { shifts: Shift[]; leave: CalendarLeave[]; day: string; onOpen: (id: string) => void }) {
  const away = leave.filter((l) => l.start_date <= day && l.end_date >= day);
  return (
    <div className="divide-y divide-line">
      {!shifts.length && <EmptyState title="No shifts on this day" icon={<CalendarClock />} />}
      {shifts.map((s) => (
        <button key={s.id} onClick={() => onOpen(s.id)} className="flex w-full flex-wrap items-start gap-4 px-5 py-3 text-left hover:bg-surface-2/50">
          <div className="w-40"><div className="text-sm font-medium">{s.name}</div><div className="num text-xs text-muted">{s.start}–{s.end}{s.overnight && " (+1)"}</div><div className="text-xs text-muted">{s.team_name ?? s.department_name}</div></div>
          <div className="grid flex-1 gap-1 text-sm sm:grid-cols-2 lg:grid-cols-3">{s.positions.map((p) => <PositionLine key={p.id} p={p} />)}</div>
          {s.vacant > 0 && <Pill tone="bad">{s.vacant} vacant</Pill>}
        </button>
      ))}
      {away.length > 0 && <p className="px-5 py-3 text-xs text-muted">On leave: {away.map((l) => l.employee.full_name).join(", ")}</p>}
    </div>
  );
}

function CandidatePicker({ url, onPick, picked }: { url: string; onPick: (c: Candidate) => void; picked?: string }) {
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["wf-cand", url], queryFn: () => api.get<Candidate[]>(url) });
  const rows = (list.data ?? []).filter((c) => !q || c.employee.full_name.toLowerCase().includes(q.toLowerCase()) || c.employee.employee_code.toLowerCase().includes(q.toLowerCase()));
  return (
    <div>
      <Input placeholder="Search name or code…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search employees" />
      <div className="mt-2 max-h-64 divide-y divide-line overflow-y-auto rounded-lg border border-line">
        {list.isLoading && <Skeleton className="m-2 h-20" />}
        {rows.map((c) => (
          <button key={c.employee.id} onClick={() => onPick(c)} disabled={c.failures.some((f) => f.hard)}
            className={cn("flex w-full items-start justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-surface-2/60 disabled:cursor-not-allowed disabled:opacity-50", picked === c.employee.id && "bg-accent-soft/60")}>
            <span><span className="font-medium">{c.employee.full_name}</span> <span className="text-xs text-muted">{c.employee.employee_code} · {c.employee.department_name}</span>
              {c.failures.length > 0 && <span className="block text-xs text-bad">{c.failures.map((f) => f.message).join(" · ")}</span>}</span>
            {c.eligible ? <Pill tone="ok">Eligible</Pill> : c.failures.some((f) => f.hard) ? <Pill tone="bad">Blocked</Pill> : <Pill tone="warn">Needs override</Pill>}
          </button>
        ))}
      </div>
    </div>
  );
}

function AssignDialog({ title, url, candidatesUrl, extra, onClose, requireReason }: {
  title: string; url: string; candidatesUrl: string; extra?: Record<string, unknown>; onClose: () => void; requireReason?: boolean;
}) {
  const [c, setC] = useState<Candidate | null>(null);
  const [reason, setReason] = useState("");
  const soft = c?.failures.filter((f) => !f.hard) ?? [];
  const [ack, setAck] = useState(false);
  const run = useAction(() => api.post(url, { employee_id: c!.employee.id, reason: reason || null, override_rules: ack ? soft.map((f) => f.code) : [], ...extra }),
    { success: "Assigned — the employee has been notified", invalidate: [...ALL_INV, ["wf-cand"]], onSuccess: onClose });
  const needReason = requireReason || soft.length > 0;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={title} size="lg"
      description={requireReason ? "Manual assignment is for when the marketplace doesn’t fill a shift. The reason is saved in the audit trail." : undefined}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={run.isPending}
        disabled={!c || (soft.length > 0 && !ack) || (needReason && reason.trim().length < 3)} onClick={() => run.mutate(undefined)}>Assign</Button></>}>
      <CandidatePicker url={candidatesUrl} onPick={(x) => { setC(x); setAck(false); }} picked={c?.employee.id} />
      {c && <div className="mt-3 space-y-2">
        {soft.length > 0 && <Checkbox label={`Override: ${soft.map((f) => f.message).join("; ")}`} checked={ack} onChange={(e) => setAck(e.target.checked)} />}
        {needReason && <Field label="Reason" htmlFor="as-r" required><Textarea id="as-r" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why this person, and what was agreed" /></Field>}
      </div>}
    </Dialog>
  );
}

function ShiftDialog({ s, onClose }: { s: Shift; onClose: () => void }) {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [assign, setAssign] = useState<Position | null>(null);
  const [releasing, setReleasing] = useState<Position | null>(null);
  const [offerAfter, setOfferAfter] = useState(true);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [count, setCount] = useState(String(s.required_count));
  const started = new Date(s.start_at) <= new Date();
  const release = useAction(({ p, reason }: { p: Position; reason: string }) => api.post(`/assignments/${p.assignment!.id}/release`, { reason, create_opportunity: offerAfter }),
    { success: offerAfter ? "Released and offered as overtime" : "Released", invalidate: ALL_INV, onSuccess: () => setReleasing(null) });
  const offer = useAction(() => api.post<Opportunity>("/overtime/opportunities", { shift_id: s.id }),
    { success: (o) => `${o.number} published to eligible employees`, invalidate: ALL_INV, onSuccess: (o) => navigate(`/workforce/overtime/${o.id}`) });
  const resize = useAction(() => api.patch(`/shifts/${s.id}`, { required_count: Number(count) }), { success: "Required count updated", invalidate: ALL_INV });
  const cancel = useAction((reason: string) => api.post(`/shifts/${s.id}/cancel`, { reason }), { success: "Shift cancelled; assigned employees notified", invalidate: ALL_INV, onSuccess: onClose });
  const unoffered = s.positions.filter((p) => p.vacant && !p.opportunity).length;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title={`${s.name} · ${dayLabel(s.shift_date, { weekday: "long", day: "numeric", month: "long" })}`}
      description={`${s.start}–${s.end}${s.overnight ? " (ends next day)" : ""} · ${s.break_minutes} min break · ${s.team_name ?? s.department_name}${s.skills.length ? ` · needs ${s.skills.map((k) => k.name).join(", ")}` : ""}`}
      footer={can("workforce.schedule") && !started ? <>
        <Button variant="ghost" onClick={() => setCancelOpen(true)}>Cancel shift</Button>
        {unoffered > 0 && can("workforce.overtime.manage") && <Button variant="primary" loading={offer.isPending} onClick={() => offer.mutate(undefined)}><Megaphone /> Offer {unoffered} vacant as overtime</Button>}
      </> : undefined}>
      <div className="space-y-2">
        {s.positions.map((p) => (
          <div key={p.id} className={cn("flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2", p.vacant ? "border-bad/40 bg-bad-soft/30" : "border-line")}>
            <div className="min-w-0 text-sm">
              <span className="num mr-2 text-xs text-muted">#{p.slot}{p.kind === "cover" && " cover"}</span>
              {p.assignment ? <><span className="font-medium">{p.assignment.employee.full_name}</span> <span className="text-xs text-muted">{SOURCE_TEXT[p.assignment.source]}{p.assignment.reason ? ` · ${p.assignment.reason}` : ""}</span></>
                : <span className="font-medium text-bad">Vacant</span>}
              {p.on_leave.length > 0 && <span className="block text-xs text-muted">On leave: {p.on_leave.map((x) => x.employee.full_name).join(", ")}</span>}
              {p.opportunity && <Link to={`/workforce/overtime/${p.opportunity.id}`} className="block text-xs text-accent hover:underline">{p.opportunity.number} · {p.opportunity.status.replace("_", " ")}</Link>}
            </div>
            {!started && can("workforce.schedule") && (p.assignment
              ? <Button size="sm" variant="ghost" onClick={() => setReleasing(p)}>Release</Button>
              : !p.opportunity && <Button size="sm" onClick={() => setAssign(p)}><UserPlus /> Assign</Button>)}
          </div>
        ))}
      </div>
      {!started && can("workforce.schedule") && <div className="mt-4 flex items-end gap-2 border-t border-line pt-4">
        <Field label="Required people" htmlFor="sh-c"><Input id="sh-c" type="number" min={0} className="w-28" value={count} onChange={(e) => setCount(e.target.value)} /></Field>
        <Button disabled={Number(count) === s.required_count} loading={resize.isPending} onClick={() => resize.mutate(undefined)}>Update</Button>
      </div>}
      {assign && <AssignDialog title={`Assign to ${s.name} ${dayLabel(s.shift_date)}`} url={`/shifts/${s.id}/assignments`} candidatesUrl={`/shifts/${s.id}/candidates`}
        extra={{ position_id: assign.id }} onClose={() => setAssign(null)} />}
      <ConfirmDialog open={!!releasing} onOpenChange={(o) => !o && setReleasing(null)} title={`Release ${releasing?.assignment?.employee.full_name}?`} requireReason tone="danger"
        confirmLabel="Release" loading={release.isPending} onConfirm={(reason) => releasing && release.mutate({ p: releasing, reason })}>
        <Checkbox label="Offer the position as overtime" checked={offerAfter} onChange={(e) => setOfferAfter(e.target.checked)} />
      </ConfirmDialog>
      <ConfirmDialog open={cancelOpen} onOpenChange={setCancelOpen} title="Cancel this shift?" description="Assigned employees are notified and open overtime offers for it are withdrawn."
        requireReason tone="danger" confirmLabel="Cancel shift" loading={cancel.isPending} onConfirm={(r) => cancel.mutate(r)} />
    </Dialog>
  );
}

function NewShiftDialog({ onClose, defaultDate }: { onClose: () => void; defaultDate: string }) {
  const templates = useTemplates();
  const [f, setF] = useState({ template_id: "", shift_date: defaultDate, required_count: "", notes: "" });
  const t = templates.data?.find((x) => x.id === f.template_id);
  const run = useAction(() => api.post<Shift>("/shifts", { template_id: f.template_id, shift_date: f.shift_date, required_count: f.required_count ? Number(f.required_count) : null, notes: f.notes || null }),
    { success: (s) => `${s.name} on ${dayLabel(s.shift_date)} created with ${s.required_count} vacant position(s)`, invalidate: ALL_INV, onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="New shift" footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={run.isPending} disabled={!f.template_id} onClick={() => run.mutate(undefined)}>Create</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Template" htmlFor="ns-t" className="sm:col-span-2" required><Select id="ns-t" value={f.template_id} onChange={(e) => setF({ ...f, template_id: e.target.value })}>
          <option value="">Select…</option>{templates.data?.map((x) => <option key={x.id} value={x.id}>{x.name} {x.start_time}–{x.end_time} · {x.team_name ?? x.department_name}</option>)}</Select></Field>
        <Field label="Date" htmlFor="ns-d"><Input id="ns-d" type="date" value={f.shift_date} onChange={(e) => setF({ ...f, shift_date: e.target.value })} /></Field>
        <Field label="People required" htmlFor="ns-c" hint={t ? `Template default: ${t.required_count}` : undefined}><Input id="ns-c" type="number" min={1} value={f.required_count} onChange={(e) => setF({ ...f, required_count: e.target.value })} /></Field>
        <Field label="Notes" htmlFor="ns-n" className="sm:col-span-2"><Textarea id="ns-n" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      </div>
    </Dialog>
  );
}

function GenerateDialog({ onClose, start, end }: { onClose: () => void; start: string; end: string }) {
  const [f, setF] = useState({ date_from: start, date_to: end, department_id: "" });
  const [res, setRes] = useState<{ shifts_created: number; assignments_created: number; skipped: { employee: string; date: string; shift: string; reason: string }[] } | null>(null);
  const run = useAction(() => api.post<NonNullable<typeof res>>("/shifts/generate", { ...f, department_id: f.department_id || null }), { invalidate: ALL_INV, onSuccess: setRes });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Generate from rotation patterns" description="Creates shifts and assigns employees according to their patterns. Existing assignments are kept; nobody is double-booked."
      footer={res ? <Button variant="primary" onClick={onClose}>Done</Button> : <><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={run.isPending} onClick={() => run.mutate(undefined)}>Generate</Button></>}>
      {!res ? <div className="grid gap-3 sm:grid-cols-3">
        <Field label="From" htmlFor="g-f"><Input id="g-f" type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></Field>
        <Field label="To" htmlFor="g-t"><Input id="g-t" type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></Field>
        <Field label="Department" htmlFor="g-d"><DepartmentSelect id="g-d" value={f.department_id} onChange={(v) => setF({ ...f, department_id: v })} /></Field>
      </div> : <div className="space-y-2 text-sm">
        <p><b>{res.shifts_created}</b> shift(s) created, <b>{res.assignments_created}</b> assignment(s) made.</p>
        {res.skipped.length > 0 && <><p className="text-fg-2">Skipped {res.skipped.length}:</p><ul className="max-h-48 overflow-y-auto text-xs text-muted">{res.skipped.map((x, i) => <li key={i}>{x.employee} · {x.shift} {dayLabel(x.date)}: {x.reason}</li>)}</ul></>}
      </div>}
    </Dialog>
  );
}

function CopyWeekDialog({ onClose, source }: { onClose: () => void; source: string }) {
  const [dept, setDept] = useState("");
  const [target, setTarget] = useState(addDays(source, 7));
  const [withPeople, setWithPeople] = useState(true);
  const run = useAction(() => api.post<{ shifts_created: number; assignments_created: number; skipped: unknown[] }>("/shifts/copy-week",
    { source_week_start: source, target_week_start: mondayOf(target), department_id: dept, include_assignments: withPeople }),
  { success: (r) => `Copied: ${r.shifts_created} shift(s), ${r.assignments_created} assignment(s)${r.skipped.length ? `, ${r.skipped.length} skipped` : ""}`, invalidate: ALL_INV, onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Copy the week of ${dayLabel(source)}`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!dept} loading={run.isPending} onClick={() => run.mutate(undefined)}>Copy</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Department" htmlFor="cw-d" required><DepartmentSelect id="cw-d" value={dept} onChange={setDept} allLabel="Select…" /></Field>
        <Field label="Into the week of" htmlFor="cw-t"><Input id="cw-t" type="date" value={target} onChange={(e) => setTarget(e.target.value)} /></Field>
        <Checkbox label="Copy assigned employees too (skips anyone on leave or already booked)" checked={withPeople} onChange={(e) => setWithPeople(e.target.checked)} />
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------ leave approvals
export function LeaveApprovalsPage() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "pending";
  const [open, setOpen] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["leave-requests", status], queryFn: () => api.get<Page<LeaveRequest>>("/leave/requests", { status: status || undefined, page_size: 100 }) });
  return (
    <div>
      <PageHeader title="Leave" description="Approving leave frees the employee’s shifts and offers them as overtime automatically."
        actions={<Select value={status} onChange={(e) => setParams({ status: e.target.value })} className="w-40" aria-label="Status">
          <option value="pending">Pending</option><option value="approved">Approved</option><option value="rejected">Rejected</option><option value="cancelled">Cancelled</option><option value="">All</option></Select>} />
      {list.error ? <ErrorState error={list.error} onRetry={list.refetch} /> : !list.data ? <Skeleton className="h-48" /> :
        !list.data.items.length ? <Card><EmptyState title={status === "pending" ? "Nothing waiting for you" : "No leave requests"} icon={<CalendarClock />} /></Card> : (
          <Card className="divide-y divide-line">
            {list.data.items.map((r) => (
              <button key={r.id} onClick={() => setOpen(r.id)} className="flex w-full flex-wrap items-center gap-4 px-5 py-3 text-left hover:bg-surface-2/50">
                <div className="min-w-0 flex-1"><div className="text-sm font-medium">{r.employee.full_name} <span className="font-normal text-muted">· {r.employee.department_name}</span></div>
                  <div className="text-xs text-muted">{r.leave_type} · {dayLabel(r.start_date)}{r.end_date !== r.start_date && ` – ${dayLabel(r.end_date)}`}{r.half_day !== "none" && ` (${r.half_day.replace("_", " ")})`} · {Number(r.days)} day(s) · {r.number}</div></div>
                <span className="text-xs text-fg-2">{r.affected_shifts.length} shift(s)</span>
                <StatusBadge status={r.status} />
              </button>
            ))}
          </Card>
        )}
      {open && <LeaveDetailDialog id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

export function LeaveDetailDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const r = useQuery({ queryKey: ["leave-request", id], queryFn: () => api.get<LeaveRequest>(`/leave/requests/${id}`) });
  const [note, setNote] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const inv = [...ALL_INV, ["leave-request", id]];
  const approve = useAction(() => api.post<LeaveRequest>(`/leave/requests/${id}/approve`, { note: note || null }), {
    success: (x) => `Approved${x.opportunities_created?.length ? ` · ${x.opportunities_created.length} overtime opportunit${x.opportunities_created.length === 1 ? "y" : "ies"} published` : ""}`, invalidate: inv });
  const reject = useAction((n: string) => api.post(`/leave/requests/${id}/reject`, { note: n }), { success: "Rejected; the employee was told why", invalidate: inv, onSuccess: () => setRejecting(false) });
  const cancel = useAction((reason: string) => api.post<{ conflicts: Conflict[] }>(`/leave/requests/${id}/cancel`, { reason }), {
    success: (x) => x.conflicts.length ? `Cancelled · ${x.conflicts.length} conflict(s) to resolve on the overtime board` : "Cancelled; shifts restored", invalidate: inv, onSuccess: () => setCancelling(false) });
  const d = r.data;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title={d ? `${d.employee.full_name} · ${d.leave_type}` : "Leave request"}
      description={d ? `${d.number} · ${dayLabel(d.start_date, { weekday: "short", day: "numeric", month: "long" })}${d.end_date !== d.start_date ? ` – ${dayLabel(d.end_date, { weekday: "short", day: "numeric", month: "long" })}` : ""} · ${Number(d.days)} working day(s)` : undefined}
      footer={d && (d.can_decide || d.can_cancel) ? <>
        {d.can_cancel && <Button variant="ghost" onClick={() => setCancelling(true)}>Cancel leave</Button>}
        {d.can_decide && <><Button onClick={() => setRejecting(true)}>Reject</Button><Button variant="primary" loading={approve.isPending} onClick={() => approve.mutate(undefined)}>Approve</Button></>}
      </> : undefined}>
      {!d ? <Skeleton className="h-40" /> : <div className="space-y-4 text-sm">
        <div className="flex flex-wrap gap-2"><StatusBadge status={d.status} />{d.half_day !== "none" && <Badge>{d.half_day.replace("_", " ")}</Badge>}</div>
        {d.reason && <div><div className="text-xs text-muted">Reason (visible to managers and HR only)</div><p>{d.reason}</p></div>}
        {d.attachments.length > 0 && <div><div className="text-xs text-muted">Documents</div>{d.attachments.map((a) =>
          <a key={a.id} href={api.downloadUrl(`/leave/requests/${d.id}/attachments/${a.id}`)} target="_blank" rel="noreferrer" className="mr-3 text-accent hover:underline">{a.filename}</a>)}</div>}
        <div>
          <div className="text-xs text-muted">Shifts affected</div>
          {!d.affected_shifts.length ? <p className="text-fg-2">No scheduled shifts in this period.</p> : <ul className="mt-1 divide-y divide-line rounded-lg border border-line">
            {d.affected_shifts.map((s) => (
              <li key={s.assignment_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                <span>{s.name} · {dayLabel(s.date)} <span className="num text-muted">{s.start}–{s.end}</span></span>
                {s.opportunity ? <Link to={`/workforce/overtime/${s.opportunity.id}`} className="text-xs text-accent hover:underline">{s.opportunity.number} · {s.opportunity.status.replace("_", " ")} ({s.opportunity.positions_filled}/{s.opportunity.positions_total})</Link>
                  : <span className="text-xs text-muted">{d.status === "pending" ? "Will be offered as overtime on approval" : s.assignment_status.replace("_", " ")}</span>}
              </li>))}
          </ul>}
        </div>
        {d.can_decide && <Field label="Note to the employee (optional)" htmlFor="lv-note"><Input id="lv-note" value={note} onChange={(e) => setNote(e.target.value)} /></Field>}
        {d.decision_note && <p className="text-fg-2">Decision note: {d.decision_note}</p>}
        <ul className="space-y-0.5 text-xs text-muted">{d.history.map((h, i) => <li key={i}>{h.action} · {dateTime(h.at)}{h.note ? ` · ${h.note}` : ""}</li>)}</ul>
      </div>}
      <ConfirmDialog open={rejecting} onOpenChange={setRejecting} title="Reject leave" requireReason reasonLabel="Reason the employee will see" tone="danger" confirmLabel="Reject" loading={reject.isPending} onConfirm={(n) => reject.mutate(n)} />
      <ConfirmDialog open={cancelling} onOpenChange={setCancelling} title="Cancel this leave?" requireReason tone="danger" confirmLabel="Cancel leave" loading={cancel.isPending} onConfirm={(n) => cancel.mutate(n)}
        description="Shifts return to the employee. Where a colleague already covers a shift, a conflict is raised for you to decide." />
    </Dialog>
  );
}

// ------------------------------------------------------------------ overtime board
export function OvertimeBoardPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "opportunities";
  const [status, setStatus] = useState("draft,published,open,partially_filled,expired");
  const opps = useQuery({ queryKey: ["wf-opps", status], refetchInterval: 10_000, queryFn: () => api.get<Page<Opportunity>>("/overtime/opportunities", { status: status || undefined, page_size: 100 }) });
  const vac = useQuery({ queryKey: ["wf-vac"], queryFn: () => api.get<Vacancy[]>("/overtime/vacancies") });
  const conflicts = useQuery({ queryKey: ["wf-conflicts"], queryFn: () => api.get<Conflict[]>("/workforce/conflicts") });
  return (
    <div className="space-y-4">
      <PageHeader title="Overtime" description="Vacancies become opportunities; the first eligible employee to claim gets the shift. Assign manually only when nobody claims." />
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v })}>
        <TabsList>
          <TabsTrigger value="opportunities">Opportunities</TabsTrigger>
          <TabsTrigger value="vacancies">Vacancies{vac.data?.length ? ` (${vac.data.reduce((a, v) => a + v.vacant_positions, 0)})` : ""}</TabsTrigger>
          <TabsTrigger value="conflicts">Conflicts{conflicts.data?.length ? ` (${conflicts.data.length})` : ""}</TabsTrigger>
        </TabsList>
        <TabsContent value="opportunities" className="mt-4">
          <Card>
            <div className="border-b border-line p-3"><Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-56" aria-label="Status">
              <option value="draft,published,open,partially_filled,expired">Needs attention or open</option><option value="open,partially_filled">Open</option>
              <option value="filled">Filled</option><option value="expired">Expired</option><option value="cancelled">Cancelled</option><option value="">All</option></Select></div>
            {opps.error ? <ErrorState error={opps.error} onRetry={opps.refetch} /> : !opps.data ? <Skeleton className="m-4 h-40" /> :
              !opps.data.items.length ? <EmptyState title="No opportunities" icon={<Zap />} /> : (
                <div className="divide-y divide-line">{opps.data.items.map((o) => (
                  <Link key={o.id} to={`/workforce/overtime/${o.id}`} className="flex flex-wrap items-center gap-4 px-5 py-3 hover:bg-surface-2/50">
                    <div className="min-w-0 flex-1"><div className="text-sm font-medium">{o.shift_name} · {dayLabel(o.date, { weekday: "short", day: "numeric", month: "short" })} <span className="num font-normal text-fg-2">{o.start}–{o.end}</span></div>
                      <div className="text-xs text-muted">{o.number} · {o.team_name ?? o.department_name} · {o.claim_attempts ?? 0} claim attempt(s){o.filled_by?.length ? ` · ${o.filled_by.map((e) => e.full_name).join(", ")}` : ""}</div></div>
                    <span className="text-xs text-muted">{o.status === "expired" ? "closed" : "closes"} {relative(o.claim_deadline)}</span>
                    <FillBar filled={o.positions_filled} total={o.positions_total} />
                    <OppStatus status={o.status} />
                  </Link>))}</div>
              )}
          </Card>
        </TabsContent>
        <TabsContent value="vacancies" className="mt-4"><VacancyList vac={vac.data} /></TabsContent>
        <TabsContent value="conflicts" className="mt-4"><ConflictList items={conflicts.data} /></TabsContent>
      </Tabs>
    </div>
  );
}

function VacancyList({ vac }: { vac?: Vacancy[] }) {
  const navigate = useNavigate();
  const offer = useAction((shiftId: string) => api.post<Opportunity>("/overtime/opportunities", { shift_id: shiftId }),
    { success: (o) => `${o.number} published`, invalidate: ALL_INV, onSuccess: (o) => navigate(`/workforce/overtime/${o.id}`) });
  if (!vac) return <Skeleton className="h-40" />;
  if (!vac.length) return <Card><EmptyState title="No vacancies in the next 7 days" icon={<Users />} /></Card>;
  return (
    <Card className="divide-y divide-line">{vac.map((v) => (
      <div key={v.shift.id} className="flex flex-wrap items-center gap-4 px-5 py-3">
        <div className="min-w-0 flex-1"><div className="text-sm font-medium">{v.shift.name} · {dayLabel(v.shift.shift_date)} <span className="num font-normal text-fg-2">{v.shift.start}–{v.shift.end}</span></div>
          <div className="text-xs text-muted">{v.shift.department_name} · {v.shift.filled}/{v.shift.required_count} filled{v.absent.length ? ` · away: ${v.absent.map((a) => a.full_name).join(", ")}` : ""}</div></div>
        <Pill tone="bad">{v.vacant_positions} vacant</Pill>
        {v.opportunities.map((o) => <Link key={o.id} to={`/workforce/overtime/${o.id}`} className="text-xs text-accent hover:underline">{o.number} · {o.status.replace("_", " ")}</Link>)}
        {v.on_offer < v.vacant_positions && <Can permission="workforce.overtime.manage"><Button size="sm" variant="primary" loading={offer.isPending} onClick={() => offer.mutate(v.shift.id)}><Megaphone /> Offer as overtime</Button></Can>}
      </div>))}
    </Card>
  );
}

function ConflictList({ items }: { items?: Conflict[] }) {
  const [deciding, setDeciding] = useState<{ c: Conflict; resolution: string } | null>(null);
  const run = useAction(({ c, resolution, note }: { c: Conflict; resolution: string; note: string }) => api.post(`/workforce/conflicts/${c.id}/resolve`, { resolution, note }),
    { success: "Resolved; the people involved were notified", invalidate: ALL_INV, onSuccess: () => setDeciding(null) });
  const LABEL: Record<string, string> = { keep_replacement: "Keep the replacement", reinstate_original: "Bring back the original employee", approve_release: "Approve withdrawal", decline: "Decline" };
  if (!items) return <Skeleton className="h-40" />;
  if (!items.length) return <Card><EmptyState title="No conflicts" description="Late withdrawals and leave cancelled after cover appear here." icon={<AlertTriangle />} /></Card>;
  return (
    <div className="space-y-3">{items.map((c) => (
      <Card key={c.id} className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0"><Badge tone="bad">{c.kind === "withdrawal_request" ? "Late withdrawal" : "Leave cancelled after cover"}</Badge>
            <p className="mt-2 text-sm">{c.summary}</p><p className="mt-1 text-xs text-muted">{relative(c.created_at)}</p></div>
          <Can permission="workforce.overtime.manage"><div className="flex flex-wrap gap-2">{c.options.map((o, i) => <Button key={o} size="sm" variant={i === 0 ? "primary" : "secondary"} onClick={() => setDeciding({ c, resolution: o })}>{LABEL[o]}</Button>)}</div></Can>
        </div>
      </Card>))}
      <ConfirmDialog open={!!deciding} onOpenChange={(o) => !o && setDeciding(null)} title={deciding ? LABEL[deciding.resolution]! : ""} requireReason reasonLabel="Note (saved and shared with the people affected)"
        confirmLabel="Confirm" loading={run.isPending} onConfirm={(note) => deciding && run.mutate({ ...deciding, note })} />
    </div>
  );
}

export function OpportunityDetailPage() {
  const { id } = useParams();
  const tz = useCompanyTz();
  const o = useQuery({ queryKey: ["wf-opp", id], queryFn: () => api.get<Opportunity>(`/overtime/opportunities/${id}`), refetchInterval: 5_000 });
  const [dlg, setDlg] = useState<"extend" | "cancel" | "assign" | null>(null);
  const [deadline, setDeadline] = useState("");
  const inv = ALL_INV;
  const publish = useAction(() => api.post(`/overtime/opportunities/${id}/publish`, {}), { success: "Published to eligible employees", invalidate: inv });
  const renotify = useAction(() => api.post<{ eligible: number; reachable: number }>(`/overtime/opportunities/${id}/renotify`), { success: (r) => `Sent again to ${r.reachable} of ${r.eligible} eligible employee(s)`, invalidate: inv });
  const extend = useAction(() => api.post(`/overtime/opportunities/${id}/extend`, { claim_deadline: fromLocalInput(deadline, tz), renotify: true }), { success: "Deadline extended and employees notified again", invalidate: inv, onSuccess: () => setDlg(null) });
  const cancel = useAction((reason: string) => api.post(`/overtime/opportunities/${id}/cancel`, { reason }), { success: "Cancelled", invalidate: inv, onSuccess: () => setDlg(null) });
  if (o.error) return <ErrorState error={o.error} onRetry={o.refetch} />;
  if (!o.data) return <Skeleton className="h-96" />;
  const d = o.data;
  const manageable = d.can_manage && !["filled", "cancelled"].includes(d.status);
  return (
    <div className="space-y-4">
      <PageHeader breadcrumb={[{ label: "Overtime", to: "/workforce/overtime" }]}
        title={<span className="flex flex-wrap items-center gap-3">{d.shift_name} · {dayLabel(d.date, { weekday: "short", day: "numeric", month: "short" })} <OppStatus status={d.status} /></span>}
        description={`${d.number} · ${d.start}–${d.end} · ${d.team_name ?? d.department_name}${d.work_location_name ? ` · ${d.work_location_name}` : ""}`}
        actions={manageable ? <>
          {d.status === "draft" && <Button variant="primary" loading={publish.isPending} onClick={() => publish.mutate(undefined)}><Megaphone /> Publish</Button>}
          {["open", "partially_filled"].includes(d.status) && <Button loading={renotify.isPending} onClick={() => renotify.mutate(undefined)}><RefreshCw /> Notify again</Button>}
          <Button onClick={() => { setDeadline(toLocalInput(new Date(Math.min(new Date(d.start_at).getTime(), Date.now() + 6 * 3600e3)).toISOString(), tz)); setDlg("extend"); }}><CalendarClock /> Extend deadline</Button>
          <Button variant={d.status === "expired" ? "primary" : "secondary"} onClick={() => setDlg("assign")}><UserPlus /> Assign manually</Button>
          <Button variant="ghost" onClick={() => setDlg("cancel")}>Cancel</Button>
        </> : undefined} />
      {d.status === "expired" && <div className="rounded-lg border border-bad/40 bg-bad-soft/60 px-4 py-3 text-sm text-bad">Nobody claimed this before {dateTime(d.claim_deadline)}. Extend the deadline to notify employees again, or assign someone manually with a reason.</div>}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5">
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div><div className="text-xs text-muted">Positions</div><div className="num text-lg font-semibold">{d.positions_filled}/{d.positions_total}</div></div>
            <div><div className="text-xs text-muted">Paid hours</div><div className="num text-lg font-semibold">{hrs(d.payable_minutes)}</div></div>
            <div><div className="text-xs text-muted">Claim deadline</div><div>{dateTime(d.claim_deadline)}</div></div>
            <div><div className="text-xs text-muted">Source</div><div className="capitalize">{d.source}</div></div>
            {d.required_skills.length > 0 && <div className="col-span-2"><div className="text-xs text-muted">Required skills</div><div className="mt-1 flex flex-wrap gap-1">{d.required_skills.map((s) => <Badge key={s}>{s}</Badge>)}</div></div>}
          </div>
          <div className="mt-5 border-t border-line pt-4">
            <div className="text-xs text-muted">Notified</div>
            {!d.recipients?.length ? <p className="text-sm text-fg-2">{d.status === "draft" ? "Not published yet." : "Nobody was eligible."}</p> :
              <ul className="mt-1 space-y-0.5 text-sm">{d.recipients.map((r) => <li key={r.round}>Round {r.round}: {r.notified} eligible{r.reachable < r.notified ? `, ${r.notified - r.reachable} without a login` : ""}</li>)}</ul>}
          </div>
          <div className="mt-4 border-t border-line pt-4">
            <div className="text-xs text-muted">Working it</div>
            {!d.assignments?.length ? <p className="text-sm text-fg-2">Nobody yet.</p> : d.assignments.map((a) => (
              <div key={a.id} className="mt-1 text-sm"><span className={cn("font-medium", a.status !== "assigned" && "text-muted line-through")}>{a.employee.full_name}</span>
                <span className="text-xs text-muted"> · {a.source === "manual" ? `assigned: ${a.reason}` : "claimed"}{a.status !== "assigned" ? ` · ${a.status}${a.end_reason ? ` (${a.end_reason})` : ""}` : ""}</span></div>))}
          </div>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader title="Claims, in the order the system recorded them" description="Times are server times with milliseconds; the earliest eligible claim won." />
          {!d.claims?.length ? <EmptyState title="No claims yet" description="This list updates every few seconds." /> : (
            <table className="w-full text-sm">
              <thead><tr className="border-b border-line bg-surface-2/60 text-left text-[11px] uppercase tracking-wider text-muted"><th className="px-4 py-2 font-medium">#</th><th className="px-4 py-2 font-medium">Employee</th><th className="px-4 py-2 font-medium">Recorded at</th><th className="px-4 py-2 font-medium">Outcome</th></tr></thead>
              <tbody>{d.claims.map((c, i) => (
                <tr key={c.id} className="border-b border-line last:border-0">
                  <td className="num px-4 py-2 text-muted">{i + 1}</td>
                  <td className="px-4 py-2">{c.employee.full_name}<span className="block text-xs text-muted">{c.employee.employee_code}</span></td>
                  <td className="num px-4 py-2 text-xs">{new Intl.DateTimeFormat("en-IN", { timeZone: tz, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3, hourCycle: "h23" } as Intl.DateTimeFormatOptions).format(new Date(c.claimed_at))}</td>
                  <td className="px-4 py-2"><StatusBadge status={c.outcome} />{c.outcome !== "won" && c.reason && <span className="block text-xs text-muted">{c.reason}</span>}</td>
                </tr>))}</tbody>
            </table>
          )}
        </Card>
      </div>
      {dlg === "extend" && <Dialog open onOpenChange={(v) => !v && setDlg(null)} title="Extend the claim deadline" description="Eligible employees are notified again."
        footer={<><Button onClick={() => setDlg(null)}>Cancel</Button><Button variant="primary" loading={extend.isPending} disabled={!deadline} onClick={() => extend.mutate(undefined)}>Extend</Button></>}>
        <Field label="New deadline" htmlFor="ex-d" hint={`No later than the shift start (${dateTime(d.start_at)})`}><Input id="ex-d" type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} /></Field>
      </Dialog>}
      {dlg === "assign" && <AssignDialog title="Assign manually" url={`/overtime/opportunities/${d.id}/assign`} candidatesUrl={`/overtime/opportunities/${d.id}/candidates`} requireReason onClose={() => setDlg(null)} />}
      <ConfirmDialog open={dlg === "cancel"} onOpenChange={(v) => !v && setDlg(null)} title="Cancel this opportunity?" description="Employees who were notified are told it’s no longer available."
        requireReason tone="danger" confirmLabel="Cancel opportunity" loading={cancel.isPending} onConfirm={(r) => cancel.mutate(r)} />
    </div>
  );
}

export function NewOpportunityButton() {
  return <Link to="/workforce/overtime?tab=vacancies"><Button variant="primary"><Plus /> Offer a vacancy</Button></Link>;
}
