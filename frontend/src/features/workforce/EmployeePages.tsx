import { useQuery } from "@tanstack/react-query";
import { Bell, CalendarDays, CalendarOff, CheckCircle2, ChevronRight, Home, LogOut, Moon, Plane, Sun, Timer, UserRound, Zap } from "lucide-react";
import { useState } from "react";
import { Link, NavLink, Outlet, useNavigate, useParams } from "react-router-dom";
import { ConfirmDialog, Dialog } from "@/components/ui/overlay";
import { EmptyState, ErrorState, StatusBadge } from "@/components/ui/page";
import { Badge, Button, Card, Checkbox, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { useAuth } from "@/hooks/auth";
import { useAction } from "@/hooks/mutation";
import { useTheme } from "@/hooks/theme";
import { api } from "@/lib/api";
import { dateTime, money, relative } from "@/lib/format";
import { cn, newIdempotencyKey } from "@/lib/utils";
import type { Notification, Page } from "@/types/api";
import type { Assignment, AttendanceRecord, LeaveRequest, MeHome, Opportunity, OvertimeCalc } from "@/types/workforce";
import { addDays, dayLabel, fromLocalInput, hrs, OppStatus, OppSummary, Pill, SOURCE_TEXT, toLocalInput, useCompanyTz, useLeaveTypes, useToday } from "./shared";

const TABS = [
  { to: "/me", label: "Home", icon: <Home />, end: true },
  { to: "/me/shifts", label: "Shifts", icon: <CalendarDays /> },
  { to: "/me/overtime", label: "Overtime", icon: <Zap /> },
  { to: "/me/leave", label: "Leave", icon: <Plane /> },
  { to: "/me/record", label: "Me", icon: <UserRound /> },
];

export const useMe = () => useQuery({ queryKey: ["wf-me"], queryFn: () => api.get<MeHome>("/workforce/me"), refetchInterval: 30_000 });

// ------------------------------------------------------------------ shell
function NotificationSheet({ onClose }: { onClose: () => void }) {
  const { data, refetch } = useQuery({ queryKey: ["notifications", "sheet"], queryFn: () => api.get<Page<Notification>>("/notifications", { page_size: 30 }) });
  const navigate = useNavigate();
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Notifications">
      <div className="-mx-5 -my-4 divide-y divide-line">
        {!data?.items.length && <p className="px-5 py-10 text-center text-sm text-muted">You’re all caught up.</p>}
        {data?.items.map((n) => (
          <button key={n.id} className="block w-full px-5 py-3 text-left hover:bg-surface-2/60" onClick={async () => {
            if (!n.read_at) await api.post(`/notifications/${n.id}/read`);
            refetch();
            const link = (n.data as { link?: string } | null)?.link;
            if (link) { onClose(); navigate(link.startsWith("/workforce") ? "/me" : link); }
          }}>
            <div className={cn("text-sm", !n.read_at && "font-semibold")}>{n.title}</div>
            {n.body && <div className="mt-0.5 whitespace-pre-line text-xs text-muted">{n.body}</div>}
            <div className="mt-1 text-[11px] text-muted">{relative(n.created_at)}</div>
          </button>
        ))}
      </div>
    </Dialog>
  );
}

