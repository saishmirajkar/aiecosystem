import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, Download, FileSpreadsheet, KeyRound, Link2, Pencil, Plus, Trash2, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { DataTable } from "@/components/ui/data-table";
import { Dialog, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/overlay";
import { DetailGrid, EmptyState, ErrorState, PageHeader, SearchInput, StatusBadge } from "@/components/ui/page";
import { Badge, Button, Card, CardHeader, Checkbox, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useDebounced, useDepartments, useUsers } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { dateTime, money } from "@/lib/format";
import { newIdempotencyKey } from "@/lib/utils";
import type { Page } from "@/types/api";
import type { Channel, Employee, Holiday, LeaveType, OvertimePolicy, PayrollBatch, Ref, ShiftPattern, ShiftTemplate, Team, WorkforcePolicy } from "@/types/workforce";
import { ApplyLeaveDialog } from "./EmployeePages";
import { addDays, dayLabel, DepartmentSelect, hrs, useRefs, useTeams, useTemplates, useToday } from "./shared";

// ------------------------------------------------------------------ employees
export function EmployeesPage() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [dept, setDept] = useState("");
  const [status, setStatus] = useState("active");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const dq = useDebounced(q);
  const list = useQuery({ queryKey: ["employees", dq, dept, status, page], placeholderData: (p) => p,
    queryFn: () => api.get<Page<Employee>>("/employees", { q: dq, department_id: dept || undefined, status: status || undefined, page, page_size: 25 }) });
  return (
    <div>
      <PageHeader title="Employees" description="People records, teams, skills and overtime eligibility."
        actions={<Can permission="workforce.employees.manage"><Button variant="primary" onClick={() => setCreating(true)}><UserPlus /> New employee</Button></Can>} />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <SearchInput value={q} onChange={(v) => { setQ(v); setPage(1); }} placeholder="Name or employee code…" className="min-w-56 flex-1" />
          <DepartmentSelect value={dept} onChange={(v) => { setDept(v); setPage(1); }} />
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="w-40" aria-label="Status">
            <option value="active">Active</option><option value="on_leave,suspended">On leave / suspended</option><option value="inactive,terminated">Inactive / left</option><option value="">All</option></Select>
        </div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(e) => e.id}
          onRowClick={(e) => navigate(`/workforce/employees/${e.id}`)} page={page} pageSize={25} total={list.data?.total} onPage={setPage}
          empty={<EmptyState title="No employees" icon={<Users />} />}
          columns={[
            { key: "c", header: "Code", cell: (e) => <span className="font-mono text-xs">{e.employee_code}</span> },
            { key: "n", header: "Name", cell: (e) => <div><div className="font-medium">{e.full_name}</div><div className="text-xs text-muted">{e.designation ?? "—"}</div></div> },
            { key: "d", header: "Department · team", hideBelow: "md", cell: (e) => <span className="text-sm">{e.department_name}{e.teams.length ? <span className="block text-xs text-muted">{e.teams.map((t) => t.name).join(", ")}</span> : null}</span> },
            { key: "s", header: "Skills", hideBelow: "lg", cell: (e) => <div className="flex flex-wrap gap-1">{e.skills.slice(0, 3).map((k) => <Badge key={k.skill_id}>{k.name}</Badge>)}</div> },
            { key: "o", header: "Overtime", hideBelow: "md", cell: (e) => e.overtime_eligible ? <Badge tone="ok">Eligible</Badge> : <Badge>No</Badge> },
            { key: "l", header: "Login", hideBelow: "lg", cell: (e) => e.has_login ? <BadgeCheck className="size-4 text-ok" /> : <span className="text-xs text-muted">—</span> },
            { key: "st", header: "Status", cell: (e) => <StatusBadge status={e.status} /> },
          ]} />
      </Card>
      {creating && <EmployeeDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function EmployeeDialog({ employee, onClose }: { employee?: Employee; onClose: () => void }) {
  const navigate = useNavigate();
  const { can } = useAuth();
  const teams = useTeams();
  const skills = useRefs("skills");
  const designations = useRefs("designations");
  const locations = useRefs("locations");
  const patterns = useQuery({ queryKey: ["patterns"], queryFn: () => api.get<ShiftPattern[]>("/shift-patterns") });
  const managers = useQuery({ queryKey: ["employees", "all-brief"], queryFn: () => api.get<Page<Employee>>("/employees", { page_size: 200 }) });
  const e = employee;
  const [f, setF] = useState<Record<string, string | boolean>>({
    employee_code: e?.employee_code ?? "", first_name: e?.first_name ?? "", last_name: e?.last_name ?? "", email: e?.email ?? "", phone: e?.phone ?? "",
    department_id: e?.department_id ?? "", designation_id: e?.designation_id ?? "", manager_id: e?.manager_id ?? "", status: e?.status ?? "active",
    employment_type: e?.employment_type ?? "permanent", date_of_joining: e?.date_of_joining ?? "", work_location_id: e?.work_location_id ?? "",
    shift_pattern_id: e?.shift_pattern_id ?? "", pattern_anchor_date: e?.pattern_anchor_date ?? "", overtime_eligible: e?.overtime_eligible ?? true,
    weekly_hour_limit: e?.weekly_hour_limit ?? "", base_hourly_rate: e?.base_hourly_rate ?? "", emergency_contact_name: e?.emergency_contact_name ?? "",
    emergency_contact_phone: e?.emergency_contact_phone ?? "", address: e?.address ?? "", notes: e?.notes ?? "",
  });
  const [teamIds, setTeamIds] = useState<string[]>(e?.teams.map((t) => t.id) ?? []);
  const [skillIds, setSkillIds] = useState<string[]>(e?.skills.map((k) => k.skill_id) ?? []);
  const v = (k: string) => String(f[k] ?? "");
  const set = (k: string) => (ev: { target: { value: string } }) => setF({ ...f, [k]: ev.target.value });
  const save = useAction(() => {
    const body: Record<string, unknown> = {};
    for (const [k, val] of Object.entries(f)) body[k] = typeof val === "string" ? (val === "" ? null : val) : val;
    body.team_ids = teamIds;
    body.skills = skillIds.map((id) => ({ skill_id: id, level: e?.skills.find((k) => k.skill_id === id)?.level ?? 1, valid_until: e?.skills.find((k) => k.skill_id === id)?.valid_until ?? null }));
    if (!can("workforce.payroll")) delete body.base_hourly_rate;
    if (e) { delete body.employee_code; return api.patch<Employee>(`/employees/${e.id}`, body); }
    return api.post<Employee>("/employees", body);
  }, { success: (x) => `${x.full_name} saved`, invalidate: [["employees"], ["employee"]], onSuccess: (x) => { onClose(); if (!e) navigate(`/workforce/employees/${x.id}`); } });
  const deptTeams = teams.data?.filter((t) => !f.department_id || t.department_id === f.department_id) ?? [];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="xl" title={e ? `Edit ${e.full_name}` : "New employee"}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!v("first_name") || !v("department_id") || (!e && !v("employee_code"))} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Employee code" htmlFor="e-code" required><Input id="e-code" value={v("employee_code")} disabled={!!e} onChange={set("employee_code")} /></Field>
        <Field label="First name" htmlFor="e-fn" required><Input id="e-fn" value={v("first_name")} onChange={set("first_name")} /></Field>
        <Field label="Last name" htmlFor="e-ln"><Input id="e-ln" value={v("last_name")} onChange={set("last_name")} /></Field>
        <Field label="Department" htmlFor="e-d" required><DepartmentSelect id="e-d" value={v("department_id")} onChange={(x) => setF({ ...f, department_id: x })} allLabel="Select…" /></Field>
        <Field label="Designation" htmlFor="e-des"><Select id="e-des" value={v("designation_id")} onChange={set("designation_id")}><option value="">—</option>{designations.data?.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select></Field>
        <Field label="Reports to" htmlFor="e-m"><Select id="e-m" value={v("manager_id")} onChange={set("manager_id")}><option value="">—</option>{managers.data?.items.filter((x) => x.id !== e?.id).map((x) => <option key={x.id} value={x.id}>{x.full_name} ({x.employee_code})</option>)}</Select></Field>
        <Field label="Email" htmlFor="e-em" hint="Optional; staff without email sign in with their code"><Input id="e-em" value={v("email")} onChange={set("email")} /></Field>
        <Field label="Phone" htmlFor="e-ph"><Input id="e-ph" value={v("phone")} onChange={set("phone")} /></Field>
        <Field label="Status" htmlFor="e-st"><Select id="e-st" value={v("status")} onChange={set("status")}>{["active", "on_leave", "suspended", "inactive", "terminated"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}</Select></Field>
        <Field label="Employment type" htmlFor="e-et"><Select id="e-et" value={v("employment_type")} onChange={set("employment_type")}>{["permanent", "contract", "temporary", "apprentice", "part_time"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}</Select></Field>
        <Field label="Date of joining" htmlFor="e-doj"><Input id="e-doj" type="date" value={v("date_of_joining")} onChange={set("date_of_joining")} /></Field>
        <Field label="Work location" htmlFor="e-loc"><Select id="e-loc" value={v("work_location_id")} onChange={set("work_location_id")}><option value="">—</option>{locations.data?.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select></Field>
        <Field label="Rotation pattern" htmlFor="e-pat"><Select id="e-pat" value={v("shift_pattern_id")} onChange={set("shift_pattern_id")}><option value="">None</option>{patterns.data?.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select></Field>
        <Field label="Pattern starts on" htmlFor="e-pa" hint="Day 1 of the cycle"><Input id="e-pa" type="date" value={v("pattern_anchor_date")} onChange={set("pattern_anchor_date")} /></Field>
        <Field label="Weekly hour limit" htmlFor="e-wl" hint="Blank = department policy"><Input id="e-wl" inputMode="decimal" value={v("weekly_hour_limit")} onChange={set("weekly_hour_limit")} /></Field>
        {can("workforce.payroll") && <Field label="Base hourly rate" htmlFor="e-rate" hint="Visible to HR only"><Input id="e-rate" inputMode="decimal" value={v("base_hourly_rate")} onChange={set("base_hourly_rate")} /></Field>}
        <Field label="Emergency contact" htmlFor="e-ecn"><Input id="e-ecn" value={v("emergency_contact_name")} onChange={set("emergency_contact_name")} /></Field>
        <Field label="Emergency phone" htmlFor="e-ecp"><Input id="e-ecp" value={v("emergency_contact_phone")} onChange={set("emergency_contact_phone")} /></Field>
        <div className="flex items-end pb-2"><Checkbox label="Eligible for overtime" checked={!!f.overtime_eligible} onChange={(ev) => setF({ ...f, overtime_eligible: ev.target.checked })} /></div>
        <div className="sm:col-span-3"><div className="mb-1.5 text-xs font-medium text-fg-2">Teams (overtime eligibility groups)</div><div className="flex flex-wrap gap-3">
          {deptTeams.map((t) => <Checkbox key={t.id} label={t.name} checked={teamIds.includes(t.id)} onChange={(ev) => setTeamIds(ev.target.checked ? [...teamIds, t.id] : teamIds.filter((x) => x !== t.id))} />)}
          {!deptTeams.length && <span className="text-xs text-muted">No teams in this department yet.</span>}</div></div>
        <div className="sm:col-span-3"><div className="mb-1.5 text-xs font-medium text-fg-2">Skills & qualifications</div><div className="flex flex-wrap gap-3">
          {skills.data?.map((k) => <Checkbox key={k.id} label={k.name} checked={skillIds.includes(k.id)} onChange={(ev) => setSkillIds(ev.target.checked ? [...skillIds, k.id] : skillIds.filter((x) => x !== k.id))} />)}</div></div>
        <Field label="Address" htmlFor="e-ad" className="sm:col-span-3"><Textarea id="e-ad" value={v("address")} onChange={set("address")} /></Field>
      </div>
    </Dialog>
  );
}

export function EmployeeDetailPage() {
  const { id } = useParams();
  const today = useToday();
  const e = useQuery({ queryKey: ["employee", id], queryFn: () => api.get<Employee>(`/employees/${id}`) });
  const sched = useQuery({ queryKey: ["employee-sched", id], enabled: !!e.data, queryFn: () => api.get<{ shifts: import("@/types/workforce").Shift[] }>("/shifts/calendar", { date_from: today, date_to: addDays(today, 13), department_id: e.data!.department_id }) });
  const [edit, setEdit] = useState(false);
  const [access, setAccess] = useState(false);
  const [link, setLink] = useState(false);
  const [leave, setLeave] = useState(false);
  if (e.error) return <ErrorState error={e.error} onRetry={e.refetch} />;
  if (!e.data) return <Skeleton className="h-96" />;
  const x = e.data;
  const mine = (sched.data?.shifts ?? []).flatMap((s) => s.positions.filter((p) => p.assignment?.employee.id === x.id || p.on_leave.some((l) => l.employee.id === x.id)).map((p) => ({ s, p })));
  return (
    <div className="space-y-4">
      <PageHeader breadcrumb={[{ label: "Employees", to: "/workforce/employees" }]} title={<span className="flex flex-wrap items-center gap-3">{x.full_name} <StatusBadge status={x.status} /></span>}
        description={`${x.employee_code} · ${x.designation ?? "—"} · ${x.department_name}`}
        actions={<Can permission="workforce.employees.manage">
          {!x.has_login && <><Button onClick={() => setAccess(true)}><KeyRound /> Give portal access</Button><Button variant="ghost" onClick={() => setLink(true)}><Link2 /> Link existing login</Button></>}
          <Button onClick={() => setLeave(true)}>Leave on behalf</Button>
          <Button variant="primary" onClick={() => setEdit(true)}><Pencil /> Edit</Button></Can>} />
      <Card className="p-5"><DetailGrid items={[
        { label: "Teams", value: x.teams.map((t) => t.name).join(", ") || "—" }, { label: "Reports to", value: x.manager_name ?? "—" },
        { label: "Overtime", value: x.overtime_eligible ? "Eligible" : "Not eligible" }, { label: "Weekly limit", value: x.weekly_hour_limit ? `${Number(x.weekly_hour_limit)} h` : "Policy default" },
        { label: "Rotation", value: x.shift_pattern_name ? `${x.shift_pattern_name} from ${x.pattern_anchor_date}` : "—" }, { label: "Joined", value: x.date_of_joining ?? "—" },
        { label: "Location", value: x.work_location_name ?? "—" }, { label: "Sign-in", value: x.has_login ? (x.login_email ?? `Employee code ${x.employee_code}`) : "No portal access" },
        { label: "Phone", value: x.phone ?? "—" }, { label: "Emergency contact", value: x.emergency_contact_name ? `${x.emergency_contact_name} · ${x.emergency_contact_phone ?? ""}` : "—" },
        ...(x.base_hourly_rate !== undefined ? [{ label: "Base hourly rate", value: x.base_hourly_rate ? money(x.base_hourly_rate) : "Not set" }] : []),
        { label: "Skills", value: x.skills.map((k) => k.name).join(", ") || "—" },
      ]} /></Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card><CardHeader title="Leave balances" description={String(new Date().getFullYear())} />
          <div className="grid grid-cols-2 gap-2 px-5 pb-5 sm:grid-cols-3">{x.leave_balances?.map((b) => (
            <div key={b.code} className="rounded-lg border border-line px-3 py-2"><div className="text-[11px] text-muted">{b.name}</div>
              <div className="num font-semibold">{b.unlimited ? "No limit" : `${Number(b.available)} left`}</div><div className="text-[11px] text-muted">{Number(b.used)} used{Number(b.pending) ? ` · ${Number(b.pending)} pending` : ""}</div></div>))}</div></Card>
        <Card><CardHeader title="Next two weeks" />
          <ul className="divide-y divide-line">{!mine.length && <li className="px-5 py-4 text-sm text-muted">Nothing scheduled.</li>}
            {mine.map(({ s, p }) => <li key={p.id} className="flex justify-between px-5 py-2 text-sm"><span>{dayLabel(s.shift_date)} · {s.name} <span className="num text-muted">{s.start}–{s.end}</span></span>
              {p.assignment?.employee.id === x.id ? <span className="text-xs text-muted">{p.assignment.source === "schedule" ? "rostered" : "overtime"}</span> : <Badge tone="warn">On leave</Badge>}</li>)}</ul></Card>
      </div>
      {edit && <EmployeeDialog employee={x} onClose={() => setEdit(false)} />}
      {access && <PortalAccessDialog e={x} onClose={() => setAccess(false)} />}
      {link && <LinkLoginDialog e={x} onClose={() => setLink(false)} />}
      {leave && <ApplyLeaveDialog employeeId={x.id} onClose={() => setLeave(false)} />}
    </div>
  );
}

function PortalAccessDialog({ e, onClose }: { e: Employee; onClose: () => void }) {
  const [pw, setPw] = useState("");
  const run = useAction(() => api.post(`/employees/${e.id}/portal-access`, { password: pw }), { success: "Portal access created", invalidate: [["employee", e.id], ["employees"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Portal access for ${e.full_name}`} description={e.email ? `They sign in with ${e.email}.` : `They sign in with employee code ${e.employee_code}. Share the password in person.`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={pw.length < 10} loading={run.isPending} onClick={() => run.mutate(undefined)}>Create login</Button></>}>
      <Field label="Initial password" htmlFor="pa-pw" hint="At least 10 characters with upper and lower case, a number and a symbol."><Input id="pa-pw" type="password" value={pw} onChange={(ev) => setPw(ev.target.value)} autoComplete="new-password" /></Field>
    </Dialog>
  );
}

function LinkLoginDialog({ e, onClose }: { e: Employee; onClose: () => void }) {
  const users = useUsers();
  const [uid, setUid] = useState("");
  const run = useAction(() => api.post(`/employees/${e.id}/link-user`, { user_id: uid }), { success: "Login linked", invalidate: [["employee", e.id], ["employees"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Link an existing login" description="For managers and supervisors who already have an ERP user. Their schedule, leave and overtime then work for them too."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!uid} loading={run.isPending} onClick={() => run.mutate(undefined)}>Link</Button></>}>
      <Field label="User" htmlFor="ll-u"><Select id="ll-u" value={uid} onChange={(ev) => setUid(ev.target.value)}><option value="">Select…</option>{users.data?.items.map((u) => <option key={u.id} value={u.id}>{u.full_name} · {u.email}</option>)}</Select></Field>
    </Dialog>
  );
}

// ------------------------------------------------------------------ setup
export function WorkforceSetupPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Workforce setup" description="Rules are configured here, not hard-coded. Demo values are examples to adjust to your location's regulations and agreements." />
      <Tabs defaultValue="templates">
        <TabsList className="flex-wrap">
          {[["templates", "Shift templates"], ["patterns", "Rotations"], ["teams", "Teams"], ["refs", "Skills & roles"], ["holidays", "Holidays"], ["leave", "Leave types"],
            ["policies", "Shift & claim rules"], ["overtime", "Overtime rules"], ["notifications", "Notifications"]].map(([v, l]) => <TabsTrigger key={v} value={v!}>{l}</TabsTrigger>)}
        </TabsList>
        <TabsContent value="templates" className="mt-4"><TemplatesTab /></TabsContent>
        <TabsContent value="patterns" className="mt-4"><PatternsTab /></TabsContent>
        <TabsContent value="teams" className="mt-4"><TeamsTab /></TabsContent>
        <TabsContent value="refs" className="mt-4"><RefsTab /></TabsContent>
        <TabsContent value="holidays" className="mt-4"><HolidaysTab /></TabsContent>
        <TabsContent value="leave" className="mt-4"><LeaveTypesTab /></TabsContent>
        <TabsContent value="policies" className="mt-4"><PoliciesTab /></TabsContent>
        <TabsContent value="overtime" className="mt-4"><OvertimePolicyTab /></TabsContent>
        <TabsContent value="notifications" className="mt-4"><ChannelsTab /></TabsContent>
      </Tabs>
    </div>
  );
}

function TemplatesTab() {
  const list = useQuery({ queryKey: ["shift-templates", "all"], queryFn: () => api.get<ShiftTemplate[]>("/shift-templates", { include_inactive: true }) });
  const [edit, setEdit] = useState<ShiftTemplate | null | undefined>(undefined);
  return (
    <Card>
      <CardHeader title="Shift templates" description="Overnight shifts (end before start) are supported." actions={<Can permission="workforce.config"><Button size="sm" variant="primary" onClick={() => setEdit(null)}><Plus /> New template</Button></Can>} />
      <DataTable rows={list.data} loading={list.isLoading} rowKey={(t) => t.id} onRowClick={setEdit} empty={<EmptyState title="No templates" />}
        columns={[
          { key: "n", header: "Shift", cell: (t) => <div className="flex items-center gap-2"><span className="size-2.5 rounded-full" style={{ background: t.color ?? "var(--muted)" }} /><div><div className="font-medium">{t.name}</div><div className="font-mono text-xs text-muted">{t.code}</div></div></div> },
          { key: "t", header: "Time", cell: (t) => <span className="num">{t.start_time}–{t.end_time}{t.overnight && " (+1)"}</span> },
          { key: "b", header: "Break", hideBelow: "md", cell: (t) => `${t.break_minutes} min` },
          { key: "d", header: "Department · team", cell: (t) => <span>{t.department_name}{t.team_name && <span className="block text-xs text-muted">{t.team_name}</span>}</span> },
          { key: "r", header: "People", align: "right", cell: (t) => t.required_count },
          { key: "s", header: "Skills", hideBelow: "lg", cell: (t) => t.skills.map((k) => k.name).join(", ") || "—" },
          { key: "a", header: "", cell: (t) => t.is_active ? null : <Badge>Inactive</Badge> },
        ]} />
      {edit !== undefined && <TemplateDialog t={edit} onClose={() => setEdit(undefined)} />}
    </Card>
  );
}

function TemplateDialog({ t, onClose }: { t: ShiftTemplate | null; onClose: () => void }) {
  const teams = useTeams();
  const skills = useRefs("skills");
  const locations = useRefs("locations");
  const [f, setF] = useState({ code: t?.code ?? "", name: t?.name ?? "", start_time: t?.start_time ?? "06:00", end_time: t?.end_time ?? "14:00", break_minutes: String(t?.break_minutes ?? 30),
    department_id: t?.department_id ?? "", team_id: t?.team_id ?? "", work_location_id: t?.work_location_id ?? "", required_count: String(t?.required_count ?? 1), color: t?.color ?? "#3d6b57", is_active: t?.is_active ?? true });
  const [skillIds, setSkillIds] = useState<string[]>(t?.skills.map((k) => k.id) ?? []);
  const save = useAction(() => {
    const body = { name: f.name, start_time: f.start_time, end_time: f.end_time, break_minutes: Number(f.break_minutes), team_id: f.team_id || null, work_location_id: f.work_location_id || null,
      required_count: Number(f.required_count), color: f.color, skill_ids: skillIds };
    return t ? api.patch(`/shift-templates/${t.id}`, { ...body, is_active: f.is_active }) : api.post("/shift-templates", { ...body, code: f.code, department_id: f.department_id });
  }, { success: "Template saved — new shifts use it; existing shifts keep their own times", invalidate: [["shift-templates"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title={t ? `Edit ${t.name}` : "New shift template"}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.name || (!t && (!f.code || !f.department_id))} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Code" htmlFor="t-c" required><Input id="t-c" value={f.code} disabled={!!t} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
        <Field label="Name" htmlFor="t-n" required><Input id="t-n" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Department" htmlFor="t-d" required><DepartmentSelect id="t-d" value={f.department_id} onChange={(v) => setF({ ...f, department_id: v })} allLabel={t ? null : "Select…"} /></Field>
        <Field label="Start" htmlFor="t-s"><Input id="t-s" type="time" value={f.start_time} onChange={(e) => setF({ ...f, start_time: e.target.value })} /></Field>
        <Field label="End" htmlFor="t-e" hint={f.end_time <= f.start_time ? "Ends the next day" : undefined}><Input id="t-e" type="time" value={f.end_time} onChange={(e) => setF({ ...f, end_time: e.target.value })} /></Field>
        <Field label="Break (min)" htmlFor="t-b"><Input id="t-b" type="number" min={0} value={f.break_minutes} onChange={(e) => setF({ ...f, break_minutes: e.target.value })} /></Field>
        <Field label="Team (overtime offered to)" htmlFor="t-t"><Select id="t-t" value={f.team_id} onChange={(e) => setF({ ...f, team_id: e.target.value })}><option value="">Whole department</option>
          {teams.data?.filter((x) => !f.department_id || x.department_id === f.department_id).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select></Field>
        <Field label="Location" htmlFor="t-l"><Select id="t-l" value={f.work_location_id} onChange={(e) => setF({ ...f, work_location_id: e.target.value })}><option value="">—</option>{locations.data?.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select></Field>
        <Field label="People required" htmlFor="t-r"><Input id="t-r" type="number" min={1} value={f.required_count} onChange={(e) => setF({ ...f, required_count: e.target.value })} /></Field>
        <Field label="Colour" htmlFor="t-col"><Input id="t-col" type="color" value={f.color} onChange={(e) => setF({ ...f, color: e.target.value })} /></Field>
        <div className="sm:col-span-3"><div className="mb-1.5 text-xs font-medium text-fg-2">Required skills</div><div className="flex flex-wrap gap-3">
          {skills.data?.map((k) => <Checkbox key={k.id} label={k.name} checked={skillIds.includes(k.id)} onChange={(e) => setSkillIds(e.target.checked ? [...skillIds, k.id] : skillIds.filter((x) => x !== k.id))} />)}</div></div>
        {t && <Checkbox label="Active" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />}
      </div>
    </Dialog>
  );
}

const WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function PatternsTab() {
  const list = useQuery({ queryKey: ["patterns"], queryFn: () => api.get<ShiftPattern[]>("/shift-patterns") });
  const templates = useTemplates();
  const [edit, setEdit] = useState<ShiftPattern | null | undefined>(undefined);
  return (
    <Card>
      <CardHeader title="Rotation patterns" description="A cycle of days, each a shift or a weekly off. Employees get a pattern and the day it starts; the schedule is generated from it."
        actions={<Can permission="workforce.config"><Button size="sm" variant="primary" onClick={() => setEdit(null)}><Plus /> New rotation</Button></Can>} />
      <div className="divide-y divide-line">{list.data?.map((p) => (
        <button key={p.id} onClick={() => setEdit(p)} className="flex w-full flex-wrap items-center gap-4 px-5 py-3 text-left hover:bg-surface-2/50">
          <div className="w-56"><div className="text-sm font-medium">{p.name}</div><div className="font-mono text-xs text-muted">{p.code} · {p.cycle_days}-day cycle</div></div>
          <div className="flex flex-wrap gap-1">{p.days.map((d) => <span key={d.day_index} className={`rounded px-1.5 py-0.5 text-[11px] ${d.template_id ? "bg-accent-soft text-accent" : "bg-surface-2 text-muted"}`}>{d.template_name ?? "Off"}</span>)}</div>
        </button>))}
        {!list.data?.length && <EmptyState title="No rotation patterns" />}
      </div>
      {edit !== undefined && <PatternDialog p={edit} templates={templates.data ?? []} onClose={() => setEdit(undefined)} />}
    </Card>
  );
}

function PatternDialog({ p, templates, onClose }: { p: ShiftPattern | null; templates: ShiftTemplate[]; onClose: () => void }) {
  const [code, setCode] = useState(p?.code ?? "");
  const [name, setName] = useState(p?.name ?? "");
  const [days, setDays] = useState<string[]>(p ? p.days.map((d) => d.template_id ?? "") : Array(7).fill(""));
  const save = useAction(() => { const body = { code, name, cycle_days: days.length, days: days.map((d) => d || null) };
    return p ? api.put(`/shift-patterns/${p.id}`, body) : api.post("/shift-patterns", body); }, { success: "Rotation saved", invalidate: [["patterns"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title={p ? `Edit ${p.name}` : "New rotation"}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!code || !name} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Code" htmlFor="p-c"><Input id="p-c" value={code} disabled={!!p} onChange={(e) => setCode(e.target.value)} /></Field>
        <Field label="Name" htmlFor="p-n" className="sm:col-span-2"><Input id="p-n" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Cycle length (days)" htmlFor="p-l"><Input id="p-l" type="number" min={1} max={56} value={days.length}
          onChange={(e) => { const n = Math.max(1, Math.min(56, Number(e.target.value) || 1)); setDays(Array.from({ length: n }, (_, i) => days[i] ?? "")); }} /></Field>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">{days.map((d, i) => (
        <Field key={i} label={`Day ${i + 1}${days.length % 7 === 0 ? ` · ${WEEK[i % 7]}` : ""}`} htmlFor={`pd-${i}`}><Select id={`pd-${i}`} value={d} onChange={(e) => setDays(days.map((x, j) => (j === i ? e.target.value : x)))}>
          <option value="">Weekly off</option>{templates.map((t) => <option key={t.id} value={t.id}>{t.name} {t.start_time}</option>)}</Select></Field>))}</div>
    </Dialog>
  );
}

function TeamsTab() {
  const teams = useTeams();
  const emps = useQuery({ queryKey: ["employees", "all-brief"], queryFn: () => api.get<Page<Employee>>("/employees", { page_size: 200 }) });
  const [edit, setEdit] = useState<Team | null | undefined>(undefined);
  const [f, setF] = useState({ code: "", name: "", department_id: "" });
  const [members, setMembers] = useState<string[]>([]);
  const save = useAction(() => edit ? api.patch(`/workforce/teams/${edit.id}`, { name: f.name, member_ids: members }) : api.post("/workforce/teams", { ...f, member_ids: members }),
    { success: "Team saved", invalidate: [["wf-teams"], ["employees"]], onSuccess: () => setEdit(undefined) });
  const open = (t: Team | null) => { setEdit(t); setF({ code: t?.code ?? "", name: t?.name ?? "", department_id: t?.department_id ?? "" }); setMembers(t?.members?.map((m) => m.id) ?? []); };
  return (
    <Card>
      <CardHeader title="Teams" description="Overtime is offered to the team a shift belongs to (or the whole department)." actions={<Can permission="workforce.config"><Button size="sm" variant="primary" onClick={() => open(null)}><Plus /> New team</Button></Can>} />
      <div className="divide-y divide-line">{teams.data?.map((t) => (
        <button key={t.id} onClick={() => open(t)} className="flex w-full items-center justify-between px-5 py-3 text-left hover:bg-surface-2/50">
          <span><span className="text-sm font-medium">{t.name}</span><span className="block text-xs text-muted">{t.department_name} · {t.code}</span></span><span className="text-sm text-fg-2">{t.member_count} members</span></button>))}</div>
      {edit !== undefined && <Dialog open onOpenChange={(o) => !o && setEdit(undefined)} size="lg" title={edit ? `Edit ${edit.name}` : "New team"}
        footer={<><Button onClick={() => setEdit(undefined)}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.name || (!edit && (!f.code || !f.department_id))} onClick={() => save.mutate(undefined)}>Save</Button></>}>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Code" htmlFor="tm-c"><Input id="tm-c" value={f.code} disabled={!!edit} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
          <Field label="Name" htmlFor="tm-n"><Input id="tm-n" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Department" htmlFor="tm-d"><DepartmentSelect id="tm-d" value={f.department_id} onChange={(v) => setF({ ...f, department_id: v })} allLabel={edit ? null : "Select…"} /></Field>
        </div>
        <div className="mt-4 text-xs font-medium text-fg-2">Members</div>
        <div className="mt-2 grid max-h-64 gap-1.5 overflow-y-auto sm:grid-cols-2">{emps.data?.items.filter((e) => !f.department_id || e.department_id === f.department_id).map((e) =>
          <Checkbox key={e.id} label={`${e.full_name} (${e.employee_code})`} checked={members.includes(e.id)} onChange={(ev) => setMembers(ev.target.checked ? [...members, e.id] : members.filter((m) => m !== e.id))} />)}</div>
      </Dialog>}
    </Card>
  );
}

function RefsTab() {
  return <div className="grid gap-4 lg:grid-cols-3">{(["skills", "designations", "locations"] as const).map((k) => <RefCard key={k} kind={k} />)}</div>;
}

function RefCard({ kind }: { kind: "skills" | "designations" | "locations" }) {
  const list = useRefs(kind);
  const [f, setF] = useState({ code: "", name: "", kind: "skill" });
  const add = useAction(() => api.post<Ref>(`/workforce/${kind}`, kind === "skills" ? f : { code: f.code, name: f.name }), { success: "Added", invalidate: [["wf-ref", kind]], onSuccess: () => setF({ code: "", name: "", kind: "skill" }) });
  const title = { skills: "Skills & qualifications", designations: "Designations", locations: "Work locations" }[kind];
  return (
    <Card><CardHeader title={title} />
      <ul className="max-h-72 divide-y divide-line overflow-y-auto">{list.data?.map((r) => <li key={r.id} className="flex justify-between px-5 py-2 text-sm"><span>{r.name}</span><span className="font-mono text-xs text-muted">{r.code}{r.kind && r.kind !== "skill" ? ` · ${r.kind}` : ""}</span></li>)}</ul>
      <Can permission="workforce.config"><div className="flex flex-wrap gap-2 border-t border-line p-3">
        <Input placeholder="Code" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} className="w-24" aria-label="Code" />
        <Input placeholder="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} className="min-w-32 flex-1" aria-label="Name" />
        {kind === "skills" && <Select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} className="w-36" aria-label="Kind"><option value="skill">Skill</option><option value="qualification">Qualification</option><option value="certification">Certification</option></Select>}
        <Button size="sm" disabled={!f.code || !f.name} loading={add.isPending} onClick={() => add.mutate(undefined)}><Plus /> Add</Button></div></Can>
    </Card>
  );
}

function HolidaysTab() {
  const [year, setYear] = useState(new Date().getFullYear());
  const list = useQuery({ queryKey: ["holidays", year], queryFn: () => api.get<Holiday[]>("/workforce/holidays", { year }) });
  const [f, setF] = useState({ holiday_date: "", name: "" });
  const add = useAction(() => api.post("/workforce/holidays", f), { success: "Holiday added", invalidate: [["holidays"]], onSuccess: () => setF({ holiday_date: "", name: "" }) });
  const del = useAction((id: string) => api.del(`/workforce/holidays/${id}`), { success: "Removed", invalidate: [["holidays"]] });
  return (
    <Card><CardHeader title="Holidays" description="Holidays don't count as leave days; hours worked on them follow the holiday overtime rule."
      actions={<Select value={year} onChange={(e) => setYear(Number(e.target.value))} className="w-28" aria-label="Year">{[year - 1, year, year + 1].map((y) => <option key={y}>{y}</option>)}</Select>} />
      <ul className="divide-y divide-line">{list.data?.map((h) => <li key={h.id} className="flex items-center justify-between px-5 py-2 text-sm"><span><span className="num mr-3 text-muted">{dayLabel(h.holiday_date)}</span>{h.name}</span>
        <Can permission="workforce.config"><Button size="sm" variant="ghost" onClick={() => del.mutate(h.id)} aria-label={`Remove ${h.name}`}><Trash2 /></Button></Can></li>)}
        {!list.data?.length && <li className="px-5 py-4 text-sm text-muted">No holidays for {year}.</li>}</ul>
      <Can permission="workforce.config"><div className="flex flex-wrap gap-2 border-t border-line p-3">
        <Input type="date" value={f.holiday_date} onChange={(e) => setF({ ...f, holiday_date: e.target.value })} className="w-44" aria-label="Date" />
        <Input placeholder="Name, e.g. Diwali" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} className="min-w-40 flex-1" aria-label="Holiday name" />
        <Button size="sm" disabled={!f.holiday_date || !f.name} loading={add.isPending} onClick={() => add.mutate(undefined)}><Plus /> Add</Button></div></Can>
    </Card>
  );
}

function LeaveTypesTab() {
  const types = useQuery({ queryKey: ["leave-types", "all"], queryFn: () => api.get<LeaveType[]>("/leave/types", { include_inactive: true }) });
  const [edit, setEdit] = useState<LeaveType | null | undefined>(undefined);
  return (
    <Card><CardHeader title="Leave types" actions={<Can permission="workforce.config"><Button size="sm" variant="primary" onClick={() => setEdit(null)}><Plus /> New type</Button></Can>} />
      <DataTable rows={types.data} loading={types.isLoading} rowKey={(t) => t.id} onRowClick={setEdit}
        columns={[
          { key: "n", header: "Type", cell: (t) => <div><div className="font-medium">{t.name}</div><div className="font-mono text-xs text-muted">{t.code}</div></div> },
          { key: "d", header: "Days / year", align: "right", cell: (t) => t.unlimited ? "No limit" : Number(t.annual_days) },
          { key: "c", header: "Carry forward", align: "right", hideBelow: "md", cell: (t) => Number(t.carry_forward_max) || "—" },
          { key: "p", header: "Paid", cell: (t) => t.is_paid ? "Yes" : "No" },
          { key: "x", header: "Rules", hideBelow: "md", cell: (t) => [t.requires_document && "document required", t.allow_half_day && "half days"].filter(Boolean).join(", ") || "—" },
          { key: "a", header: "", cell: (t) => t.is_active ? null : <Badge>Inactive</Badge> },
        ]} />
      {edit !== undefined && <LeaveTypeDialog t={edit} onClose={() => setEdit(undefined)} />}
    </Card>
  );
}

function LeaveTypeDialog({ t, onClose }: { t: LeaveType | null; onClose: () => void }) {
  const [f, setF] = useState({ code: t?.code ?? "", name: t?.name ?? "", annual_days: t?.annual_days ?? "0", carry_forward_max: t?.carry_forward_max ?? "0", is_paid: t?.is_paid ?? true,
    unlimited: t?.unlimited ?? false, requires_document: t?.requires_document ?? false, allow_half_day: t?.allow_half_day ?? true, is_active: t?.is_active ?? true });
  const save = useAction(() => { const { code, is_active, ...rest } = f; return t ? api.patch(`/leave/types/${t.id}`, { ...rest, is_active }) : api.post("/leave/types", { code, ...rest }); },
    { success: "Leave type saved", invalidate: [["leave-types"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={t ? `Edit ${t.name}` : "New leave type"} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.code || !f.name} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Code" htmlFor="lt-c"><Input id="lt-c" value={f.code} disabled={!!t} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
        <Field label="Name" htmlFor="lt-n"><Input id="lt-n" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Days per year" htmlFor="lt-d"><Input id="lt-d" inputMode="decimal" value={f.annual_days} disabled={f.unlimited} onChange={(e) => setF({ ...f, annual_days: e.target.value })} /></Field>
        <Field label="Carry forward up to" htmlFor="lt-cf"><Input id="lt-cf" inputMode="decimal" value={f.carry_forward_max} onChange={(e) => setF({ ...f, carry_forward_max: e.target.value })} /></Field>
        {(["is_paid", "unlimited", "requires_document", "allow_half_day"] as const).map((k) => <Checkbox key={k} label={{ is_paid: "Paid", unlimited: "No yearly limit", requires_document: "Needs a supporting document", allow_half_day: "Half days allowed" }[k]}
          checked={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.checked })} />)}
        {t && <Checkbox label="Active" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />}
      </div>
    </Dialog>
  );
}

const POLICY_FIELDS: { k: keyof WorkforcePolicy; label: string; hint?: string; kind: "bool" | "num" }[] = [
  { k: "auto_publish", label: "Publish vacancies automatically when leave is approved", kind: "bool" },
  { k: "claim_deadline_hours", label: "Claims close (hours before shift start)", kind: "num" },
  { k: "min_rest_hours", label: "Minimum rest between shifts (hours)", kind: "num" },
  { k: "weekly_hour_cap", label: "Weekly hour limit", hint: "Blank = no limit", kind: "num" },
  { k: "monthly_cover_hours_cap", label: "Covered-shift hours per month limit", hint: "Blank = no limit", kind: "num" },
  { k: "withdrawal_cutoff_hours", label: "Employees may withdraw until (hours before start)", kind: "num" },
  { k: "auto_reopen_on_withdrawal", label: "Re-offer a shift automatically when someone withdraws", kind: "bool" },
  { k: "allow_self_check_in", label: "Allow employee self check-in", kind: "bool" },
  { k: "half_day_auto_cover", label: "Offer cover for half-day leave automatically", kind: "bool" },
  { k: "show_pay_estimates", label: "Show employees estimated overtime earnings", kind: "bool" },
  { k: "late_grace_minutes", label: "Late after (minutes)", kind: "num" },
  { k: "half_day_min_hours", label: "Half day below (hours worked)", kind: "num" },
];

function PoliciesTab() {
  const list = useQuery({ queryKey: ["wf-policies"], queryFn: () => api.get<WorkforcePolicy[]>("/workforce/policies") });
  const depts = useDepartments();
  const [edit, setEdit] = useState<WorkforcePolicy | null>(null);
  const [newDept, setNewDept] = useState("");
  const company = list.data?.find((p) => !p.department_id);
  return (
    <div className="space-y-3">
      {list.data?.map((p) => (
        <Card key={p.id} className="p-5">
          <div className="flex items-start justify-between gap-3"><div><div className="text-sm font-semibold">{p.department_name ?? "Company default"}</div>
            <p className="mt-1 text-xs text-muted">Claims close {Number(p.claim_deadline_hours)} h before start · rest {Number(p.min_rest_hours)} h · weekly limit {p.weekly_hour_cap ? `${Number(p.weekly_hour_cap)} h` : "none"} · withdraw until {Number(p.withdrawal_cutoff_hours)} h before · {p.auto_publish ? "auto-publish" : "manager reviews first"} · self check-in {p.allow_self_check_in ? "on" : "off"}</p></div>
            <Can permission="workforce.config"><Button size="sm" onClick={() => setEdit(p)}><Pencil /> Edit</Button></Can></div>
        </Card>))}
      <Can permission="workforce.config"><Card className="flex flex-wrap items-center gap-2 p-4"><span className="text-sm">Different rules for a department:</span>
        <Select value={newDept} onChange={(e) => setNewDept(e.target.value)} className="w-56" aria-label="Department"><option value="">Select…</option>
          {depts.data?.filter((d) => !list.data?.some((p) => p.department_id === d.id)).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select>
        <Button size="sm" disabled={!newDept || !company} onClick={() => company && setEdit({ ...company, id: "", department_id: newDept, department_name: depts.data?.find((d) => d.id === newDept)?.name ?? null })}><Plus /> Add override</Button></Card></Can>
      {edit && <PolicyDialog p={edit} onClose={() => setEdit(null)} />}
    </div>
  );
}

function PolicyDialog({ p, onClose }: { p: WorkforcePolicy; onClose: () => void }) {
  const [f, setF] = useState<Record<string, unknown>>({ ...p });
  const [off, setOff] = useState<number[]>(p.weekly_off_days);
  const save = useAction(() => { const body: Record<string, unknown> = { department_id: p.department_id, weekly_off_days: off };
    for (const fd of POLICY_FIELDS) body[fd.k] = fd.kind === "num" ? (f[fd.k] === "" || f[fd.k] === null ? null : String(f[fd.k])) : !!f[fd.k];
    return api.put("/workforce/policies", body); }, { success: "Rules saved", invalidate: [["wf-policies"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title={`Rules · ${p.department_name ?? "Company default"}`} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        {POLICY_FIELDS.filter((x) => x.kind === "num").map((x) => <Field key={x.k} label={x.label} hint={x.hint} htmlFor={`pol-${x.k}`}><Input id={`pol-${x.k}`} inputMode="decimal" value={String(f[x.k] ?? "")} onChange={(e) => setF({ ...f, [x.k]: e.target.value })} /></Field>)}
        <div className="space-y-2 sm:col-span-2">{POLICY_FIELDS.filter((x) => x.kind === "bool").map((x) => <Checkbox key={x.k} label={x.label} checked={!!f[x.k]} onChange={(e) => setF({ ...f, [x.k]: e.target.checked })} />)}</div>
        <div className="sm:col-span-2"><div className="mb-1.5 text-xs font-medium text-fg-2">Weekly off days (for employees without a rotation)</div><div className="flex flex-wrap gap-3">
          {WEEK.map((d, i) => <Checkbox key={d} label={d} checked={off.includes(i + 1)} onChange={(e) => setOff(e.target.checked ? [...off, i + 1] : off.filter((x) => x !== i + 1))} />)}</div></div>
      </div>
    </Dialog>
  );
}

function OvertimePolicyTab() {
  const list = useQuery({ queryKey: ["ot-policies"], queryFn: () => api.get<OvertimePolicy[]>("/overtime/policies") });
  const [edit, setEdit] = useState<OvertimePolicy | null | undefined>(undefined);
  return (
    <div className="space-y-3">
      {list.data?.map((p) => (
        <Card key={p.id} className="p-5"><div className="flex items-start justify-between gap-3">
          <div><div className="flex items-center gap-2 text-sm font-semibold">{p.name}{p.is_default && <Badge tone="accent">Default</Badge>}</div>
            <p className="mt-1 text-xs text-muted">Over {Number(p.daily_threshold_hours)} h/day or {Number(p.weekly_threshold_hours)} h/week at ×{Number(p.ot_multiplier)} · weekly off {p.weekly_off_rule === "all_overtime" ? `all hours ×${Number(p.weekly_off_multiplier)}` : "normal"} · holidays {p.holiday_rule === "all_overtime" ? `all hours ×${Number(p.holiday_multiplier)}` : "normal"} · round {p.rounding_mode} to {p.rounding_minutes} min · ignore under {p.min_overtime_minutes} min{p.night_start ? ` · night ${p.night_start}–${p.night_end} +${money(p.night_allowance_per_hour)}/h` : ""}</p>
            {p.description && <p className="mt-1 text-xs text-fg-2">{p.description}</p>}</div>
          <Can permission="workforce.config"><Button size="sm" onClick={() => setEdit(p)}><Pencil /> Edit</Button></Can></div></Card>))}
      <Can permission="workforce.config"><Button onClick={() => setEdit(null)}><Plus /> New overtime policy</Button></Can>
      {edit !== undefined && <OtPolicyDialog p={edit} onClose={() => setEdit(undefined)} />}
    </div>
  );
}

function OtPolicyDialog({ p, onClose }: { p: OvertimePolicy | null; onClose: () => void }) {
  const [f, setF] = useState<Record<string, string | boolean>>({
    code: p?.code ?? "", name: p?.name ?? "", description: p?.description ?? "", daily_threshold_hours: p?.daily_threshold_hours ?? "9", weekly_threshold_hours: p?.weekly_threshold_hours ?? "48",
    ot_multiplier: p?.ot_multiplier ?? "2", weekly_off_rule: p?.weekly_off_rule ?? "all_overtime", weekly_off_multiplier: p?.weekly_off_multiplier ?? "2", holiday_rule: p?.holiday_rule ?? "all_overtime",
    holiday_multiplier: p?.holiday_multiplier ?? "2", night_start: p?.night_start?.slice(0, 5) ?? "", night_end: p?.night_end?.slice(0, 5) ?? "", night_allowance_per_hour: p?.night_allowance_per_hour ?? "0",
    rounding_minutes: String(p?.rounding_minutes ?? 15), rounding_mode: p?.rounding_mode ?? "down", min_overtime_minutes: String(p?.min_overtime_minutes ?? 30),
    requires_approval: p?.requires_approval ?? true, is_default: p?.is_default ?? false,
  });
  const v = (k: string) => String(f[k] ?? "");
  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const save = useAction(() => { const body = { ...f, night_start: f.night_start || null, night_end: f.night_end || null, description: f.description || null,
    rounding_minutes: Number(f.rounding_minutes), min_overtime_minutes: Number(f.min_overtime_minutes) };
    return p ? api.put(`/overtime/policies/${p.id}`, body) : api.post("/overtime/policies", body); }, { success: "Overtime policy saved — use Recalculate on Overtime records to apply it", invalidate: [["ot-policies"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title={p ? `Edit ${p.name}` : "New overtime policy"} description="Set these to match your location's regulations and agreements. Changes apply to records recalculated afterwards; exported records never change."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!v("code") || !v("name")} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Code" htmlFor="op-c"><Input id="op-c" value={v("code")} disabled={!!p} onChange={set("code")} /></Field>
        <Field label="Name" htmlFor="op-n" className="sm:col-span-2"><Input id="op-n" value={v("name")} onChange={set("name")} /></Field>
        <Field label="Daily threshold (h)" htmlFor="op-dt"><Input id="op-dt" inputMode="decimal" value={v("daily_threshold_hours")} onChange={set("daily_threshold_hours")} /></Field>
        <Field label="Weekly threshold (h)" htmlFor="op-wt"><Input id="op-wt" inputMode="decimal" value={v("weekly_threshold_hours")} onChange={set("weekly_threshold_hours")} /></Field>
        <Field label="Overtime multiplier" htmlFor="op-m"><Input id="op-m" inputMode="decimal" value={v("ot_multiplier")} onChange={set("ot_multiplier")} /></Field>
        <Field label="Weekly-off days" htmlFor="op-wo"><Select id="op-wo" value={v("weekly_off_rule")} onChange={set("weekly_off_rule")}><option value="all_overtime">All hours are overtime</option><option value="normal">Normal rules</option></Select></Field>
        <Field label="Weekly-off multiplier" htmlFor="op-wom"><Input id="op-wom" inputMode="decimal" value={v("weekly_off_multiplier")} onChange={set("weekly_off_multiplier")} /></Field>
        <div />
        <Field label="Holidays" htmlFor="op-h"><Select id="op-h" value={v("holiday_rule")} onChange={set("holiday_rule")}><option value="all_overtime">All hours are overtime</option><option value="normal">Normal rules</option></Select></Field>
        <Field label="Holiday multiplier" htmlFor="op-hm"><Input id="op-hm" inputMode="decimal" value={v("holiday_multiplier")} onChange={set("holiday_multiplier")} /></Field>
        <div />
        <Field label="Night from" htmlFor="op-ns"><Input id="op-ns" type="time" value={v("night_start")} onChange={set("night_start")} /></Field>
        <Field label="Night until" htmlFor="op-ne"><Input id="op-ne" type="time" value={v("night_end")} onChange={set("night_end")} /></Field>
        <Field label="Night allowance per hour" htmlFor="op-na"><Input id="op-na" inputMode="decimal" value={v("night_allowance_per_hour")} onChange={set("night_allowance_per_hour")} /></Field>
        <Field label="Round to (min)" htmlFor="op-r"><Input id="op-r" type="number" min={0} value={v("rounding_minutes")} onChange={set("rounding_minutes")} /></Field>
        <Field label="Rounding" htmlFor="op-rm"><Select id="op-rm" value={v("rounding_mode")} onChange={set("rounding_mode")}><option value="down">Down</option><option value="nearest">Nearest</option><option value="up">Up</option></Select></Field>
        <Field label="Ignore overtime under (min)" htmlFor="op-mo"><Input id="op-mo" type="number" min={0} value={v("min_overtime_minutes")} onChange={set("min_overtime_minutes")} /></Field>
        <Field label="Description" htmlFor="op-d" className="sm:col-span-3"><Textarea id="op-d" value={v("description")} onChange={set("description")} /></Field>
        <Checkbox label="Manager approval required" checked={!!f.requires_approval} onChange={(e) => setF({ ...f, requires_approval: e.target.checked })} />
        <Checkbox label="Default policy" checked={!!f.is_default} onChange={(e) => setF({ ...f, is_default: e.target.checked })} />
      </div>
    </Dialog>
  );
}

function ChannelsTab() {
  const ch = useQuery({ queryKey: ["wf-channels"], queryFn: () => api.get<Channel[]>("/workforce/notification-channels") });
  const LABEL: Record<string, string> = { in_app: "In-app", email: "Email", push: "Push", whatsapp: "WhatsApp" };
  return (
    <Card><CardHeader title="Notification channels" description="Each person chooses their channels under Profile & notifications. A channel only delivers when it is connected." />
      <ul className="divide-y divide-line">{ch.data?.map((c) => <li key={c.channel} className="flex items-center justify-between px-5 py-3 text-sm"><span><span className="font-medium">{LABEL[c.channel] ?? c.channel}</span><span className="block text-xs text-muted">{c.detail}</span></span>
        {c.connected ? <Badge tone="ok">Connected</Badge> : <Badge>Not connected</Badge>}</li>)}</ul>
      <p className="border-t border-line px-5 py-3 text-xs text-muted">Email sends when the server is configured with SMTP settings. Push and WhatsApp need a provider integration; until then nothing is sent on those channels.</p>
    </Card>
  );
}

// ------------------------------------------------------------------ payroll
export function PayrollPage() {
  const today = useToday();
  const list = useQuery({ queryKey: ["payroll"], queryFn: () => api.get<PayrollBatch[]>("/payroll/exports") });
  const [f, setF] = useState({ period_start: `${today.slice(0, 8)}01`, period_end: today, department_id: "" });
  const [key, setKey] = useState(newIdempotencyKey);
  const create = useAction(() => api.post<PayrollBatch>("/payroll/exports", { ...f, department_id: f.department_id || null }, key),
    { success: (b) => `${b.number}: ${b.line_count} line(s), ${Number(b.total_hours)} h`, invalidate: [["payroll"], ["wf-calc"]], onSuccess: () => setKey(newIdempotencyKey()) });
  return (
    <div className="space-y-4">
      <PageHeader title="Payroll export" description="Approved overtime for a period, ready for your payroll software. Exported records are locked; later changes appear as correction lines in the next export." />
      <Card className="p-4"><div className="flex flex-wrap items-end gap-3">
        <Field label="From" htmlFor="pe-f"><Input id="pe-f" type="date" value={f.period_start} onChange={(e) => setF({ ...f, period_start: e.target.value })} /></Field>
        <Field label="To" htmlFor="pe-t"><Input id="pe-t" type="date" value={f.period_end} onChange={(e) => setF({ ...f, period_end: e.target.value })} /></Field>
        <Field label="Department" htmlFor="pe-d"><DepartmentSelect id="pe-d" value={f.department_id} onChange={(v) => setF({ ...f, department_id: v })} /></Field>
        <Button variant="primary" loading={create.isPending} onClick={() => create.mutate(undefined)}><FileSpreadsheet /> Create export</Button>
      </div></Card>
      <Card>
        <DataTable rows={list.data} loading={list.isLoading} error={list.error} rowKey={(b) => b.id} empty={<EmptyState title="No exports yet" icon={<FileSpreadsheet />} />}
          columns={[
            { key: "n", header: "Batch", cell: (b) => <span className="font-mono text-xs">{b.number}</span> },
            { key: "p", header: "Period", cell: (b) => `${dayLabel(b.period_start)} – ${dayLabel(b.period_end)}` },
            { key: "d", header: "Department", hideBelow: "md", cell: (b) => b.department_name ?? "All" },
            { key: "e", header: "Employees", align: "right", cell: (b) => b.employee_count },
            { key: "h", header: "Overtime", align: "right", cell: (b) => <span className="num">{hrs(b.total_minutes)}</span> },
            { key: "a", header: "Amount", align: "right", cell: (b) => <span className="num">{money(b.total_amount)}</span> },
            { key: "c", header: "Created", hideBelow: "lg", cell: (b) => <span className="text-xs text-muted">{dateTime(b.created_at)}</span> },
            { key: "dl", header: "", cell: (b) => <div className="flex justify-end gap-1">
              <a href={api.downloadUrl(`/payroll/exports/${b.id}/download`, { format: "csv" })}><Button size="sm" variant="ghost"><Download /> CSV</Button></a>
              <a href={api.downloadUrl(`/payroll/exports/${b.id}/download`, { format: "xlsx" })}><Button size="sm" variant="ghost"><Download /> Excel</Button></a></div> },
          ]} />
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ reports
export function WorkforceReportsPage() {
  const today = useToday();
  const [from, setFrom] = useState(`${today.slice(0, 8)}01`);
  const [to, setTo] = useState(today);
  const [group, setGroup] = useState("employee");
  const ot = useQuery({ queryKey: ["rep-ot", from, to, group], queryFn: () => api.get<{ rows: Record<string, string | number>[]; totals: Record<string, string> }>("/workforce/reports/overtime", { date_from: from, date_to: to, group_by: group }) });
  const att = useQuery({ queryKey: ["rep-att", from, to], queryFn: () => api.get<{ rows: (Record<string, number> & { employee: { full_name: string; employee_code: string }; worked_hours: string; attendance_pct: number | null })[] }>("/workforce/reports/attendance", { date_from: from, date_to: to }) });
  const wf = useQuery({ queryKey: ["rep-wf", from, to], queryFn: () => api.get<{ headcount: Record<string, string | number>[]; leave_days_by_type: { leave_type: string; days: string }[]; overtime_marketplace: Record<string, number | null>; active_employees: number }>("/workforce/reports/workforce", { date_from: from, date_to: to }) });
  const m = wf.data?.overtime_marketplace;
  return (
    <div className="space-y-4">
      <PageHeader title="Workforce reports" actions={<><Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-40" aria-label="From" /><Input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-40" aria-label="To" /></>} />
      <Tabs defaultValue="overtime">
        <TabsList><TabsTrigger value="overtime">Overtime</TabsTrigger><TabsTrigger value="attendance">Attendance</TabsTrigger><TabsTrigger value="workforce">Workforce & marketplace</TabsTrigger></TabsList>
        <TabsContent value="overtime" className="mt-4"><Card>
          <div className="flex flex-wrap items-center gap-3 border-b border-line p-3"><Select value={group} onChange={(e) => setGroup(e.target.value)} className="w-44" aria-label="Group by"><option value="employee">By employee</option><option value="department">By department</option></Select>
            {ot.data && <span className="text-sm text-fg-2">{ot.data.totals.payable_hours} h calculated · {ot.data.totals.approved_hours} h approved{ot.data.totals.amount ? ` · ${money(ot.data.totals.amount)}` : ""}</span>}</div>
          <DataTable rows={ot.data?.rows} loading={ot.isLoading} rowKey={(r) => String(r.key)} empty={<EmptyState title="No overtime in this period" />}
            columns={[{ key: "l", header: group === "employee" ? "Employee" : "Department", cell: (r) => r.label },
              { key: "w", header: "Worked h", align: "right", cell: (r) => r.worked_hours }, { key: "d", header: "Daily OT", align: "right", hideBelow: "md", cell: (r) => r.ot_daily_hours },
              { key: "wk", header: "Weekly OT", align: "right", hideBelow: "md", cell: (r) => r.ot_weekly_hours }, { key: "o", header: "Off-day / holiday", align: "right", hideBelow: "lg", cell: (r) => `${r.ot_weekly_off_hours} / ${r.ot_holiday_hours}` },
              { key: "p", header: "Overtime h", align: "right", cell: (r) => <b>{r.payable_hours}</b> }, { key: "a", header: "Approved h", align: "right", cell: (r) => r.approved_hours },
              ...(ot.data?.totals.amount !== undefined ? [{ key: "m", header: "Amount", align: "right" as const, cell: (r: Record<string, string | number>) => money(r.amount) }] : [])]} />
        </Card></TabsContent>
        <TabsContent value="attendance" className="mt-4"><Card>
          <DataTable rows={att.data?.rows} loading={att.isLoading} rowKey={(r) => r.employee.employee_code} empty={<EmptyState title="No attendance in this period" />}
            columns={[{ key: "e", header: "Employee", cell: (r) => r.employee.full_name }, { key: "s", header: "Shifts", align: "right", cell: (r) => r.shifts },
              { key: "p", header: "Present", align: "right", cell: (r) => (r.present ?? 0) + (r.late ?? 0) }, { key: "l", header: "Late", align: "right", cell: (r) => r.late ?? 0 },
              { key: "a", header: "Absent", align: "right", cell: (r) => r.absent ?? 0 }, { key: "lv", header: "Leave", align: "right", cell: (r) => r.on_leave ?? 0 },
              { key: "w", header: "Worked", align: "right", cell: (r) => `${r.worked_hours} h` }, { key: "pc", header: "Attendance", align: "right", cell: (r) => r.attendance_pct === null ? "—" : `${r.attendance_pct}%` }]} />
        </Card></TabsContent>
        <TabsContent value="workforce" className="mt-4"><div className="grid gap-4 lg:grid-cols-3">
          <Card><CardHeader title="Overtime marketplace" />{m && <dl className="grid grid-cols-2 gap-3 px-5 pb-5 text-sm">
            {[["Opportunities", m.total], ["Filled", m.filled], ["Expired unfilled", m.expired], ["Cancelled", m.cancelled], ["Filled by claim", m.by_marketplace], ["Assigned by manager", m.by_manager],
              ["Median time to fill", m.median_minutes_to_fill === null ? "—" : `${m.median_minutes_to_fill} min`]].map(([l, val]) => <div key={String(l)}><dt className="text-xs text-muted">{l}</dt><dd className="num font-semibold">{val}</dd></div>)}</dl>}</Card>
          <Card><CardHeader title="Headcount" description={`${wf.data?.active_employees ?? "—"} active`} /><ul className="divide-y divide-line">{wf.data?.headcount.map((h) => <li key={String(h.department)} className="flex justify-between px-5 py-2 text-sm"><span>{h.department}</span><span className="num">{h.active ?? 0} active / {h.total}</span></li>)}</ul></Card>
          <Card><CardHeader title="Leave taken" /><ul className="divide-y divide-line">{!wf.data?.leave_days_by_type.length && <li className="px-5 py-3 text-sm text-muted">None in this period.</li>}
            {wf.data?.leave_days_by_type.map((l) => <li key={l.leave_type} className="flex justify-between px-5 py-2 text-sm"><span>{l.leave_type}</span><span className="num">{Number(l.days)} days</span></li>)}</ul></Card>
        </div></TabsContent>
      </Tabs>
    </div>
  );
}
