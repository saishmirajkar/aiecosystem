import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { DataTable } from "@/components/ui/data-table";
import { ConfirmDialog, Dialog } from "@/components/ui/overlay";
import { ErrorState, PageHeader, SearchInput, StatusBadge } from "@/components/ui/page";
import { Badge, Button, Card, CardHeader, Checkbox, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useCategories, useDebounced, useDepartments, useRoles, useUnits, useWarehouses } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { api, ApiError, errorMessage } from "@/lib/api";
import { dateTime, label, relative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AuditEntry, Company, Conversion, Department, Page, Permission, Role, Setting, User } from "@/types/api";

// ---------------------------------------------------------------- Company
const COMPANY_FIELDS: [keyof Company, string][] = [
  ["name", "Company name"], ["legal_name", "Legal name"], ["registration_number", "Registration no."], ["tax_id", "Tax ID / GSTIN"],
  ["industry", "Industry"], ["phone", "Phone"], ["email", "Email"], ["website", "Website"], ["address_line1", "Address line 1"],
  ["address_line2", "Address line 2"], ["city", "City"], ["state", "State"], ["postal_code", "Postal code"], ["country", "Country"],
  ["currency_code", "Currency (ISO)"], ["timezone", "Time zone (IANA)"], ["date_format", "Date format"], ["number_format", "Number locale"],
];