/** Mobile-first layout for employees: a bottom tab bar and nothing they don't need. */
export function EmployeeShell({ demo }: { demo: boolean }) {
  const { user, logout } = useAuth();
  const [theme, toggleTheme] = useTheme();
  const [bell, setBell] = useState(false);
  const unread = useQuery({ queryKey: ["notifications", "unread"], queryFn: () => api.get<Page<Notification>>("/notifications", { unread_only: true, page_size: 1 }), refetchInterval: 30_000 });
  return (
    <div className="flex min-h-full flex-col bg-bg">
      <header className="sticky top-0 z-30 border-b border-line bg-bg/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-2xl items-center gap-2 px-4">
          <Link to="/me" className="flex items-center gap-2">
            <span className="grid size-7 place-items-center rounded-md bg-fg text-[10px] font-bold tracking-wider text-bg">AB</span>
            <span className="leading-tight"><span className="block text-[13px] font-semibold">{user?.full_name.split(" ")[0]}</span>
              <span className="block text-[11px] text-muted">{user?.company?.name}</span></span>
          </Link>
          {demo && <Badge tone="accent">Demo</Badge>}
          <div className="flex-1" />
          <Button variant="ghost" size="icon" className="relative" onClick={() => setBell(true)} aria-label={`Notifications${unread.data?.total ? ` (${unread.data.total} unread)` : ""}`}>
            <Bell />{!!unread.data?.total && <span className="absolute right-1 top-1 grid min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] font-semibold text-white">{unread.data.total}</span>}
          </Button>
          <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>{theme === "dark" ? <Sun /> : <Moon />}</Button>
          <Button variant="ghost" size="icon" onClick={() => logout()} aria-label="Sign out"><LogOut /></Button>
        </div>
      </header>
      <main className="mx-auto w-full max-w-2xl flex-1 px-4 pb-28 pt-4"><Outlet /></main>
      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur" aria-label="Employee navigation">
        <div className="mx-auto grid max-w-2xl grid-cols-5">
          {TABS.map((t) => (
            <NavLink key={t.to} to={t.to} end={t.end} className={({ isActive }) => cn("flex flex-col items-center gap-0.5 py-2 text-[11px] text-muted [&_svg]:size-5",
              isActive && "font-medium text-fg [&_svg]:text-accent")}>{t.icon}{t.label}</NavLink>
          ))}
        </div>
      </nav>
      {bell && <NotificationSheet onClose={() => { setBell(false); unread.refetch(); }} />}
    </div>
  );
}

// ------------------------------------------------------------------ claim
export function ClaimButton({ o, className, size = "md" }: { o: Opportunity; className?: string; size?: "sm" | "md" }) {
  const [key] = useState(newIdempotencyKey);
  const claim = useAction(() => api.post<{ claim: { outcome: string } }>(`/overtime/opportunities/${o.id}/claim`, undefined, key), {
    success: "Your overtime shift has been confirmed.", invalidate: [["wf-me"], ["marketplace"], ["wf-opp", o.id], ["my-schedule"], ["my-ot"], ["notifications"]],
  });
  if (o.claimed_by_me) return <Pill tone="ok" className={className}><CheckCircle2 className="mr-1 size-3.5" />You’re confirmed</Pill>;
  if (o.status === "published") return <Pill className={className}>Opens {relative(o.claims_open_at)}</Pill>;
  if (o.eligible === false) return <Button size={size} disabled className={className} title={o.ineligible_reasons?.join("; ")}>Not eligible</Button>;
  return (
    <Button variant="primary" size={size} className={className} loading={claim.isPending} disabled={claim.isSuccess}
      onClick={(e) => { e.preventDefault(); e.stopPropagation(); claim.mutate(undefined); }}>
      {claim.isSuccess ? "Claimed" : "Claim shift"}
    </Button>
  );
}

function OppCard({ o }: { o: Opportunity }) {
  return (
    <Link to={`/me/overtime/${o.id}`} className="block">
      <Card className={cn("p-4 transition-colors hover:border-fg-2/40", o.claimed_by_me && "border-ok/50")}>
        <div className="flex items-start justify-between gap-3">
          <OppSummary o={o} />
          <ChevronRight className="mt-1 size-4 shrink-0 text-muted" />
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
          <span className="text-xs text-muted">{o.positions_left} of {o.positions_total} open · claim by {dateTime(o.claim_deadline)}</span>
          <ClaimButton o={o} size="sm" />
        </div>
        {o.eligible === false && !o.claimed_by_me && <p className="mt-2 text-xs text-bad">{o.ineligible_reasons?.[0]}</p>}
      </Card>
    </Link>
  );
}

// ------------------------------------------------------------------ home
function ShiftRow({ a, action }: { a: Assignment; action?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 py-2.5">
      <div className="w-14 shrink-0 text-center"><div className="text-[11px] uppercase text-muted">{dayLabel(a.shift_date, { weekday: "short" })}</div>
        <div className="num text-lg font-semibold leading-tight">{dayLabel(a.shift_date, { day: "numeric" })}</div></div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">{a.shift_name} <span className="num font-normal text-fg-2">{a.start}–{a.end}</span></div>
        <div className="text-xs text-muted">{a.department_name} · {SOURCE_TEXT[a.source]}</div>
      </div>
      {action}
    </div>
  );
}

