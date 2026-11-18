import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { LineItems, linesValid, newLine, type LineBase } from "@/components/LineItems";
import { WarehouseSelect } from "@/components/pickers";
import { DataTable } from "@/components/ui/data-table";
import { ConfirmDialog, Dialog } from "@/components/ui/overlay";
import { DetailGrid, ErrorState, PageHeader, StatusBadge } from "@/components/ui/page";
import { Badge, Button, Card, CardHeader, Checkbox, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useDepartments, useSuppliers } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { date, dateTime, label, money, qty } from "@/lib/format";
import type { Page, PurchaseOrder, Requisition } from "@/types/api";

interface RLine extends LineBase { supplier: string; cost: string }
const makeLine = () => newLine<RLine>({ supplier: "", cost: "" });

function NewRequisitionDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const depts = useDepartments();
  const sups = useSuppliers();
  const [dept, setDept] = useState("");
  const [required, setRequired] = useState("");
  const [why, setWhy] = useState("");
  const [submit, setSubmit] = useState(true);
  const [lines, setLines] = useState<RLine[]>([makeLine()]);
  const create = useAction(() => api.post<Requisition>("/purchase-requisitions", {
    department_id: dept || null, required_date: required || null, justification: why || null, submit,
    items: lines.map((l) => ({ product_id: l.product!.id, quantity: l.quantity, preferred_supplier_id: l.supplier || null, estimated_unit_cost: l.cost || null })),
  }), { success: (r) => `${r.requisition_number} created`, invalidate: [["requisitions"]], onSuccess: (r) => { onClose(); navigate(`/purchasing/requisitions/${r.id}`); } });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title="New purchase requisition"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={create.isPending} disabled={!linesValid(lines)} onClick={() => create.mutate(undefined)}>{submit ? "Submit for approval" : "Save draft"}</Button></>}>
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Department" htmlFor="rd"><Select id="rd" value={dept} onChange={(e) => setDept(e.target.value)}><option value="">—</option>{depts.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select></Field>
          <Field label="Required by" htmlFor="rr"><Input id="rr" type="date" value={required} onChange={(e) => setRequired(e.target.value)} /></Field>
          <Field label="Justification" htmlFor="rj" className="sm:col-span-2"><Textarea id="rj" value={why} onChange={(e) => setWhy(e.target.value)} /></Field>
        </div>
        <LineItems lines={lines} onChange={setLines} makeLine={makeLine} extra={(l, update) => (
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="Preferred supplier" htmlFor={`rs-${l.key}`}><Select id={`rs-${l.key}`} value={l.supplier} onChange={(e) => update({ supplier: e.target.value })}><option value="">—</option>{sups.data?.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select></Field>
            <Field label="Estimated unit cost" htmlFor={`rc-${l.key}`} hint={l.product ? `Purchase price ${money(l.product.purchase_price)}` : undefined}><Input id={`rc-${l.key}`} inputMode="decimal" value={l.cost} onChange={(e) => update({ cost: e.target.value })} /></Field>
          </div>
        )} />
        <Checkbox label="Submit for approval immediately" checked={submit} onChange={(e) => setSubmit(e.target.checked)} />
      </div>
    </Dialog>
  );
}