export function CompanyPage() {
  const { can, refresh } = useAuth();
  const company = useQuery({ queryKey: ["company"], queryFn: () => api.get<Company>("/company") });
  const [f, setF] = useState<Record<string, string>>({});
  const [fy, setFy] = useState(4);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (company.data) {
      setF(Object.fromEntries(COMPANY_FIELDS.map(([k]) => [k, String(company.data[k] ?? "")])));
      setFy(company.data.fiscal_year_start_month);
    }
  }, [company.data]);
  const save = useAction(() => api.patch<Company>("/company", { ...Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v === "" ? null : v])), fiscal_year_start_month: fy }),
    { success: "Company settings saved", invalidate: [["company"]], onSuccess: () => refresh() });
  const upload = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    try { await api.upload("/company/logo", form); toast.success("Logo updated"); company.refetch(); } catch (e) { toast.error(errorMessage(e)); }
  };
  if (company.error) return <ErrorState error={company.error} onRetry={company.refetch} />;
  if (!company.data) return <Skeleton className="h-96" />;
  const editable = can("company.manage");
  return (
    <div>
      <PageHeader title="Company" description="Identity and regional settings of this installation. Each customer deployment has exactly one company."
        actions={editable && <Button variant="primary" loading={save.isPending} onClick={() => save.mutate(undefined)}>Save changes</Button>} />
      <div className="grid gap-4 xl:grid-cols-[1fr_300px]">
        <Card className="p-5">
          <div className="grid gap-4 sm:grid-cols-2">
            {COMPANY_FIELDS.map(([k, l]) => (
              <Field key={k} label={l} htmlFor={`c-${k}`}><Input id={`c-${k}`} value={f[k] ?? ""} disabled={!editable} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></Field>
            ))}
            <Field label="Financial year starts" htmlFor="c-fy">
              <Select id="c-fy" value={fy} disabled={!editable} onChange={(e) => setFy(Number(e.target.value))}>
                {Array.from({ length: 12 }, (_, i) => <option key={i + 1} value={i + 1}>{new Date(2000, i, 1).toLocaleString("en", { month: "long" })}</option>)}
              </Select>
            </Field>
          </div>
        </Card>
        <Card className="h-fit p-5">
          <div className="text-sm font-medium">Logo</div>
          <div className="mt-3 grid h-32 place-items-center rounded-lg border border-dashed border-line bg-surface-2/50">
            {company.data.logo_attachment_id ? <img src={api.downloadUrl(`/attachments/${company.data.logo_attachment_id}`)} alt="Company logo" className="max-h-24 max-w-[80%] object-contain" /> : <span className="text-xs text-muted">No logo</span>}
          </div>
          {editable && <>
            <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            <Button className="mt-3 w-full" onClick={() => fileRef.current?.click()}><Upload /> Upload logo</Button>
            <p className="mt-2 text-xs text-muted">PNG, JPEG or WebP, up to 10 MB.</p>
          </>}
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Users
function UserDialog({ user, onClose }: { user?: User; onClose: () => void }) {
  const roles = useRoles();
  const depts = useDepartments();
  const whs = useWarehouses();
  const [f, setF] = useState({ email: user?.email ?? "", full_name: user?.full_name ?? "", password: "", phone: user?.phone ?? "", job_title: user?.job_title ?? "",
    role_ids: user?.roles.map((r) => r.id) ?? [], department_ids: user?.departments.map((d) => d.id) ?? [], primary_department_id: user?.primary_department_id ?? "",
    warehouse_ids: user?.warehouse_ids ?? [] });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const toggle = (k: "role_ids" | "department_ids" | "warehouse_ids", id: string) => setF((x) => ({ ...x, [k]: x[k].includes(id) ? x[k].filter((y) => y !== id) : [...x[k], id] }));
  const save = useAction(() => {
    const body = { full_name: f.full_name, phone: f.phone || null, job_title: f.job_title || null, role_ids: f.role_ids, department_ids: f.department_ids,
      primary_department_id: f.department_ids.includes(f.primary_department_id) ? f.primary_department_id : f.department_ids[0] ?? null, warehouse_ids: f.warehouse_ids };
    return user ? api.patch<User>(`/users/${user.id}`, body) : api.post<User>("/users", { ...body, email: f.email, password: f.password });
  }, { success: user ? "User updated" : "User created", invalidate: [["users"]], onSuccess: onClose,
    onError: (e) => { if (e instanceof ApiError) setErrors({ ...e.fieldErrors(), form: e.message }); } });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title={user ? `Edit ${user.full_name}` : "New user"}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.full_name || (!user && (!f.email || !f.password))} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Full name" htmlFor="u-n" required error={errors.full_name}><Input id="u-n" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
        <Field label="Email" htmlFor="u-e" required error={errors.email}><Input id="u-e" type="email" value={f.email} disabled={!!user} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        {!user && <Field label="Initial password" htmlFor="u-p" required error={errors.password} hint="≥10 chars; letters + numbers/symbols"><Input id="u-p" type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>}
        <Field label="Job title" htmlFor="u-j"><Input id="u-j" value={f.job_title} onChange={(e) => setF({ ...f, job_title: e.target.value })} /></Field>
        <Field label="Phone" htmlFor="u-ph"><Input id="u-ph" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
        <div className="sm:col-span-2">
          <div className="mb-1.5 text-[13px] font-medium text-fg-2">Roles</div>
          <div className="grid gap-1.5 sm:grid-cols-2">{roles.data?.map((r) => <Checkbox key={r.id} label={r.name} checked={f.role_ids.includes(r.id)} onChange={() => toggle("role_ids", r.id)} />)}</div>
        </div>
        <div>
          <div className="mb-1.5 text-[13px] font-medium text-fg-2">Departments</div>
          <div className="space-y-1.5">{depts.data?.map((d) => <Checkbox key={d.id} label={d.name} checked={f.department_ids.includes(d.id)} onChange={() => toggle("department_ids", d.id)} />)}</div>
          {f.department_ids.length > 1 && <Field label="Primary department" htmlFor="u-pd" className="mt-2"><Select id="u-pd" value={f.primary_department_id} onChange={(e) => setF({ ...f, primary_department_id: e.target.value })}>
            {depts.data?.filter((d) => f.department_ids.includes(d.id)).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select></Field>}
        </div>
        <div>
          <div className="mb-1.5 text-[13px] font-medium text-fg-2">Warehouse access</div>
          <p className="mb-1.5 text-xs text-muted">None selected = all warehouses.</p>
          <div className="space-y-1.5">{whs.data?.map((w) => <Checkbox key={w.id} label={`${w.code} — ${w.name}`} checked={f.warehouse_ids.includes(w.id)} onChange={() => toggle("warehouse_ids", w.id)} />)}</div>
        </div>
        {errors.form && <p className="text-sm text-bad sm:col-span-2">{errors.form}</p>}
      </div>
    </Dialog>
  );
}