function CheckInCard({ me }: { me: MeHome }) {
  const [key, setKey] = useState(newIdempotencyKey);
  const recs = useQuery({ queryKey: ["my-att-today"], queryFn: () => api.get<AttendanceRecord[]>("/attendance", { mine: true, date_from: addDays(new Date().toISOString().slice(0, 10), -1) }) });
  const current = me.today[0];
  const rec = recs.data?.find((r) => r.assignment_id === current?.id);
  const act = useAction((kind: "check-in" | "check-out") => api.post<AttendanceRecord>(`/attendance/${kind}`, {}, key), {
    success: (r) => (r.check_out_at ? `Checked out at ${r.check_out}` : `Checked in at ${r.check_in}`), invalidate: [["my-att-today"], ["wf-me"]],
    onSuccess: () => setKey(newIdempotencyKey()),
  });
  if (!me.self_check_in || !current) return null;
  return (
    <div className="mt-3 flex items-center justify-between gap-3 rounded-lg bg-surface-2/70 px-3 py-2.5">
      <span className="text-xs text-fg-2">{rec?.check_in_at ? `Checked in ${rec.check_in}${rec.check_out_at ? ` · out ${rec.check_out}` : ""}` : "Not checked in yet"}</span>
      {!rec?.check_out_at && <Button size="sm" variant="primary" loading={act.isPending} onClick={() => act.mutate(rec?.check_in_at ? "check-out" : "check-in")}>
        {rec?.check_in_at ? "Check out" : "Check in"}</Button>}
    </div>
  );
}

export function MeHomePage() {
  const me = useMe();
  if (me.error) return <ErrorState error={me.error} onRetry={me.refetch} />;
  if (!me.data) return <Skeleton className="h-96" />;
  const d = me.data;
  const today = d.today[0];
  const ot = d.overtime_month;
  const att = d.attendance_month;
  return (
    <div className="space-y-5">
      <section>
        <p className="text-xs uppercase tracking-[0.12em] text-muted">Today</p>
        <Card className="mt-2 p-4">
          {today ? <>
            <div className="flex items-baseline justify-between gap-2"><span className="text-lg font-semibold">{today.shift_name}</span><span className="num text-lg">{today.start}–{today.end}</span></div>
            <p className="text-sm text-muted">{today.department_name} · {SOURCE_TEXT[today.source]}</p>
            <CheckInCard me={d} />
          </> : <p className="text-sm text-fg-2">No shift today.{d.upcoming[0] && <> Next: <b>{d.upcoming[0].shift_name}</b> on {dayLabel(d.upcoming[0].shift_date)} at {d.upcoming[0].start}.</>}</p>}
        </Card>
      </section>

      <section>
        <div className="flex items-baseline justify-between"><p className="text-xs uppercase tracking-[0.12em] text-muted">Overtime in your team</p>
          <Link to="/me/overtime" className="text-xs text-accent">See all</Link></div>
        <div className="mt-2 space-y-2.5">
          {d.opportunities.length === 0 ? <Card className="p-4 text-sm text-muted">No open overtime right now. You’ll get a notification when a shift opens up.</Card>
            : d.opportunities.slice(0, 3).map((o) => <OppCard key={o.id} o={o} />)}
        </div>
      </section>

      <section className="grid grid-cols-3 gap-2.5">
        <Card className="p-3"><div className="text-[11px] text-muted">OT approved</div><div className="num mt-0.5 text-lg font-semibold">{hrs(ot.approved_minutes)}</div></Card>
        <Card className="p-3"><div className="text-[11px] text-muted">OT pending</div><div className="num mt-0.5 text-lg font-semibold">{hrs(ot.pending_minutes)}</div></Card>
        <Card className="p-3"><div className="text-[11px] text-muted">{d.show_pay_estimates && ot.estimated_amount !== null ? "Est. OT pay" : "Days present"}</div>
          <div className="num mt-0.5 text-lg font-semibold">{d.show_pay_estimates && ot.estimated_amount !== null ? money(ot.estimated_amount, { compact: true }) : (att.present ?? 0) + (att.late ?? 0)}</div></Card>
      </section>

      <section>
        <p className="text-xs uppercase tracking-[0.12em] text-muted">Coming up</p>
        <Card className="mt-2 divide-y divide-line px-4">
          {d.upcoming.length === 0 ? <p className="py-4 text-sm text-muted">Nothing scheduled in the next two weeks.</p> : d.upcoming.slice(0, 6).map((a) => <ShiftRow key={a.id} a={a} />)}
        </Card>
      </section>

      <section>
        <div className="flex items-baseline justify-between"><p className="text-xs uppercase tracking-[0.12em] text-muted">Leave balance</p><Link to="/me/leave" className="text-xs text-accent">Apply</Link></div>
        <div className="mt-2 flex flex-wrap gap-2">
          {d.leave_balances.filter((b) => !b.unlimited).map((b) => (
            <div key={b.code} className="rounded-lg border border-line bg-surface px-3 py-2"><div className="text-[11px] text-muted">{b.name}</div>
              <div className="num text-sm font-semibold">{Number(b.available ?? 0)} <span className="font-normal text-muted">left</span></div></div>
          ))}
        </div>
        {d.leave_requests.filter((r) => r.status === "pending").map((r) => (
          <p key={r.id} className="mt-2 text-xs text-fg-2"><StatusBadge status={r.status} /> {r.leave_type}, {dayLabel(r.start_date)}{r.end_date !== r.start_date && ` – ${dayLabel(r.end_date)}`}</p>
        ))}
      </section>
    </div>
  );
}

