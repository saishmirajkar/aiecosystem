import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/overlay";
import { PageHeader, SearchInput, StatusBadge } from "@/components/ui/page";
import { Button, Card, Checkbox, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useDebounced } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { api, ApiError } from "@/lib/api";
import type { Page, Supplier } from "@/types/api";

const FIELDS: [keyof Supplier, string, string?][] = [
  ["code", "Code"], ["name", "Name"], ["contact_person", "Contact person"], ["email", "Email", "email"], ["phone", "Phone"],
  ["city", "City"], ["country", "Country"], ["tax_id", "Tax ID / GSTIN"], ["payment_terms", "Payment terms"], ["currency_code", "Currency"],
  ["lead_time_days", "Lead time (days)", "number"],
];

function SupplierDialog({ supplier, onClose }: { supplier?: Supplier; onClose: () => void }) {
  const [f, setF] = useState<Record<string, string | boolean>>(() => {
    const init: Record<string, string | boolean> = { address: supplier?.address ?? "", notes: supplier?.notes ?? "", is_active: supplier?.is_active ?? true };
    for (const [k] of FIELDS) init[k] = supplier?.[k] != null ? String(supplier[k]) : "";
    return init;
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const save = useAction(() => {
    const body: Record<string, unknown> = {};
    for (const [k, , type] of FIELDS) body[k] = f[k] === "" ? null : type === "number" ? Number(f[k]) : f[k];
    body.address = f.address || null;
    body.notes = f.notes || null;
    if (supplier) { body.is_active = f.is_active; return api.patch<Supplier>(`/suppliers/${supplier.id}`, body); }
    return api.post<Supplier>("/suppliers", body);
  }, { success: supplier ? "Supplier updated" : "Supplier created", invalidate: [["suppliers"]], onSuccess: onClose,
    onError: (e) => { if (e instanceof ApiError) setErrors({ ...e.fieldErrors(), ...(e.details?.field ? { [String(e.details.field)]: e.message } : {}), form: e.message }); } });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title={supplier ? `Edit ${supplier.name}` : "New supplier"}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.code || !f.name} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        {FIELDS.map(([k, l, type]) => (
          <Field key={k} label={l} htmlFor={`s-${k}`} error={errors[k]} required={k === "code" || k === "name"}>
            <Input id={`s-${k}`} type={type ?? "text"} value={String(f[k] ?? "")} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
          </Field>
        ))}
        <Field label="Address" htmlFor="s-addr" className="sm:col-span-2"><Textarea id="s-addr" value={String(f.address)} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
        <Field label="Notes" htmlFor="s-notes" className="sm:col-span-2"><Textarea id="s-notes" value={String(f.notes)} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        {supplier && <Checkbox label="Active" checked={!!f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />}
        {errors.form && <p className="text-sm text-bad sm:col-span-2">{errors.form}</p>}
      </div>
    </Dialog>
  );
}

export function SuppliersPage() {
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const [active, setActive] = useState("true");
  const [page, setPage] = useState(1);
  const [dialog, setDialog] = useState<Supplier | "new" | null>(null);
  const dq = useDebounced(q);
  useEffect(() => setPage(1), [dq, active]);
  const list = useQuery({ queryKey: ["suppliers", dq, active, page], queryFn: () => api.get<Page<Supplier>>("/suppliers", { q: dq, is_active: active === "all" ? undefined : active, page, page_size: 25 }), placeholderData: (p) => p });
  return (
    <div>
      <PageHeader title="Suppliers" description="Vendor master referenced by products, purchase orders and goods receipts."
        actions={<Can permission="suppliers.manage"><Button variant="primary" onClick={() => setDialog("new")}><Plus /> New supplier</Button></Can>} />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <SearchInput value={q} onChange={setQ} placeholder="Name, code, contact…" className="min-w-56 flex-1" />
          <Select value={active} onChange={(e) => setActive(e.target.value)} className="w-32" aria-label="Status"><option value="true">Active</option><option value="false">Inactive</option><option value="all">All</option></Select>
        </div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(s) => s.id}
          onRowClick={can("suppliers.manage") ? (s) => setDialog(s) : undefined} page={page} pageSize={25} total={list.data?.total} onPage={setPage}
          columns={[
            { key: "code", header: "Code", cell: (s) => <span className="font-mono text-xs">{s.code}</span> },
            { key: "name", header: "Supplier", cell: (s) => <div><div className="font-medium">{s.name}</div><div className="text-xs text-muted">{[s.city, s.country].filter(Boolean).join(", ")}</div></div> },
            { key: "contact", header: "Contact", hideBelow: "md", cell: (s) => <div className="text-xs"><div>{s.contact_person ?? "—"}</div><div className="text-muted">{s.email ?? s.phone ?? ""}</div></div> },
            { key: "terms", header: "Terms", hideBelow: "lg", cell: (s) => <span className="text-xs">{s.payment_terms ?? "—"}</span> },
            { key: "lead", header: "Lead time", align: "right", hideBelow: "lg", cell: (s) => s.lead_time_days != null ? `${s.lead_time_days} d` : "—" },
            { key: "st", header: "Status", cell: (s) => <StatusBadge status={s.is_active ? "active" : "cancelled"} /> },
          ]} />
      </Card>
      {dialog && <SupplierDialog supplier={dialog === "new" ? undefined : dialog} onClose={() => setDialog(null)} />}
    </div>
  );
}