export function UsersPage() {
  const { user: me, can } = useAuth();
  const [q, setQ] = useState("");
  const [dialog, setDialog] = useState<User | "new" | null>(null);
  const [pwFor, setPwFor] = useState<User | null>(null);
  const [pw, setPw] = useState("");
  const dq = useDebounced(q);
  const list = useQuery({ queryKey: ["users", dq], queryFn: () => api.get<Page<User>>("/users", { q: dq, page_size: 100 }) });
  const toggle = useAction((u: User) => api.post<User>(`/users/${u.id}/${u.is_active ? "deactivate" : "activate"}`), { success: (u) => `${u.full_name} ${u.is_active ? "activated" : "deactivated"}`, invalidate: [["users"]] });
  const setPassword = useAction(() => api.post(`/users/${pwFor!.id}/set-password`, { new_password: pw }), { success: "Password set; the user's sessions were signed out", onSuccess: () => { setPwFor(null); setPw(""); } });
  return (
    <div>
      <PageHeader title="Users" description="Accounts, roles, departments and warehouse scope."
        actions={<Can permission="users.manage"><Button variant="primary" onClick={() => setDialog("new")}><Plus /> New user</Button></Can>} />
      <Card>
        <div className="border-b border-line p-3"><SearchInput value={q} onChange={setQ} placeholder="Name or email…" className="max-w-sm" /></div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(u) => u.id}
          columns={[
            { key: "n", header: "User", cell: (u) => <div><div className="font-medium">{u.full_name} {u.id === me?.id && <Badge>You</Badge>}</div><div className="text-xs text-muted">{u.email}</div></div> },
            { key: "r", header: "Roles", cell: (u) => <div className="flex flex-wrap gap-1">{u.roles.map((r) => <Badge key={r.id} tone="neutral">{r.name}</Badge>)}</div> },
            { key: "d", header: "Departments", hideBelow: "md", cell: (u) => <span className="text-xs">{u.departments.map((d) => d.name).join(", ") || "—"}</span> },
            { key: "w", header: "Warehouses", hideBelow: "lg", cell: (u) => <span className="text-xs">{u.warehouse_ids.length ? `${u.warehouse_ids.length} assigned` : "All"}</span> },
            { key: "l", header: "Last login", hideBelow: "lg", cell: (u) => <span className="text-xs text-muted">{relative(u.last_login_at)}</span> },
            { key: "s", header: "Status", cell: (u) => <StatusBadge status={u.is_active ? "active" : "cancelled"} /> },
            { key: "a", header: "", align: "right", cell: (u) => can("users.manage") && (
              <div className="flex justify-end gap-1">
                <Button size="sm" variant="ghost" onClick={() => setDialog(u)}>Edit</Button>
                <Button size="sm" variant="ghost" onClick={() => setPwFor(u)} aria-label={`Set password for ${u.full_name}`}><KeyRound /></Button>
                {u.id !== me?.id && <Button size="sm" variant="ghost" onClick={() => toggle.mutate(u)}>{u.is_active ? "Deactivate" : "Activate"}</Button>}
              </div>
            ) },
          ]} />
      </Card>
      {dialog && <UserDialog user={dialog === "new" ? undefined : dialog} onClose={() => setDialog(null)} />}
      <ConfirmDialog open={!!pwFor} onOpenChange={(o) => { if (!o) { setPwFor(null); setPw(""); } }} title={`Set password for ${pwFor?.full_name}`} confirmLabel="Set password"
        loading={setPassword.isPending} onConfirm={() => setPassword.mutate(undefined)}>
        <Field label="New password" htmlFor="setpw" hint="The user will be signed out of all sessions."><Input id="setpw" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
      </ConfirmDialog>
    </div>
  );
}