// ------------------------------------------------------------------ marketplace
export function MarketplacePage() {
  const [eligibleOnly, setEligibleOnly] = useState(false);
  const [shift, setShift] = useState("");
  const [from, setFrom] = useState("");
  const list = useQuery({ queryKey: ["marketplace", eligibleOnly, shift, from], refetchInterval: 10_000,
    queryFn: () => api.get<Opportunity[]>("/overtime/marketplace", { eligible_only: eligibleOnly, shift: shift || undefined, date_from: from || undefined }) });
  const mine = useQuery({ queryKey: ["my-ot"], queryFn: () => api.get<Assignment[]>("/overtime/my-assignments", { upcoming_only: true }) });
  const shiftNames = [...new Set((list.data ?? []).map((o) => o.shift_name))];
  return (
    <div className="space-y-4">
      <div><h1 className="text-xl font-semibold tracking-tight">Overtime</h1>
        <p className="text-sm text-muted">Open shifts in your team. The first eligible person to claim a shift gets it.</p></div>
      <div className="flex flex-wrap items-center gap-2">
        <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-40" aria-label="From date" />
        <Select value={shift} onChange={(e) => setShift(e.target.value)} className="w-36" aria-label="Shift"><option value="">All shifts</option>
          {shiftNames.map((n) => <option key={n}>{n}</option>)}</Select>
        <Checkbox label="Only shifts I can take" checked={eligibleOnly} onChange={(e) => setEligibleOnly(e.target.checked)} />
      </div>
      {list.error ? <ErrorState error={list.error} onRetry={list.refetch} /> : list.isLoading ? <Skeleton className="h-48" /> :
        !list.data?.length ? <Card><EmptyState title="No open overtime" description="When a shift opens up you’ll get a notification." icon={<Zap />} /></Card>
          : <div className="space-y-2.5">{list.data.map((o) => <OppCard key={o.id} o={o} />)}</div>}
      {!!mine.data?.length && <section>
        <p className="mt-6 text-xs uppercase tracking-[0.12em] text-muted">My upcoming overtime</p>
        <Card className="mt-2 divide-y divide-line px-4">{mine.data.map((a) => <ShiftRow key={a.id} a={a} action={<WithdrawButton a={a} />} />)}</Card>
      </section>}
    </div>
  );
}

function WithdrawButton({ a }: { a: Assignment }) {
  const [open, setOpen] = useState(false);
  const run = useAction((reason: string) => api.post<{ message: string }>(`/assignments/${a.id}/withdraw`, { reason }), {
    success: (r) => r.message, invalidate: [["my-ot"], ["wf-me"], ["marketplace"], ["my-schedule"]], onSuccess: () => setOpen(false),
  });
  if (!a.can_withdraw) return null;
  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>Withdraw</Button>
      <ConfirmDialog open={open} onOpenChange={setOpen} title={`Withdraw from ${a.shift_name} on ${dayLabel(a.shift_date)}?`} requireReason tone="danger"
        description={a.withdraw_needs_approval ? "It’s less than the cut-off before the shift, so your manager has to approve this. You stay scheduled until they do."
          : "The shift will be offered to your colleagues again."}
        confirmLabel={a.withdraw_needs_approval ? "Ask my manager" : "Withdraw"} loading={run.isPending} onConfirm={(r) => run.mutate(r)} />
    </>
  );
}

