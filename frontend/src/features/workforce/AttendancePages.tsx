import { useQuery } from "@tanstack/react-query";
import { CheckCheck, ClipboardList, FileUp, RefreshCw, Timer } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { DataTable } from "@/components/ui/data-table";
import { ConfirmDialog, Dialog, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/overlay";
import { EmptyState, ErrorState, PageHeader, StatusBadge } from "@/components/ui/page";
import { Button, Card, Field, Input, Select, Skeleton } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AttendanceRecord, Correction, DaySheetRow, OvertimeCalc } from "@/types/workforce";
import { addDays, dayLabel, DepartmentSelect, fromLocalInput, hrs, toLocalInput, useCompanyTz, useToday } from "./shared";

const LIVE: Record<string, { text: string; tone: string }> = {
  scheduled: { text: "Scheduled", tone: "text-muted" }, working: { text: "Working", tone: "text-accent" }, not_checked_in: { text: "Not checked in", tone: "text-bad" },
  on_leave: { text: "On leave", tone: "text-warn" },
};

function RecordDialog({ row, onClose }: { row: DaySheetRow; onClose: () => void }) {
  const tz = useCompanyTz();
  const r = row.record;
  const [f, setF] = useState({ in: toLocalInput(r?.check_in_at ?? row.start_at, tz), out: toLocalInput(r?.check_out_at ?? (r ? null : row.end_at), tz),
    brk: String(r?.break_minutes ?? ""), status: "", notes: r?.notes ?? "" });
  const run = useAction(() => api.post<AttendanceRecord>("/attendance/record", {
    assignment_id: row.assignment_id, check_in_at: fromLocalInput(f.in, tz), check_out_at: fromLocalInput(f.out, tz),
    ...(f.brk !== "" ? { break_minutes: Number(f.brk) } : {}), status: f.status || null, notes: f.notes || null }),
  { success: (x) => `Saved: ${x.status.replace("_", " ")}, worked ${hrs(x.worked_minutes)}`, invalidate: [["wf-daysheet"], ["wf-att"], ["wf-calc"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`${row.employee.full_name} · ${row.shift_name} ${row.scheduled}`}
      description={r ? "Changing a saved record keeps the previous values in the correction history." : "Record actual times. Overtime is recalculated from them."}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={run.isPending} onClick={() => run.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Check-in" htmlFor="r-in"><Input id="r-in" type="datetime-local" value={f.in} onChange={(e) => setF({ ...f, in: e.target.value })} /></Field>
        <Field label="Check-out" htmlFor="r-out"><Input id="r-out" type="datetime-local" value={f.out} onChange={(e) => setF({ ...f, out: e.target.value })} /></Field>
        <Field label="Break (minutes)" htmlFor="r-b" hint="Leave blank for the shift's break"><Input id="r-b" type="number" min={0} value={f.brk} onChange={(e) => setF({ ...f, brk: e.target.value })} /></Field>
        <Field label="Status" htmlFor="r-s" hint="Leave on automatic unless you need to override"><Select id="r-s" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
          <option value="">Automatic</option>{["present", "late", "half_day", "absent", "on_leave", "holiday", "weekly_off"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}</Select></Field>
        <Field label="Notes" htmlFor="r-n" className="sm:col-span-2"><Input id="r-n" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      </div>
    </Dialog>
  );
}

function DaySheet() {
  const today = useToday();
  const [day, setDay] = useState(today);
  const [dept, setDept] = useState("");
  const [editing, setEditing] = useState<DaySheetRow | null>(null);
  const rows = useQuery({ queryKey: ["wf-daysheet", day, dept], refetchInterval: 30_000, queryFn: () => api.get<DaySheetRow[]>("/attendance/day-sheet", { day, department_id: dept || undefined }) });
  const counts = (rows.data ?? []).reduce<Record<string, number>>((a, r) => ({ ...a, [r.live_status]: (a[r.live_status] ?? 0) + 1 }), {});
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
        <Input type="date" value={day} onChange={(e) => setDay(e.target.value)} className="w-40" aria-label="Day" />
        <DepartmentSelect value={dept} onChange={setDept} />
        <div className="flex-1" />
        <span className="text-xs text-muted">{Object.entries(counts).map(([k, n]) => `${n} ${(LIVE[k]?.text ?? k.replace("_", " ")).toLowerCase()}`).join(" · ")}</span>
      </div>
      {rows.error ? <ErrorState error={rows.error} onRetry={rows.refetch} /> : !rows.data ? <Skeleton className="m-4 h-40" /> :
        !rows.data.length ? <EmptyState title="Nobody scheduled" icon={<ClipboardList />} /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead><tr className="border-b border-line bg-surface-2/60 text-left text-[11px] uppercase tracking-wider text-muted">
                {["Employee", "Shift", "In", "Out", "Worked", "Status", ""].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr></thead>
              <tbody>{rows.data.map((r) => (
                <tr key={r.assignment_id} className="border-b border-line last:border-0">
                  <td className="px-4 py-2"><div className="font-medium">{r.employee.full_name}</div><div className="text-xs text-muted">{r.employee.employee_code}{r.source !== "schedule" && " · overtime"}</div></td>
                  <td className="px-4 py-2">{r.shift_name}<div className="num text-xs text-muted">{r.scheduled}</div></td>
                  <td className="num px-4 py-2">{r.record?.check_in ?? "—"}</td>
                  <td className="num px-4 py-2">{r.record?.check_out ?? "—"}</td>
                  <td className="num px-4 py-2">{r.record ? hrs(r.record.worked_minutes) : "—"}{r.record?.late_minutes ? <span className="block text-xs text-warn">{r.record.late_minutes} min late</span> : null}</td>
                  <td className="px-4 py-2">{LIVE[r.live_status] ? <span className={cn("text-xs font-medium", LIVE[r.live_status]!.tone)}>{LIVE[r.live_status]!.text}</span> : <StatusBadge status={r.live_status} />}</td>
                  <td className="px-4 py-2 text-right">{r.live_status !== "on_leave" && <Can permission="workforce.attendance.manage"><Button size="sm" variant="ghost" onClick={() => setEditing(r)}><Timer /> Record</Button></Can>}</td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
      {editing && <RecordDialog row={editing} onClose={() => setEditing(null)} />}
    </Card>
  );
}

function Corrections() {
  const [status, setStatus] = useState("pending");
  const [rejecting, setRejecting] = useState<Correction | null>(null);
  const list = useQuery({ queryKey: ["wf-corr", status], queryFn: () => api.get<Correction[]>("/attendance/corrections", { status }) });
  const decide = useAction(({ c, ok, note }: { c: Correction; ok: boolean; note?: string }) => api.post(`/attendance/corrections/${c.id}/${ok ? "approve" : "reject"}`, ok ? {} : { note }),
    { success: "Decision saved; the employee was notified", invalidate: [["wf-corr"], ["wf-daysheet"], ["wf-calc"]], onSuccess: () => setRejecting(null) });
  const fmt = (v: unknown) => (typeof v === "string" && v.includes("T") ? new Date(v).toTimeString().slice(0, 5) : v === null || v === undefined ? "—" : String(v));
  return (
    <Card>
      <div className="border-b border-line p-3"><Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-40" aria-label="Status">
        <option value="pending">Pending</option><option value="approved">Approved</option><option value="rejected">Rejected</option></Select></div>
      {!list.data ? <Skeleton className="m-4 h-32" /> : !list.data.length ? <EmptyState title="No corrections" icon={<CheckCheck />} /> : (
        <div className="divide-y divide-line">{list.data.map((c) => (
          <div key={c.id} className="flex flex-wrap items-start justify-between gap-3 px-5 py-3">
            <div className="min-w-0 text-sm">
              <div className="font-medium">{c.employee.full_name} · {c.shift?.name} {c.shift && dayLabel(c.shift.date)} <span className="num font-normal text-muted">{c.shift?.start}–{c.shift?.end}</span></div>
              <div className="text-xs text-fg-2">Now: in {fmt(c.before?.check_in_at)}, out {fmt(c.before?.check_out_at)} → proposed: in {fmt(c.check_in_at ?? c.before?.check_in_at)}, out {fmt(c.check_out_at ?? c.before?.check_out_at)}{c.status_override && `, status ${c.status_override}`}</div>
              <div className="mt-0.5 text-xs text-muted">“{c.reason}”{c.decision_note && ` · ${c.decision_note}`}</div>
            </div>
            {c.status === "pending" ? <Can permission="workforce.attendance.manage"><div className="flex gap-2">
              <Button size="sm" onClick={() => setRejecting(c)}>Reject</Button>
              <Button size="sm" variant="primary" loading={decide.isPending} onClick={() => decide.mutate({ c, ok: true })}>Approve</Button></div></Can> : <StatusBadge status={c.status} />}
          </div>))}</div>
      )}
      <ConfirmDialog open={!!rejecting} onOpenChange={(o) => !o && setRejecting(null)} title="Reject correction" requireReason tone="danger" confirmLabel="Reject"
        loading={decide.isPending} onConfirm={(note) => rejecting && decide.mutate({ c: rejecting, ok: false, note })} />
    </Card>
  );
}

function Records() {
  const today = useToday();
  const [from, setFrom] = useState(addDays(today, -6));
  const [to, setTo] = useState(today);
  const [status, setStatus] = useState("");
  const list = useQuery({ queryKey: ["wf-att", from, to, status], queryFn: () => api.get<AttendanceRecord[]>("/attendance", { date_from: from, date_to: to, status: status || undefined }) });
  return (
    <Card>
      <div className="flex flex-wrap gap-2 border-b border-line p-3">
        <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-40" aria-label="From" />
        <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-40" aria-label="To" />
        <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-40" aria-label="Status"><option value="">All statuses</option>
          {["present", "late", "half_day", "absent", "on_leave"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}</Select>
      </div>
      <DataTable rows={list.data} loading={list.isLoading} error={list.error} rowKey={(r) => r.id} empty={<EmptyState title="No records" />}
        columns={[
          { key: "e", header: "Employee", cell: (r) => <div><div className="font-medium">{r.employee.full_name}</div><div className="text-xs text-muted">{r.employee.employee_code}</div></div> },
          { key: "d", header: "Date", cell: (r) => dayLabel(r.work_date) },
          { key: "s", header: "Shift", hideBelow: "md", cell: (r) => <span>{r.shift_name}<span className="num block text-xs text-muted">{r.scheduled}</span></span> },
          { key: "io", header: "In – out", cell: (r) => <span className="num">{r.check_in} – {r.check_out}</span> },
          { key: "w", header: "Worked", align: "right", cell: (r) => <span className="num">{hrs(r.worked_minutes)}</span> },
          { key: "st", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
          { key: "src", header: "Source", hideBelow: "lg", cell: (r) => <span className="text-xs text-muted">{r.source}</span> },
        ]} />
    </Card>
  );
}

function ImportDialog({ onClose }: { onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [res, setRes] = useState<{ imported: number; errors: number; rows: { line: number; employee_code: string; status: string; error?: string }[] } | null>(null);
  const run = useAction(() => { const fd = new FormData(); fd.append("file", file!); return api.upload<NonNullable<typeof res>>("/attendance/import", fd); },
    { invalidate: [["wf-daysheet"], ["wf-att"], ["wf-calc"]], onSuccess: setRes });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Import attendance from CSV" description="For exports from biometric or RFID terminals. Columns: employee_code, date (YYYY-MM-DD), check_in (HH:MM), check_out (HH:MM), break_minutes (optional)."
      footer={res ? <Button variant="primary" onClick={onClose}>Done</Button> : <><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!file} loading={run.isPending} onClick={() => run.mutate(undefined)}>Import</Button></>}>
      {!res ? <Field label="CSV file" htmlFor="imp-f"><Input id="imp-f" type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field> : (
        <div className="space-y-2 text-sm"><p><b>{res.imported}</b> imported, <b>{res.errors}</b> with errors.</p>
          {res.errors > 0 && <ul className="max-h-48 overflow-y-auto text-xs text-bad">{res.rows.filter((r) => r.status === "error").map((r) => <li key={r.line}>Line {r.line} ({r.employee_code}): {r.error}</li>)}</ul>}</div>
      )}
    </Dialog>
  );
}

export function AttendancePage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "day";
  const [importing, setImporting] = useState(false);
  return (
    <div className="space-y-4">
      <PageHeader title="Attendance" description="Record who came in. Overtime is calculated only from these records."
        actions={<Can permission="workforce.attendance.manage"><Button onClick={() => setImporting(true)}><FileUp /> Import CSV</Button></Can>} />
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v })}>
        <TabsList><TabsTrigger value="day">Day sheet</TabsTrigger><TabsTrigger value="corrections">Corrections</TabsTrigger><TabsTrigger value="records">Records</TabsTrigger></TabsList>
        <TabsContent value="day" className="mt-4"><DaySheet /></TabsContent>
        <TabsContent value="corrections" className="mt-4"><Corrections /></TabsContent>
        <TabsContent value="records" className="mt-4"><Records /></TabsContent>
      </Tabs>
      {importing && <ImportDialog onClose={() => setImporting(false)} />}
    </div>
  );
}

// ------------------------------------------------------------------ overtime records
export function OvertimeRecordsPage() {
  const today = useToday();
  const { can } = useAuth();
  const [from, setFrom] = useState(`${today.slice(0, 8)}01`);
  const [to, setTo] = useState(today);
  const [status, setStatus] = useState("calculated");
  const [dept, setDept] = useState("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [adjusting, setAdjusting] = useState<OvertimeCalc | null>(null);
  const [rejecting, setRejecting] = useState<OvertimeCalc | null>(null);
  const [mins, setMins] = useState("");
  const list = useQuery({ queryKey: ["wf-calc", from, to, status, dept], queryFn: () => api.get<OvertimeCalc[]>("/overtime/calculations", { date_from: from, date_to: to, status: status || undefined, department_id: dept || undefined }) });
  const inv = [["wf-calc"], ["wf-dash"]];
  const approve = useAction(({ c, minutes }: { c: OvertimeCalc; minutes?: number }) => api.post(`/overtime/calculations/${c.id}/approve`, minutes !== undefined ? { approved_minutes: minutes } : {}),
    { success: "Approved", invalidate: inv, onSuccess: () => setAdjusting(null) });
  const reject = useAction(({ c, note }: { c: OvertimeCalc; note: string }) => api.post(`/overtime/calculations/${c.id}/reject`, { note }), { success: "Rejected", invalidate: inv, onSuccess: () => setRejecting(null) });
  const bulk = useAction(() => api.post<{ approved: number; skipped: unknown[] }>("/overtime/calculations/bulk-approve", { ids: [...sel] }),
    { success: (r) => `${r.approved} approved${r.skipped.length ? `, ${r.skipped.length} skipped` : ""}`, invalidate: inv, onSuccess: () => setSel(new Set()) });
  const recalc = useAction(() => api.post<{ employees: number; rows_changed: number }>("/overtime/calculations/recalculate", { date_from: from, date_to: to, department_id: dept || null }),
    { success: (r) => `Recalculated ${r.employees} employee(s); ${r.rows_changed} record(s) changed`, invalidate: inv });
  const rows = list.data ?? [];
  const decidable = rows.filter((r) => r.can_decide);
  const money_ = rows.some((r) => r.amount !== undefined);
  return (
    <div className="space-y-4">
      <PageHeader title="Overtime records" description="Worked out from recorded attendance against the overtime policy. Approve before payroll export; exported records are locked."
        actions={<Can permission="workforce.overtime.approve"><Button loading={recalc.isPending} onClick={() => recalc.mutate(undefined)}><RefreshCw /> Recalculate</Button>
          {sel.size > 0 && <Button variant="primary" loading={bulk.isPending} onClick={() => bulk.mutate(undefined)}><CheckCheck /> Approve {sel.size}</Button>}</Can>} />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-40" aria-label="From" />
          <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-40" aria-label="To" />
          <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-44" aria-label="Status"><option value="calculated">Awaiting approval</option>
            <option value="approved">Approved</option><option value="exported">Exported</option><option value="rejected">Rejected</option><option value="">All</option></Select>
          <DepartmentSelect value={dept} onChange={setDept} />
        </div>
        {list.error ? <ErrorState error={list.error} onRetry={list.refetch} /> : !list.data ? <Skeleton className="m-4 h-40" /> : !rows.length ? <EmptyState title="No overtime records" description="Nothing in this period matches." /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-sm">
              <thead><tr className="border-b border-line bg-surface-2/60 text-left text-[11px] uppercase tracking-wider text-muted">
                <th className="w-8 px-3 py-2">{decidable.length > 0 && <input type="checkbox" aria-label="Select all" checked={sel.size === decidable.length}
                  onChange={(e) => setSel(e.target.checked ? new Set(decidable.map((r) => r.id)) : new Set())} />}</th>
                {["Employee", "Date", "Worked", "Regular", "Overtime", "Breakdown", money_ ? "Amount" : "", "Status", ""].map((h, i) => <th key={i} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
              <tbody>{rows.map((c) => (
                <tr key={c.id} className="border-b border-line last:border-0">
                  <td className="px-3 py-2">{c.can_decide && <input type="checkbox" aria-label="Select" checked={sel.has(c.id)} onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(c.id) : n.delete(c.id); setSel(n); }} />}</td>
                  <td className="px-3 py-2"><div className="font-medium">{c.employee.full_name}</div><div className="text-xs text-muted">{c.employee.employee_code}</div></td>
                  <td className="px-3 py-2">{dayLabel(c.work_date)}{c.day_type !== "regular" && <div className="text-xs text-warn">{c.day_type.replace("_", " ")}</div>}{c.is_correction && <div className="text-xs text-info">correction</div>}</td>
                  <td className="num px-3 py-2">{hrs(c.worked_minutes)}</td>
                  <td className="num px-3 py-2">{hrs(c.regular_minutes)}</td>
                  <td className="num px-3 py-2 font-medium">{hrs(c.payable_minutes)}{c.approved_minutes !== null && c.approved_minutes !== c.payable_minutes && <div className="text-xs text-muted">approved {hrs(c.approved_minutes)}</div>}</td>
                  <td className="px-3 py-2 text-xs text-muted">{[c.ot_daily_minutes && `daily ${hrs(c.ot_daily_minutes)}`, c.ot_weekly_minutes && `weekly ${hrs(c.ot_weekly_minutes)}`,
                    c.ot_weekly_off_minutes && `off-day ${hrs(c.ot_weekly_off_minutes)}`, c.ot_holiday_minutes && `holiday ${hrs(c.ot_holiday_minutes)}`, c.night_minutes && `night ${hrs(c.night_minutes)}`].filter(Boolean).join(" · ")} · ×{Number(c.multiplier)}</td>
                  {money_ && <td className="num px-3 py-2">{money(c.approved_amount ?? c.amount)}</td>}
                  <td className="px-3 py-2"><StatusBadge status={c.status} /></td>
                  <td className="px-3 py-2 text-right">{c.can_decide && can("workforce.overtime.approve") && <div className="flex justify-end gap-1">
                    <Button size="sm" variant="ghost" onClick={() => setRejecting(c)}>Reject</Button>
                    <Button size="sm" variant="ghost" onClick={() => { setAdjusting(c); setMins(String(c.payable_minutes)); }}>Adjust</Button>
                    <Button size="sm" variant="primary" onClick={() => approve.mutate({ c })}>Approve</Button></div>}</td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
      </Card>
      {adjusting && <Dialog open onOpenChange={(o) => !o && setAdjusting(null)} title={`Approve fewer minutes · ${adjusting.employee.full_name}`}
        footer={<><Button onClick={() => setAdjusting(null)}>Cancel</Button><Button variant="primary" loading={approve.isPending} onClick={() => approve.mutate({ c: adjusting, minutes: Number(mins) })}>Approve</Button></>}>
        <Field label="Approved overtime (minutes)" htmlFor="adj" hint={`Calculated: ${adjusting.payable_minutes} min`}><Input id="adj" type="number" min={0} max={adjusting.payable_minutes} value={mins} onChange={(e) => setMins(e.target.value)} /></Field>
      </Dialog>}
      <ConfirmDialog open={!!rejecting} onOpenChange={(o) => !o && setRejecting(null)} title="Reject overtime" requireReason tone="danger" confirmLabel="Reject"
        loading={reject.isPending} onConfirm={(note) => rejecting && reject.mutate({ c: rejecting, note })} />
      <p className="text-xs text-muted">Overtime is never assumed from the schedule. Only hours recorded in attendance above the policy thresholds count.</p>
    </div>
  );
}