// ---------------------------------------------------------------- Roles & permissions
export function RolesPage() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const roles = useRoles();
  const perms = useQuery({ queryKey: ["permissions"], queryFn: () => api.get<Permission[]>("/permissions"), staleTime: Infinity });
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);
  const [nr, setNr] = useState({ code: "", name: "", description: "" });
  const role = roles.data?.find((r) => r.id === selected) ?? roles.data?.[0];
  useEffect(() => { if (role) setDraft(new Set(role.permissions)); }, [role?.id, role?.permissions.join()]);
  const grouped = useMemo(() => {
    const g: Record<string, Permission[]> = {};
    for (const p of perms.data ?? []) (g[p.module] ??= []).push(p);
    return g;
  }, [perms.data]);
  const save = useAction(() => api.patch<Role>(`/roles/${role!.id}`, { permissions: [...draft] }), { success: "Role permissions saved", invalidate: [["roles"]], onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }) });
  const create = useAction(() => api.post<Role>("/roles", { ...nr, description: nr.description || null, permissions: [] }), { success: "Role created", invalidate: [["roles"]], onSuccess: (r) => { setCreating(false); setSelected(r.id); setNr({ code: "", name: "", description: "" }); } });
  const del = useAction(() => api.del(`/roles/${role!.id}`), { success: "Role deleted", invalidate: [["roles"]], onSuccess: () => setSelected(null) });
  const editable = can("roles.manage") && role?.code !== "super_admin";
  const dirty = role && (draft.size !== role.permissions.length || role.permissions.some((p) => !draft.has(p)));
  return (
    <div>
      <PageHeader title="Roles & permissions" description="Permissions are enforced by the backend on every request and every AI tool call."
        actions={<Can permission="roles.manage"><Button variant="primary" onClick={() => setCreating(true)}><Plus /> New role</Button></Can>} />
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <Card className="h-fit">
          {roles.isLoading ? <Skeleton className="m-3 h-40" /> : (
            <ul className="p-1.5">{roles.data?.map((r) => (
              <li key={r.id}><button type="button" onClick={() => setSelected(r.id)} className={cn("w-full rounded-lg px-3 py-2 text-left hover:bg-surface-2", r.id === role?.id && "bg-surface-2")}>
                <div className="flex items-center justify-between gap-2 text-[13px] font-medium">{r.name}{r.is_system && <Badge>System</Badge>}</div>
                <div className="text-[11px] text-muted">{r.permissions.length} permissions · {r.user_count} users</div>
              </button></li>
            ))}</ul>
          )}
        </Card>
        {role && (
          <Card>
            <CardHeader title={role.name} description={role.description ?? undefined} actions={<>
              {editable && !role.is_system && <Button size="sm" variant="ghost" onClick={() => del.mutate(undefined)} loading={del.isPending}>Delete role</Button>}
              {editable && <Button size="sm" variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate(undefined)}>Save permissions</Button>}
            </>} />
            {role.code === "super_admin" && <p className="border-b border-line bg-accent-soft/50 px-5 py-2 text-xs text-fg-2">The Super Administrator always holds every permission.</p>}
            <div className="grid gap-5 p-5 md:grid-cols-2">
              {Object.entries(grouped).map(([mod, ps]) => (
                <div key={mod}>
                  <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">{label(mod)}</div>
                  <div className="space-y-1.5">{ps.map((p) => (
                    <label key={p.code} className="flex cursor-pointer items-start gap-2 text-sm">
                      <input type="checkbox" className="mt-0.5 size-4 accent-[var(--accent)]" disabled={!editable} checked={draft.has(p.code)}
                        onChange={() => setDraft((d) => { const n = new Set(d); if (n.has(p.code)) n.delete(p.code); else n.add(p.code); return n; })} />
                      <span><span className="text-fg">{p.name}</span><span className="block font-mono text-[10.5px] text-muted">{p.code}</span></span>
                    </label>
                  ))}</div>
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
      <Dialog open={creating} onOpenChange={setCreating} title="New role" size="sm"
        footer={<><Button onClick={() => setCreating(false)}>Cancel</Button><Button variant="primary" loading={create.isPending} disabled={!nr.code || !nr.name} onClick={() => create.mutate(undefined)}>Create</Button></>}>
        <div className="space-y-3">
          <Field label="Code" htmlFor="rc" hint="lowercase_with_underscores"><Input id="rc" value={nr.code} onChange={(e) => setNr({ ...nr, code: e.target.value })} /></Field>
          <Field label="Name" htmlFor="rn"><Input id="rn" value={nr.name} onChange={(e) => setNr({ ...nr, name: e.target.value })} /></Field>
          <Field label="Description" htmlFor="rd"><Textarea id="rd" value={nr.description} onChange={(e) => setNr({ ...nr, description: e.target.value })} /></Field>
        </div>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------- Departments
export function DepartmentsPage() {
  const { can } = useAuth();
  const list = useQuery({ queryKey: ["departments", "all"], queryFn: () => api.get<Department[]>("/departments", { include_inactive: true }) });
  const [dialog, setDialog] = useState<Department | "new" | null>(null);
  const [f, setF] = useState({ code: "", name: "", description: "", is_active: true });
  useEffect(() => { if (dialog && dialog !== "new") setF({ code: dialog.code, name: dialog.name, description: dialog.description ?? "", is_active: dialog.is_active }); else if (dialog === "new") setF({ code: "", name: "", description: "", is_active: true }); }, [dialog]);
  const save = useAction(() => dialog === "new"
    ? api.post("/departments", { code: f.code.toUpperCase(), name: f.name, description: f.description || null })
    : api.patch(`/departments/${(dialog as Department).id}`, { name: f.name, description: f.description || null, is_active: f.is_active }),
  { success: "Department saved", invalidate: [["departments"]], onSuccess: () => setDialog(null) });
  return (
    <div>
      <PageHeader title="Departments" description="Organisational units for users. Only Inventory (with purchasing) has an operational system and AI agent in this release."
        actions={<Can permission="departments.manage"><Button variant="primary" onClick={() => setDialog("new")}><Plus /> New department</Button></Can>} />
      <Card>
        <DataTable rows={list.data} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(d) => d.id} onRowClick={can("departments.manage") ? (d) => setDialog(d) : undefined}
          columns={[
            { key: "c", header: "Code", cell: (d) => <span className="font-mono text-xs">{d.code}</span> },
            { key: "n", header: "Name", cell: (d) => d.name },
            { key: "desc", header: "Description", hideBelow: "md", cell: (d) => <span className="text-xs text-muted">{d.description ?? "—"}</span> },
            { key: "m", header: "Members", align: "right", cell: (d) => d.member_count },
            { key: "s", header: "Status", cell: (d) => <StatusBadge status={d.is_active ? "active" : "cancelled"} /> },
          ]} />
      </Card>
      <Dialog open={!!dialog} onOpenChange={(o) => !o && setDialog(null)} title={dialog === "new" ? "New department" : "Edit department"} size="sm"
        footer={<><Button onClick={() => setDialog(null)}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.code || !f.name} onClick={() => save.mutate(undefined)}>Save</Button></>}>
        <div className="space-y-3">
          <Field label="Code" htmlFor="dc"><Input id="dc" value={f.code} disabled={dialog !== "new"} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
          <Field label="Name" htmlFor="dn"><Input id="dn" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Description" htmlFor="dd"><Textarea id="dd" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
          {dialog !== "new" && <Checkbox label="Active" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />}
        </div>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------- Units, conversions, categories
export function UnitsPage() {
  const { can } = useAuth();
  const units = useUnits();
  const cats = useCategories();
  const conv = useQuery({ queryKey: ["conversions"], queryFn: () => api.get<Conversion[]>("/unit-conversions") });
  const [u, setU] = useState({ code: "", name: "", category: "count", decimals: "0" });
  const [c, setC] = useState({ from: "", to: "", factor: "" });
  const [cat, setCat] = useState({ code: "", name: "" });
  const addUnit = useAction(() => api.post("/units", { ...u, code: u.code.toUpperCase(), decimals: Number(u.decimals) }), { success: "Unit added", invalidate: [["units"]], onSuccess: () => setU({ code: "", name: "", category: "count", decimals: "0" }) });
  const addConv = useAction(() => api.post("/unit-conversions", { from_uom_id: c.from, to_uom_id: c.to, factor: c.factor }), { success: "Conversion added", invalidate: [["conversions"]], onSuccess: () => setC({ from: "", to: "", factor: "" }) });
  const addCat = useAction(() => api.post("/categories", cat), { success: "Category added", invalidate: [["categories"]], onSuccess: () => setCat({ code: "", name: "" }) });
  const manage = can("masterdata.manage");
  return (
    <div className="space-y-4">
      <PageHeader title="Units of measurement" description="Units, conversions (1 from-unit = factor × to-unit) and product categories." />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Units" />
          <DataTable dense rows={units.data} loading={units.isLoading} rowKey={(x) => x.id} columns={[
            { key: "c", header: "Code", cell: (x) => <span className="font-mono text-xs">{x.code}</span> }, { key: "n", header: "Name", cell: (x) => x.name },
            { key: "k", header: "Category", cell: (x) => label(x.category) }, { key: "d", header: "Decimals", align: "right", cell: (x) => x.decimals },
          ]} />
          {manage && <div className="grid gap-2 border-t border-line p-3 sm:grid-cols-[90px_1fr_130px_80px_auto]">
            <Input aria-label="Unit code" placeholder="Code" value={u.code} onChange={(e) => setU({ ...u, code: e.target.value })} />
            <Input aria-label="Unit name" placeholder="Name" value={u.name} onChange={(e) => setU({ ...u, name: e.target.value })} />
            <Select aria-label="Unit category" value={u.category} onChange={(e) => setU({ ...u, category: e.target.value })}>{["mass", "volume", "length", "area", "count", "packaging", "other"].map((k) => <option key={k} value={k}>{label(k)}</option>)}</Select>
            <Input aria-label="Decimals" type="number" min={0} max={4} value={u.decimals} onChange={(e) => setU({ ...u, decimals: e.target.value })} />
            <Button disabled={!u.code || !u.name} loading={addUnit.isPending} onClick={() => addUnit.mutate(undefined)}>Add</Button>
          </div>}
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Conversions" />
            <DataTable dense rows={conv.data} loading={conv.isLoading} rowKey={(x) => x.id} columns={[
              { key: "f", header: "Conversion", cell: (x) => <span className="num">1 {x.from_uom_code} = {Number(x.factor).toLocaleString()} {x.to_uom_code}</span> },
              { key: "p", header: "Scope", cell: (x) => x.product_id ? <Badge tone="accent">Product-specific</Badge> : <span className="text-xs text-muted">Global</span> },
            ]} />
            {manage && <div className="grid gap-2 border-t border-line p-3 sm:grid-cols-[1fr_1fr_100px_auto]">
              <Select aria-label="From unit" value={c.from} onChange={(e) => setC({ ...c, from: e.target.value })}><option value="">From…</option>{units.data?.map((x) => <option key={x.id} value={x.id}>{x.code}</option>)}</Select>
              <Select aria-label="To unit" value={c.to} onChange={(e) => setC({ ...c, to: e.target.value })}><option value="">To…</option>{units.data?.map((x) => <option key={x.id} value={x.id}>{x.code}</option>)}</Select>
              <Input aria-label="Factor" placeholder="Factor" inputMode="decimal" value={c.factor} onChange={(e) => setC({ ...c, factor: e.target.value })} />
              <Button disabled={!c.from || !c.to || !(Number(c.factor) > 0)} loading={addConv.isPending} onClick={() => addConv.mutate(undefined)}>Add</Button>
            </div>}
          </Card>
          <Card>
            <CardHeader title="Product categories" />
            <DataTable dense rows={cats.data} loading={cats.isLoading} rowKey={(x) => x.id} columns={[
              { key: "c", header: "Code", cell: (x) => <span className="font-mono text-xs">{x.code}</span> }, { key: "n", header: "Name", cell: (x) => x.name },
              { key: "p", header: "Products", align: "right", cell: (x) => x.product_count },
            ]} />
            {manage && <div className="grid gap-2 border-t border-line p-3 sm:grid-cols-[120px_1fr_auto]">
              <Input aria-label="Category code" placeholder="Code" value={cat.code} onChange={(e) => setCat({ ...cat, code: e.target.value })} />
              <Input aria-label="Category name" placeholder="Name" value={cat.name} onChange={(e) => setCat({ ...cat, name: e.target.value })} />
              <Button disabled={!cat.code || !cat.name} loading={addCat.isPending} onClick={() => addCat.mutate(undefined)}>Add</Button>
            </div>}
          </Card>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- System settings
export function SystemSettingsPage() {
  const { can } = useAuth();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api.get<Setting[]>("/settings") });
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<Record<string, string | null>>("/health") });
  const [values, setValues] = useState<Record<string, unknown>>({});
  useEffect(() => { if (settings.data) setValues(Object.fromEntries(settings.data.map((s) => [s.key, s.value]))); }, [settings.data]);
  const changed = Object.fromEntries(Object.entries(values).filter(([k, v]) => settings.data?.find((s) => s.key === k)?.value !== v));
  const save = useAction(() => api.patch<Setting[]>("/settings", { values: changed }), { success: "Settings saved", invalidate: [["settings"]] });
  if (settings.error) return <ErrorState error={settings.error} onRetry={settings.refetch} />;
  const editable = can("settings.manage");
  return (
    <div>
      <PageHeader title="System settings" description="Inventory policies, approval rules and AI behaviour for this installation."
        actions={editable && <Button variant="primary" disabled={!Object.keys(changed).length} loading={save.isPending} onClick={() => save.mutate(undefined)}>Save changes</Button>} />
      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <Card className="divide-y divide-line">
          {settings.isLoading ? <Skeleton className="m-4 h-64" /> : settings.data?.map((s) => (
            <div key={s.key} className="flex flex-col gap-2 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0"><div className="text-sm font-medium">{s.description}</div><div className="font-mono text-[11px] text-muted">{s.key}{s.updated_at && ` · updated ${relative(s.updated_at)}`}</div></div>
              <div className="w-full shrink-0 sm:w-48">
                {typeof s.value === "boolean" ? (
                  <Select aria-label={s.key} disabled={!editable} value={String(values[s.key])} onChange={(e) => setValues({ ...values, [s.key]: e.target.value === "true" })}><option value="true">Enabled</option><option value="false">Disabled</option></Select>
                ) : s.key === "inventory.valuation_method" ? (
                  <Select aria-label={s.key} disabled value={String(values[s.key])}><option value="weighted_average">Weighted average</option></Select>
                ) : (
                  <Input aria-label={s.key} disabled={!editable} inputMode="decimal" value={String(values[s.key] ?? "")}
                    onChange={(e) => setValues({ ...values, [s.key]: typeof s.value === "number" ? Number(e.target.value) : e.target.value })} />
                )}
              </div>
            </div>
          ))}
        </Card>
        <Card className="h-fit">
          <CardHeader title="Installation health" />
          <dl className="space-y-2.5 p-5 text-sm">
            {health.data && Object.entries(health.data).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2"><dt className="text-muted">{label(k)}</dt><dd className="font-mono text-xs">{v ?? "—"}</dd></div>
            ))}
          </dl>
          <p className="border-t border-line px-5 py-3 text-xs text-muted">AI engine “offline” means no Anthropic API key is configured; the Command Center answers recognised questions deterministically from live data.</p>
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Audit log
export function AuditLogPage() {
  const [action, setAction] = useState("");
  const [entity, setEntity] = useState("");
  const [page, setPage] = useState(1);
  const da = useDebounced(action);
  const list = useQuery({ queryKey: ["audit", da, entity, page], queryFn: () => api.get<Page<AuditEntry>>("/audit-logs", { action: da, entity_type: entity, page, page_size: 50 }), placeholderData: (p) => p });
  const [open, setOpen] = useState<AuditEntry | null>(null);
  return (
    <div>
      <PageHeader title="Audit log" description="Who did what, when, from where — including logins, permission changes, postings, approvals and AI actions." />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <SearchInput value={action} onChange={(v) => { setAction(v); setPage(1); }} placeholder="Action prefix, e.g. inventory.post" className="min-w-64 flex-1" />
          <Select value={entity} onChange={(e) => { setEntity(e.target.value); setPage(1); }} className="w-52" aria-label="Entity type">
            <option value="">All entities</option>{["user", "role", "company", "system_settings", "product", "warehouse", "inventory_transaction", "stock_transfer", "stock_adjustment", "purchase_order", "goods_receipt", "purchase_requisition", "action_approval"].map((x) => <option key={x} value={x}>{label(x)}</option>)}
          </Select>
        </div>
        <DataTable dense rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(x) => x.id} onRowClick={setOpen}
          page={page} pageSize={50} total={list.data?.total} onPage={setPage}
          columns={[
            { key: "t", header: "When", cell: (x) => <span className="text-xs">{dateTime(x.created_at)}</span> },
            { key: "a", header: "Action", cell: (x) => <span className="font-mono text-xs">{x.action}</span> },
            { key: "u", header: "Actor", cell: (x) => x.actor_name ?? <span className="text-muted">System / anonymous</span> },
            { key: "e", header: "Entity", hideBelow: "md", cell: (x) => <span className="text-xs">{x.entity_type ? label(x.entity_type) : "—"}</span> },
            { key: "ip", header: "IP", hideBelow: "lg", cell: (x) => <span className="font-mono text-xs text-muted">{x.ip_address ?? "—"}</span> },
          ]} />
      </Card>
      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)} title={open?.action ?? ""} description={open ? `${dateTime(open.created_at)} · ${open.actor_name ?? "system"}` : undefined} size="lg">
        <pre className="max-h-[60vh] overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-xs">{JSON.stringify(open?.changes ?? {}, null, 2)}</pre>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------- Profile
export function ProfilePage() {
  const { user, refresh } = useAuth();
  const [p, setP] = useState({ full_name: user?.full_name ?? "", phone: user?.phone ?? "", job_title: user?.job_title ?? "" });
  const [pw, setPw] = useState({ current: "", next: "" });
  const prefs = useQuery({ queryKey: ["prefs"], queryFn: () => api.get<{ alert_type: string; in_app: boolean; email: boolean }[]>("/notifications/preferences") });
  const [draft, setDraft] = useState<{ alert_type: string; in_app: boolean; email: boolean }[]>([]);
  useEffect(() => { if (prefs.data) setDraft(prefs.data); }, [prefs.data]);
  const saveProfile = useAction(() => api.patch("/auth/me", { full_name: p.full_name, phone: p.phone || null, job_title: p.job_title || null }), { success: "Profile updated", onSuccess: () => refresh() });
  const savePw = useAction(() => api.post("/auth/change-password", { current_password: pw.current, new_password: pw.next }), { success: "Password changed; other sessions signed out", onSuccess: () => setPw({ current: "", next: "" }) });
  const savePrefs = useAction(() => api.put("/notifications/preferences", draft), { success: "Notification preferences saved", invalidate: [["prefs"]] });
  return (
    <div className="space-y-4">
      <PageHeader title="Profile" description={`${user?.email} · ${user?.roles.map((r) => r.name).join(", ")}`} />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Your details" actions={<Button size="sm" variant="primary" loading={saveProfile.isPending} onClick={() => saveProfile.mutate(undefined)}>Save</Button>} />
          <div className="grid gap-3 p-5 sm:grid-cols-2">
            <Field label="Full name" htmlFor="pf-n"><Input id="pf-n" value={p.full_name} onChange={(e) => setP({ ...p, full_name: e.target.value })} /></Field>
            <Field label="Job title" htmlFor="pf-j"><Input id="pf-j" value={p.job_title} onChange={(e) => setP({ ...p, job_title: e.target.value })} /></Field>
            <Field label="Phone" htmlFor="pf-p"><Input id="pf-p" value={p.phone} onChange={(e) => setP({ ...p, phone: e.target.value })} /></Field>
          </div>
        </Card>
        <Card id="password">
          <CardHeader title="Change password" actions={<Button size="sm" variant="primary" disabled={!pw.current || !pw.next} loading={savePw.isPending} onClick={() => savePw.mutate(undefined)}>Update</Button>} />
          <div className="grid gap-3 p-5 sm:grid-cols-2">
            <Field label="Current password" htmlFor="pw-c"><Input id="pw-c" type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></Field>
            <Field label="New password" htmlFor="pw-n" hint="≥10 characters; letters + numbers/symbols"><Input id="pw-n" type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></Field>
          </div>
        </Card>
      </div>
      <Card>
        <CardHeader title="Notification preferences" description="Daily alert digests honour these choices. Email delivery activates once SMTP is configured." actions={<Button size="sm" variant="primary" loading={savePrefs.isPending} onClick={() => savePrefs.mutate(undefined)}>Save</Button>} />
        {prefs.isLoading ? <Skeleton className="m-4 h-32" /> : (
          <table className="w-full text-sm">
            <thead><tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-muted"><th className="px-5 py-2 font-medium">Alert</th><th className="px-5 py-2 font-medium">In-app</th><th className="px-5 py-2 font-medium">Email</th></tr></thead>
            <tbody>{draft.map((d, i) => (
              <tr key={d.alert_type} className="border-b border-line last:border-0">
                <td className="px-5 py-2">{label(d.alert_type)}</td>
                {(["in_app", "email"] as const).map((k) => (
                  <td key={k} className="px-5 py-2"><input type="checkbox" className="size-4 accent-[var(--accent)]" aria-label={`${label(d.alert_type)} ${k}`} checked={d[k]}
                    onChange={() => setDraft(draft.map((x, j) => (j === i ? { ...x, [k]: !x[k] } : x)))} /></td>
                ))}
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