export function MeOpportunityPage() {
  const { id } = useParams();
  const o = useQuery({ queryKey: ["wf-opp", id], queryFn: () => api.get<Opportunity>(`/overtime/opportunities/${id}`), refetchInterval: 10_000 });
  if (o.error) return <ErrorState error={o.error} onRetry={o.refetch} />;
  if (!o.data) return <Skeleton className="h-64" />;
  const d = o.data;
  return (
    <div className="space-y-4">
      <Link to="/me/overtime" className="text-sm text-muted">← Overtime</Link>
      <Card className="p-5">
        <div className="mb-3 flex items-center justify-between"><span className="font-mono text-xs text-muted">{d.number}</span><OppStatus status={d.status} /></div>
        <OppSummary o={d} />
        <div className="mt-4 grid grid-cols-2 gap-3 border-t border-line pt-4 text-sm">
          <div><div className="text-xs text-muted">Positions open</div><div className="num font-medium">{d.positions_left} of {d.positions_total}</div></div>
          <div><div className="text-xs text-muted">Claim by</div><div className="font-medium">{dateTime(d.claim_deadline)}</div></div>
        </div>
        {d.eligible === false && !d.claimed_by_me && <div className="mt-4 rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">
          You can’t take this shift: {d.ineligible_reasons?.join("; ")}.</div>}
        {d.notes && <p className="mt-3 text-sm text-fg-2">{d.notes}</p>}
        <div className="mt-5">{["open", "partially_filled", "published"].includes(d.status) || d.claimed_by_me
          ? <ClaimButton o={d} className="w-full justify-center" /> : <p className="text-center text-sm text-muted">This opportunity is {d.status.replace("_", " ")}.</p>}</div>
        <p className="mt-3 text-center text-[11.5px] text-muted">The first eligible claim the system records wins. Final pay is based on your recorded attendance and approval.</p>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ shifts
export function MyShiftsPage() {
  const today = useToday();
  const [weeks, setWeeks] = useState(4);
  const cal = useQuery({ queryKey: ["my-schedule", weeks], queryFn: () => api.get<{ shifts: import("@/types/workforce").Shift[]; leave: import("@/types/workforce").CalendarLeave[] }>(
    "/workforce/me/schedule", { date_from: today, date_to: addDays(today, weeks * 7 - 1) }) });
  const mine = useQuery({ queryKey: ["my-ot"], queryFn: () => api.get<Assignment[]>("/overtime/my-assignments", { upcoming_only: true }) });
  const days = Array.from({ length: weeks * 7 }, (_, i) => addDays(today, i));
  return (
    <div className="space-y-4">
      <div><h1 className="text-xl font-semibold tracking-tight">My shifts</h1><p className="text-sm text-muted">Your schedule, including overtime you’ve taken.</p></div>
      {cal.error ? <ErrorState error={cal.error} onRetry={cal.refetch} /> : !cal.data ? <Skeleton className="h-96" /> : (
        <Card className="divide-y divide-line">
          {days.map((d) => {
            const shifts = cal.data.shifts.filter((s) => s.shift_date === d);
            const leave = cal.data.leave.find((l) => l.start_date <= d && l.end_date >= d);
            if (!shifts.length && !leave) return null;
            return (
              <div key={d} className="flex gap-3 px-4 py-3">
                <div className="w-14 shrink-0"><div className="text-[11px] uppercase text-muted">{dayLabel(d, { weekday: "short" })}</div>
                  <div className={cn("num text-lg font-semibold leading-tight", d === today && "text-accent")}>{dayLabel(d, { day: "numeric", month: "short" })}</div></div>
                <div className="min-w-0 flex-1 space-y-1.5">
                  {shifts.map((s) => {
                    const p = s.positions[0];
                    const away = p && !p.assignment;
                    const a = mine.data?.find((x) => x.shift_id === s.id);
                    return (
                      <div key={s.id} className="flex items-center justify-between gap-2">
                        <div><div className={cn("text-sm font-medium", away && "text-muted line-through")}>{s.name} <span className="num font-normal">{p?.assignment?.start ?? s.start}–{p?.assignment?.end ?? s.end}</span></div>
                          <div className="text-xs text-muted">{s.department_name}{p?.assignment ? ` · ${SOURCE_TEXT[p.assignment.source]}` : " · on leave"}</div></div>
                        {a && <WithdrawButton a={a} />}
                      </div>
                    );
                  })}
                  {leave && !shifts.length && <div className="flex items-center gap-1.5 text-sm text-warn"><CalendarOff className="size-4" /> On leave</div>}
                </div>
              </div>
            );
          })}
        </Card>
      )}
      <Button variant="ghost" className="w-full" onClick={() => setWeeks(weeks + 4)}>Show 4 more weeks</Button>
    </div>
  );
}

// ------------------------------------------------------------------ leave
export function ApplyLeaveDialog({ onClose, employeeId }: { onClose: () => void; employeeId?: string }) {
  const types = useLeaveTypes();
  const today = useToday();
  const [f, setF] = useState({ leave_type_id: "", start_date: today, end_date: today, half_day: "none", reason: "" });
  const [file, setFile] = useState<File | null>(null);
  const type = types.data?.find((t) => t.id === f.leave_type_id);
  const preview = useQuery({ queryKey: ["leave-preview", f.start_date, f.end_date, f.half_day, employeeId], enabled: !!f.start_date && f.end_date >= f.start_date,
    queryFn: () => api.get<{ days: string; affected_shifts: { name: string; date: string; start: string; end: string }[] }>("/leave/preview",
      { start_date: f.start_date, end_date: f.end_date, half_day: f.half_day, employee_id: employeeId }) });
  const submit = useAction(async () => {
    const needsDoc = type?.requires_document;
    const req = await api.post<LeaveRequest>("/leave/requests", { ...f, employee_id: employeeId, reason: f.reason || null, submit: !needsDoc });
    if (needsDoc && file) {
      const fd = new FormData();
      fd.append("file", file);
      await api.upload(`/leave/requests/${req.id}/attachments`, fd);
      return api.post<LeaveRequest>(`/leave/requests/${req.id}/submit`);
    }
    return req;
  }, { success: (r) => r.status === "pending" ? "Leave request sent to your manager" : "Saved as a draft", invalidate: [["my-leave"], ["wf-me"], ["leave-requests"]], onSuccess: onClose });
  const half = f.half_day !== "none";
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Apply for leave" description="Your manager is notified. If your shifts need cover, colleagues are offered them without being told why you’re away."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={submit.isPending}
        disabled={!f.leave_type_id || f.end_date < f.start_date || (type?.requires_document && !file)} onClick={() => submit.mutate(undefined)}>Send request</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Leave type" htmlFor="lv-t" className="sm:col-span-2" required><Select id="lv-t" value={f.leave_type_id} onChange={(e) => setF({ ...f, leave_type_id: e.target.value })}>
          <option value="">Select…</option>{types.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</Select></Field>
        <Field label="From" htmlFor="lv-s"><Input id="lv-s" type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value, end_date: half || e.target.value > f.end_date ? e.target.value : f.end_date })} /></Field>
        <Field label="To" htmlFor="lv-e"><Input id="lv-e" type="date" value={f.end_date} disabled={half} min={f.start_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></Field>
        {type?.allow_half_day !== false && <Field label="Half day" htmlFor="lv-h" className="sm:col-span-2"><Select id="lv-h" value={f.half_day}
          onChange={(e) => setF({ ...f, half_day: e.target.value, end_date: e.target.value !== "none" ? f.start_date : f.end_date })}>
          <option value="none">Full day(s)</option><option value="first_half">First half</option><option value="second_half">Second half</option></Select></Field>}
        <Field label="Reason" htmlFor="lv-r" className="sm:col-span-2" hint="Only your manager and HR can see this."><Textarea id="lv-r" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
        {type?.requires_document && <Field label="Supporting document" htmlFor="lv-f" className="sm:col-span-2" required hint="PDF or image, for example a medical certificate.">
          <Input id="lv-f" type="file" accept=".pdf,.png,.jpg,.jpeg" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>}
      </div>
      {preview.data && <div className="mt-4 rounded-lg bg-surface-2/70 px-3 py-2.5 text-sm">
        <b>{Number(preview.data.days)}</b> working day(s)
        {preview.data.affected_shifts.length > 0 && <span className="text-fg-2"> · shifts affected: {preview.data.affected_shifts.map((s) => `${s.name} ${dayLabel(s.date)}`).join(", ")}</span>}
      </div>}
    </Dialog>
  );
}

