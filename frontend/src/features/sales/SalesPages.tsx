import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2, Truck, Users } from "lucide-react";
import { useState } from "react";
import { ProductPicker, WarehouseSelect } from "@/components/pickers";
import { DataTable } from "@/components/ui/data-table";
import { ConfirmDialog, Dialog } from "@/components/ui/overlay";
import { DetailGrid, EmptyState, PageHeader, SearchInput, StatusBadge } from "@/components/ui/page";
import { Button, Card, Checkbox, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useDebounced } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { date, dateTime, qty, today } from "@/lib/format";
import { newIdempotencyKey } from "@/lib/utils";
import type { Page, Product } from "@/types/api";
import type { Customer, DispatchNote, OrderRow } from "@/types/production";
import { useCustomers } from "../production/OrdersPages";

// ---------------------------------------------------------------- customers
const FIELDS = [["contact_person", "Contact person"], ["phone", "Phone"], ["email", "Email"], ["city", "City"], ["gstin", "GSTIN"]] as const;

function CustomerDialog({ customer, onClose }: { customer: Customer | null; onClose: () => void }) {
  const [f, setF] = useState<Record<string, string | boolean>>(() => ({
    code: customer?.code ?? "", name: customer?.name ?? "", contact_person: customer?.contact_person ?? "", phone: customer?.phone ?? "",
    email: customer?.email ?? "", city: customer?.city ?? "", gstin: customer?.gstin ?? "", address: customer?.address ?? "", is_active: customer?.is_active ?? true,
  }));
  const val = (k: string) => String(f[k] ?? "");
  const body = () => {
    const b: Record<string, unknown> = { name: val("name") };
    for (const k of ["contact_person", "phone", "email", "city", "gstin", "address"]) b[k] = val(k) || null;
    return b;
  };
  const save = useAction(() => customer ? api.patch<Customer>(`/customers/${customer.id}`, { ...body(), is_active: !!f.is_active })
    : api.post<Customer>("/customers", { code: val("code"), ...body() }),
  { success: (c) => `${c.name} saved`, invalidate: [["customers"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={customer ? `Edit ${customer.name}` : "New customer"}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Code" htmlFor="cu-code" required><Input id="cu-code" value={val("code")} disabled={!!customer} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
        <Field label="Name" htmlFor="cu-name" required><Input id="cu-name" value={val("name")} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        {FIELDS.map(([key, lbl]) => <Field key={key} label={lbl} htmlFor={`cu-${key}`}><Input id={`cu-${key}`} value={val(key)} onChange={(e) => setF({ ...f, [key]: e.target.value })} /></Field>)}
        <Field label="Address" htmlFor="cu-addr" className="sm:col-span-2"><Textarea id="cu-addr" value={val("address")} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
        {customer && <Checkbox label="Active" checked={!!f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />}
      </div>
    </Dialog>
  );
}

export function CustomersPage() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<Customer | null | undefined>(undefined);
  const { can } = useAuth();
  const dq = useDebounced(q);
  const list = useQuery({ queryKey: ["customers", dq, page], queryFn: () => api.get<Page<Customer>>("/customers", { q: dq, page, page_size: 25 }), placeholderData: (p) => p });
  return (
    <div>
      <PageHeader title="Customers" description="Who finished goods are made for and dispatched to."
        actions={<Can permission="customers.manage"><Button variant="primary" onClick={() => setEditing(null)}><Plus /> New customer</Button></Can>} />
      <Card>
        <div className="border-b border-line p-3"><SearchInput value={q} onChange={(v) => { setQ(v); setPage(1); }} placeholder="Name or code…" className="max-w-sm" /></div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(c) => c.id}
          onRowClick={can("customers.manage") ? setEditing : undefined} page={page} pageSize={25} total={list.data?.total} onPage={setPage}
          empty={<EmptyState title="No customers" icon={<Users />} />}
          columns={[
            { key: "c", header: "Code", cell: (c) => <span className="font-mono text-xs">{c.code}</span> },
            { key: "n", header: "Name", cell: (c) => <div><div className="font-medium">{c.name}</div><div className="text-xs text-muted">{c.contact_person}</div></div> },
            { key: "ci", header: "City", hideBelow: "md", cell: (c) => c.city ?? "—" },
            { key: "g", header: "GSTIN", hideBelow: "lg", cell: (c) => <span className="font-mono text-xs">{c.gstin ?? "—"}</span> },
            { key: "s", header: "Status", cell: (c) => <StatusBadge status={c.is_active ? "active" : "inactive"} /> },
          ]} />
      </Card>
      {editing !== undefined && <CustomerDialog customer={editing} onClose={() => setEditing(undefined)} />}
    </div>
  );
}

// ---------------------------------------------------------------- dispatch
type Line = { key: string; product: Product | null; quantity: string };
let n = 0;

function NewDispatchDialog({ onClose }: { onClose: () => void }) {
  const customers = useCustomers();
  const orders = useQuery({ queryKey: ["production-orders", "completed-for-dispatch"], queryFn: () => api.get<Page<OrderRow>>("/production/orders", { status: "completed", page_size: 50 }) });
  const [f, setF] = useState({ customer_id: "", warehouse_id: "", order_id: "", dispatch_date: today(), vehicle_number: "", transporter: "", customer_reference: "", notes: "" });
  const [lines, setLines] = useState<Line[]>([{ key: `l${++n}`, product: null, quantity: "" }]);
  const save = useAction(() => api.post<DispatchNote>("/dispatch-notes", {
    ...f, order_id: f.order_id || null, vehicle_number: f.vehicle_number || null, transporter: f.transporter || null,
    customer_reference: f.customer_reference || null, notes: f.notes || null,
    lines: lines.filter((l) => l.product).map((l) => ({ product_id: l.product!.id, quantity: l.quantity })),
  }), { success: (d) => `${d.dispatch_number} drafted — dispatch it to post the stock out`, invalidate: [["dispatch-notes"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title="New dispatch note" description="Drafts don't move stock. Stock leaves the warehouse when you dispatch the note."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.customer_id || !f.warehouse_id || !lines.some((l) => l.product && Number(l.quantity) > 0)}
        onClick={() => save.mutate(undefined)}>Save draft</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Customer" htmlFor="dn-c" required><Select id="dn-c" value={f.customer_id} onChange={(e) => setF({ ...f, customer_id: e.target.value })}>
          <option value="">Select…</option>{customers.data?.items.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
        <Field label="Ship from" htmlFor="dn-w" required><WarehouseSelect id="dn-w" value={f.warehouse_id} onChange={(v) => setF({ ...f, warehouse_id: v })} /></Field>
        <Field label="Production order" htmlFor="dn-o"><Select id="dn-o" value={f.order_id} onChange={(e) => setF({ ...f, order_id: e.target.value })}>
          <option value="">—</option>{orders.data?.items.map((o) => <option key={o.id} value={o.id}>{o.order_number} · {o.product_name} ×{qty(o.quantity)}</option>)}</Select></Field>
        <Field label="Dispatch date" htmlFor="dn-d"><Input id="dn-d" type="date" value={f.dispatch_date} onChange={(e) => setF({ ...f, dispatch_date: e.target.value })} /></Field>
        <Field label="Vehicle no." htmlFor="dn-v"><Input id="dn-v" value={f.vehicle_number} onChange={(e) => setF({ ...f, vehicle_number: e.target.value })} /></Field>
        <Field label="Transporter" htmlFor="dn-t"><Input id="dn-t" value={f.transporter} onChange={(e) => setF({ ...f, transporter: e.target.value })} /></Field>
        <Field label="Customer PO / reference" htmlFor="dn-r" className="sm:col-span-2"><Input id="dn-r" value={f.customer_reference} onChange={(e) => setF({ ...f, customer_reference: e.target.value })} /></Field>
      </div>
      <h3 className="mb-2 mt-5 text-sm font-semibold">Items</h3>
      <div className="space-y-2">
        {lines.map((l, i) => (
          <div key={l.key} className="grid grid-cols-[1fr_120px_auto] items-center gap-2">
            <ProductPicker value={l.product} onChange={(p) => setLines(lines.map((x, j) => (j === i ? { ...x, product: p } : x)))} />
            <Input aria-label="Quantity" inputMode="decimal" value={l.quantity} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} />
            <Button variant="ghost" size="icon" aria-label="Remove line" disabled={lines.length === 1} onClick={() => setLines(lines.filter((_, j) => j !== i))}><Trash2 /></Button>
          </div>
        ))}
        <Button size="sm" variant="ghost" onClick={() => setLines([...lines, { key: `l${++n}`, product: null, quantity: "" }])}><Plus /> Add item</Button>
      </div>
      <Field label="Notes" htmlFor="dn-n" className="mt-3"><Textarea id="dn-n" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
    </Dialog>
  );
}

function DispatchDetail({ note, onClose }: { note: DispatchNote; onClose: () => void }) {
  const [key] = useState(newIdempotencyKey);
  const [cancelOpen, setCancelOpen] = useState(false);
  const { can } = useAuth();
  const inv = [["dispatch-notes"], ["balances"], ["products"], ["overview"]];
  const send = useAction(() => api.post<DispatchNote>(`/dispatch-notes/${note.id}/dispatch`, undefined, key), { success: (d) => `${d.dispatch_number} dispatched — stock posted out`, invalidate: inv, onSuccess: onClose });
  const cancel = useAction((reason: string) => api.post<DispatchNote>(`/dispatch-notes/${note.id}/cancel`, { reason }), { success: "Dispatch note cancelled", invalidate: inv, onSuccess: onClose });
  const draft = note.status === "draft" && can("dispatch.manage");
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title={`${note.dispatch_number} · ${note.customer_name}`}
      footer={draft ? <><Button variant="ghost" onClick={() => setCancelOpen(true)}>Cancel note</Button><Button variant="primary" loading={send.isPending} onClick={() => send.mutate(undefined)}><Truck /> Dispatch</Button></> : undefined}>
      <DetailGrid items={[
        { label: "Status", value: <StatusBadge status={note.status} /> }, { label: "Ship from", value: note.warehouse_code },
        { label: "Dispatch date", value: date(note.dispatch_date) }, { label: "Dispatched", value: dateTime(note.dispatched_at) },
        { label: "Vehicle", value: note.vehicle_number ?? "—" }, { label: "Transporter", value: note.transporter ?? "—" },
        { label: "Customer ref.", value: note.customer_reference ?? "—" },
      ]} />
      <table className="mt-4 w-full text-sm">
        <thead><tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-muted"><th className="py-2">Item</th><th className="py-2 text-right">Quantity</th></tr></thead>
        <tbody>{note.lines.map((l) => <tr key={l.id} className="border-b border-line"><td className="py-2"><div className="font-medium">{l.name}</div><div className="font-mono text-xs text-muted">{l.sku}</div></td><td className="num py-2 text-right">{qty(l.quantity)} {l.uom}</td></tr>)}</tbody>
      </table>
      {note.notes && <p className="mt-3 text-sm text-fg-2">{note.notes}</p>}
      <ConfirmDialog open={cancelOpen} onOpenChange={setCancelOpen} title="Cancel dispatch note" requireReason tone="danger" confirmLabel="Cancel note"
        loading={cancel.isPending} onConfirm={(r) => cancel.mutate(r)} />
    </Dialog>
  );
}

export function DispatchNotesPage() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<DispatchNote | null>(null);
  const list = useQuery({ queryKey: ["dispatch-notes", status, page], queryFn: () => api.get<Page<DispatchNote>>("/dispatch-notes", { status, page, page_size: 25 }), placeholderData: (p) => p });
  return (
    <div>
      <PageHeader title="Dispatch notes" description="Finished goods shipped to customers. Dispatching posts the stock out of the warehouse."
        actions={<Can permission="dispatch.manage"><Button variant="primary" onClick={() => setCreating(true)}><Plus /> New dispatch</Button></Can>} />
      <Card>
        <div className="border-b border-line p-3">
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="w-48" aria-label="Status">
            <option value="">All</option><option value="draft">Draft</option><option value="dispatched">Dispatched</option><option value="cancelled">Cancelled</option>
          </Select>
        </div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(d) => d.id}
          onRowClick={setOpen} page={page} pageSize={25} total={list.data?.total} onPage={setPage}
          empty={<EmptyState title="No dispatch notes" icon={<Truck />} />}
          columns={[
            { key: "n", header: "Dispatch", cell: (d) => <span className="font-mono text-xs">{d.dispatch_number}</span> },
            { key: "c", header: "Customer", cell: (d) => <span className="font-medium">{d.customer_name}</span> },
            { key: "i", header: "Items", cell: (d) => <span className="text-xs">{d.lines.map((l) => `${l.name} ×${qty(l.quantity)}`).join(", ")}</span> },
            { key: "w", header: "From", hideBelow: "md", cell: (d) => d.warehouse_code },
            { key: "d", header: "Date", hideBelow: "md", cell: (d) => <span className="text-xs text-muted">{date(d.dispatch_date)}</span> },
            { key: "s", header: "Status", cell: (d) => <StatusBadge status={d.status} /> },
          ]} />
      </Card>
      {creating && <NewDispatchDialog onClose={() => setCreating(false)} />}
      {open && <DispatchDetail note={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