export function RequisitionsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const list = useQuery({ queryKey: ["requisitions", status, page], queryFn: () => api.get<Page<Requisition>>("/purchase-requisitions", { status, page, page_size: 25 }), placeholderData: (p) => p });
  return (
    <div>
      <PageHeader title="Purchase requisitions" description="Internal purchase requests. Approved requisitions convert into purchase orders."
        actions={<Can permission="purchasing.requisition.create"><Button variant="primary" onClick={() => setCreating(true)}><Plus /> New requisition</Button></Can>} />
      <Card>
        <div className="border-b border-line p-3">
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="w-52" aria-label="Status">
            <option value="">All statuses</option>{["draft", "pending_approval", "approved", "rejected", "converted", "cancelled"].map((s) => <option key={s} value={s}>{label(s)}</option>)}
          </Select>
        </div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(r) => r.id}
          onRowClick={(r) => navigate(`/purchasing/requisitions/${r.id}`)} page={page} pageSize={25} total={list.data?.total} onPage={setPage}
          columns={[
            { key: "no", header: "Number", cell: (r) => <span className="font-mono text-xs">{r.requisition_number}</span> },
            { key: "items", header: "Items", cell: (r) => <span className="text-xs text-fg-2">{r.items.map((i) => `${qty(i.quantity)} ${i.uom_code} ${i.product_sku}`).join(", ")}</span> },
            { key: "by", header: "Requested by", hideBelow: "md", cell: (r) => <div className="text-xs"><div>{r.created_by_name}</div><div className="text-muted">{r.department_name ?? ""}</div></div> },
            { key: "need", header: "Required", hideBelow: "lg", cell: (r) => date(r.required_date) },
            { key: "est", header: "Est. total", align: "right", hideBelow: "lg", cell: (r) => <span className="num">{money(r.estimated_total)}</span> },
            { key: "src", header: "", hideBelow: "lg", cell: (r) => r.source === "ai" ? <Badge tone="accent">AI draft</Badge> : null },
            { key: "st", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
          ]} />
      </Card>
      {creating && <NewRequisitionDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

export function RequisitionDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { can, user } = useAuth();
  const sups = useSuppliers();
  const [dialog, setDialog] = useState<"reject" | "cancel" | "convert" | null>(null);
  const [conv, setConv] = useState({ supplier_id: "", delivery_warehouse_id: "", expected_delivery_date: "" });
  const r = useQuery({ queryKey: ["requisition", id], queryFn: () => api.get<Requisition>(`/purchase-requisitions/${id}`) });
  const act = useAction(({ action, body }: { action: string; body?: unknown }) => api.post<Requisition>(`/purchase-requisitions/${id}/${action}`, body),
    { success: (x) => `Requisition ${label(x.status).toLowerCase()}`, invalidate: [["requisition", id], ["requisitions"]], onSuccess: () => setDialog(null) });
  const convert = useAction(() => api.post<PurchaseOrder>(`/purchase-requisitions/${id}/convert`, { ...conv, expected_delivery_date: conv.expected_delivery_date || null }),
    { success: (po) => `Purchase order ${po.po_number} created`, invalidate: [["requisition", id], ["requisitions"], ["purchase-orders"]], onSuccess: (po) => navigate(`/purchasing/orders/${po.id}`) });
  if (r.error) return <ErrorState error={r.error} onRetry={r.refetch} />;
  if (!r.data) return <Skeleton className="h-64" />;
  const d = r.data;
  const owner = d.created_by === user?.id;
  return (
    <div className="space-y-4">
      <PageHeader breadcrumb={[{ label: "Purchase requisitions", to: "/purchasing/requisitions" }]}
        title={<span className="flex items-center gap-3">{d.requisition_number} <StatusBadge status={d.status} /></span>} description={d.justification ?? undefined}
        actions={<>
          {d.status === "draft" && (owner || can("purchasing.requisition.approve")) && <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "submit" })}>Submit</Button>}
          {d.status === "pending_approval" && can("purchasing.requisition.approve") && <>
            <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "approve" })}>Approve</Button>
            <Button onClick={() => setDialog("reject")}>Reject</Button></>}
          {d.status === "approved" && can("purchasing.po.create") && <Button variant="primary" onClick={() => setDialog("convert")}>Convert to PO</Button>}
          {["draft", "pending_approval", "approved"].includes(d.status) && (owner || can("purchasing.requisition.approve")) && <Button variant="ghost" onClick={() => setDialog("cancel")}>Cancel</Button>}
          {d.purchase_order_id && <Button onClick={() => navigate(`/purchasing/orders/${d.purchase_order_id}`)}>View purchase order</Button>}
        </>} />
      <Card><div className="p-5"><DetailGrid items={[
        { label: "Requested by", value: d.created_by_name }, { label: "Department", value: d.department_name }, { label: "Required by", value: date(d.required_date) },
        { label: "Estimated total", value: money(d.estimated_total) }, { label: "Created", value: dateTime(d.created_at) }, { label: "Decided", value: dateTime(d.approved_at) },
        { label: "Origin", value: d.source === "ai" ? "AI Command Center (approved draft)" : "Manual" }, { label: "Rejection reason", value: d.rejection_reason },
      ]} /></div></Card>
      <Card>
        <CardHeader title="Items" />
        <DataTable rows={d.items} rowKey={(i) => i.id} columns={[
          { key: "p", header: "Product", cell: (i) => <div><div>{i.product_name}</div><div className="font-mono text-xs text-muted">{i.product_sku}</div></div> },
          { key: "q", header: "Quantity", align: "right", cell: (i) => <span className="num">{qty(i.quantity)} <span className="text-xs text-muted">{i.uom_code}</span></span> },
          { key: "s", header: "Preferred supplier", cell: (i) => i.preferred_supplier_name ?? "—" },
          { key: "c", header: "Est. unit cost", align: "right", cell: (i) => <span className="num">{money(i.estimated_unit_cost)}</span> },
        ]} />
      </Card>
      <ConfirmDialog open={dialog === "reject"} onOpenChange={(o) => !o && setDialog(null)} title="Reject requisition" requireReason tone="danger" confirmLabel="Reject"
        loading={act.isPending} onConfirm={(reason) => act.mutate({ action: "reject", body: { reason } })} />
      <ConfirmDialog open={dialog === "cancel"} onOpenChange={(o) => !o && setDialog(null)} title="Cancel requisition" requireReason confirmLabel="Cancel requisition"
        loading={act.isPending} onConfirm={(reason) => act.mutate({ action: "cancel", body: { reason } })} />
      <Dialog open={dialog === "convert"} onOpenChange={(o) => !o && setDialog(null)} title="Convert to purchase order" description="Creates a draft PO with these items; review prices before submitting."
        footer={<><Button onClick={() => setDialog(null)}>Cancel</Button><Button variant="primary" loading={convert.isPending} disabled={!conv.supplier_id || !conv.delivery_warehouse_id} onClick={() => convert.mutate(undefined)}>Create PO</Button></>}>
        <div className="grid gap-3">
          <Field label="Supplier" htmlFor="cs" required><Select id="cs" value={conv.supplier_id} onChange={(e) => setConv({ ...conv, supplier_id: e.target.value })}><option value="">Select…</option>{sups.data?.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select></Field>
          <Field label="Deliver to" htmlFor="cw" required><WarehouseSelect id="cw" value={conv.delivery_warehouse_id} onChange={(v) => setConv({ ...conv, delivery_warehouse_id: v })} /></Field>
          <Field label="Expected delivery" htmlFor="ce"><Input id="ce" type="date" value={conv.expected_delivery_date} onChange={(e) => setConv({ ...conv, expected_delivery_date: e.target.value })} /></Field>
        </div>
      </Dialog>
    </div>
  );
}