export function MyLeavePage() {
  const me = useMe();
  const [apply, setApply] = useState(false);
  const [cancelling, setCancelling] = useState<LeaveRequest | null>(null);
  const list = useQuery({ queryKey: ["my-leave"], queryFn: () => api.get<Page<LeaveRequest>>("/leave/requests", { mine: true, page_size: 50 }) });
  const cancel = useAction(({ r, reason }: { r: LeaveRequest; reason: string }) => api.post<LeaveRequest>(`/leave/requests/${r.id}/cancel`, { reason }),
    { success: "Leave cancelled", invalidate: [["my-leave"], ["wf-me"]], onSuccess: () => setCancelling(null) });
  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between gap-3">
        <div><h1 className="text-xl font-semibold tracking-tight">Leave</h1><p className="text-sm text-muted">Balances for {new Date().getFullYear()} and your requests.</p></div>
        <Button variant="primary" onClick={() => setApply(true)}>Apply</Button>
      </div>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
        {me.data?.leave_balances.map((b) => (
          <Card key={b.code} className="p-3"><div className="text-[11px] text-muted">{b.name}</div>
            <div className="num mt-0.5 text-lg font-semibold">{b.unlimited ? "—" : Number(b.available ?? 0)}</div>
            <div className="text-[11px] text-muted">{b.unlimited ? "No limit" : `of ${Number(b.entitled) + Number(b.carried_forward) + Number(b.adjusted)} · ${Number(b.used)} used${Number(b.pending) ? ` · ${Number(b.pending)} pending` : ""}`}</div></Card>
        ))}
      </div>
      <Card className="divide-y divide-line">
        {!list.data?.items.length && <p className="p-4 text-sm text-muted">No leave requests yet.</p>}
        {list.data?.items.map((r) => (
          <div key={r.id} className="flex items-start justify-between gap-3 px-4 py-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2 text-sm font-medium">{r.leave_type} <StatusBadge status={r.status} /></div>
              <div className="text-xs text-muted">{dayLabel(r.start_date)}{r.end_date !== r.start_date && ` – ${dayLabel(r.end_date)}`}{r.half_day !== "none" && ` (${r.half_day.replace("_", " ")})`} · {Number(r.days)} day(s) · {r.number}</div>
              {r.decision_note && <div className="mt-1 text-xs text-fg-2">Manager: {r.decision_note}</div>}
            </div>
            {r.can_cancel && <Button size="sm" variant="ghost" onClick={() => setCancelling(r)}>Cancel</Button>}
          </div>
        ))}
      </Card>
      {apply && <ApplyLeaveDialog onClose={() => setApply(false)} />}
      <ConfirmDialog open={!!cancelling} onOpenChange={(o) => !o && setCancelling(null)} title={`Cancel ${cancelling?.number}?`} requireReason tone="danger"
        description={cancelling?.status === "approved" ? "Your shifts come back to your schedule. If a colleague already covers one, your manager decides who works it." : undefined}
        confirmLabel="Cancel leave" loading={cancel.isPending} onConfirm={(reason) => cancelling && cancel.mutate({ r: cancelling, reason })} />
    </div>
  );
}

// ------------------------------------------------------------------ my record
function CorrectionDialog({ rec, onClose }: { rec: AttendanceRecord; onClose: () => void }) {
  const tz = useCompanyTz();
  const [f, setF] = useState({ check_in: toLocalInput(rec.check_in_at ?? rec.scheduled_start, tz), check_out: toLocalInput(rec.check_out_at ?? rec.scheduled_end, tz), reason: "" });
  const run = useAction(() => api.post("/attendance/corrections", { assignment_id: rec.assignment_id, check_in_at: fromLocalInput(f.check_in, tz),
    check_out_at: fromLocalInput(f.check_out, tz), reason: f.reason }), { success: "Correction sent to your manager", invalidate: [["my-att"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Correct ${rec.shift_name} on ${dayLabel(rec.work_date)}`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={run.isPending} disabled={f.reason.length < 3} onClick={() => run.mutate(undefined)}>Send</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Check-in" htmlFor="c-in"><Input id="c-in" type="datetime-local" value={f.check_in} onChange={(e) => setF({ ...f, check_in: e.target.value })} /></Field>
        <Field label="Check-out" htmlFor="c-out"><Input id="c-out" type="datetime-local" value={f.check_out} onChange={(e) => setF({ ...f, check_out: e.target.value })} /></Field>
        <Field label="What happened?" htmlFor="c-r" className="sm:col-span-2" required><Textarea id="c-r" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
      </div>
    </Dialog>
  );
}

export function MeRecordPage() {
  const today = useToday();
  const [correcting, setCorrecting] = useState<AttendanceRecord | null>(null);
  const att = useQuery({ queryKey: ["my-att"], queryFn: () => api.get<AttendanceRecord[]>("/attendance", { mine: true, date_from: addDays(today, -30), date_to: today }) });
  const ot = useQuery({ queryKey: ["my-calc"], queryFn: () => api.get<OvertimeCalc[]>("/overtime/calculations", { mine: true, date_from: addDays(today, -60), date_to: today }) });
  const corr = useQuery({ queryKey: ["my-corr"], queryFn: () => api.get<{ id: string; status: string; shift: { name: string; date: string } | null; reason: string }[]>("/attendance/corrections", { mine: true, status: "" }) });
  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between"><div><h1 className="text-xl font-semibold tracking-tight">My record</h1><p className="text-sm text-muted">Attendance and overtime from the last month.</p></div>
        <Link to="/me/account" className="text-sm text-accent">Account & notifications</Link></div>
      <section>
        <p className="text-xs uppercase tracking-[0.12em] text-muted">Overtime</p>
        <Card className="mt-2 divide-y divide-line">
          {!ot.data?.length && <p className="p-4 text-sm text-muted">No overtime recorded. Overtime is worked out from your attendance, not from the schedule.</p>}
          {ot.data?.map((c) => (
            <div key={c.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
              <div><div className="text-sm font-medium">{dayLabel(c.work_date)} · {hrs(c.payable_minutes)}{c.is_correction && " (correction)"}</div>
                <div className="text-xs text-muted">Worked {hrs(c.worked_minutes)} · {c.day_type.replace("_", " ")}{c.amount !== undefined && ` · ${money(c.approved_amount ?? c.amount)}`}</div></div>
              <StatusBadge status={c.status} />
            </div>
          ))}
        </Card>
      </section>
      <section>
        <p className="text-xs uppercase tracking-[0.12em] text-muted">Attendance</p>
        <Card className="mt-2 divide-y divide-line">
          {!att.data?.length && <p className="p-4 text-sm text-muted">No attendance recorded in the last 30 days.</p>}
          {att.data?.map((r) => (
            <div key={r.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
              <div className="min-w-0"><div className="text-sm font-medium">{dayLabel(r.work_date)} · {r.shift_name}</div>
                <div className="num text-xs text-muted">In {r.check_in} · out {r.check_out} · worked {hrs(r.worked_minutes)}{r.late_minutes > 0 && ` · ${r.late_minutes} min late`}</div></div>
              <div className="flex items-center gap-2"><StatusBadge status={r.status} />
                {r.assignment_id && <Button size="sm" variant="ghost" onClick={() => setCorrecting(r)} aria-label="Request correction"><Timer /></Button>}</div>
            </div>
          ))}
        </Card>
        {!!corr.data?.length && <div className="mt-3 space-y-1">{corr.data.slice(0, 5).map((c) => (
          <p key={c.id} className="text-xs text-fg-2"><StatusBadge status={c.status} /> Correction for {c.shift?.name} {c.shift && dayLabel(c.shift.date)}</p>))}</div>}
      </section>
      {correcting && <CorrectionDialog rec={correcting} onClose={() => setCorrecting(null)} />}
    </div>
  );
}
