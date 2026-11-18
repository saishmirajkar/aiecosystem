import { useQuery } from "@tanstack/react-query";
import { PackageCheck, Plus } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AttachmentsPanel } from "@/components/Attachments";
import { LineItems, linesValid, newLine, type LineBase } from "@/components/LineItems";
import { WarehouseSelect } from "@/components/pickers";
import { DataTable } from "@/components/ui/data-table";
import { ConfirmDialog, Dialog } from "@/components/ui/overlay";
import { DetailGrid, ErrorState, PageHeader, SearchInput, StatusBadge } from "@/components/ui/page";
import { Badge, Button, Card, CardHeader, Checkbox, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useDebounced, useSuppliers } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { date, dateTime, label, money, qty } from "@/lib/format";
import type { GoodsReceipt, Page, PurchaseOrder } from "@/types/api";
import { GRNCreateDialog } from "./GoodsReceiptsPage";

interface POLine extends LineBase { price: string; tax: string }
const makeLine = () => newLine<POLine>({ price: "", tax: "18" });

function NewPODialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const sups = useSuppliers();
  const [f, setF] = useState({ supplier_id: "", delivery_warehouse_id: "", expected_delivery_date: "", supplier_reference: "", notes: "" });
  const [submit, setSubmit] = useState(true);
  const [lines, setLines] = useState<POLine[]>([makeLine()]);
  const create = useAction(() => api.post<PurchaseOrder>("/purchase-orders", {
    ...f, expected_delivery_date: f.expected_delivery_date || null, supplier_reference: f.supplier_reference || null, notes: f.notes || null, submit,
    items: lines.map((l) => ({ product_id: l.product!.id, quantity_ordered: l.quantity, unit_price: l.price || l.product!.purchase_price, tax_rate: l.tax || "0" })),
  }), { success: (po) => `${po.po_number} created`, invalidate: [["purchase-orders"]], onSuccess: (po) => { onClose(); navigate(`/purchasing/orders/${po.id}`); } });
  const total = lines.reduce((s, l) => s + Number(l.quantity || 0) * Number(l.price || l.product?.purchase_price || 0) * (1 + Number(l.tax || 0) / 100), 0);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title="New purchase order" description="A purchase order never changes stock — only a confirmed goods receipt does."
      footer={<><span className="mr-auto self-center text-sm text-muted">Total incl. tax <span className="num text-fg">{money(total)}</span></span>
        <Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={create.isPending} disabled={!f.supplier_id || !f.delivery_warehouse_id || !linesValid(lines)} onClick={() => create.mutate(undefined)}>{submit ? "Create & submit" : "Save draft"}</Button></>}>
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Supplier" htmlFor="ps" required><Select id="ps" value={f.supplier_id} onChange={(e) => setF({ ...f, supplier_id: e.target.value })}><option value="">Select…</option>{sups.data?.items.map((s) => <option key={s.id} value={s.id}>{s.code} — {s.name}</option>)}</Select></Field>
          <Field label="Deliver to" htmlFor="pw" required><WarehouseSelect id="pw" value={f.delivery_warehouse_id} onChange={(v) => setF({ ...f, delivery_warehouse_id: v })} /></Field>
          <Field label="Expected delivery" htmlFor="pe"><Input id="pe" type="date" value={f.expected_delivery_date} onChange={(e) => setF({ ...f, expected_delivery_date: e.target.value })} /></Field>
          <Field label="Supplier reference" htmlFor="pr"><Input id="pr" value={f.supplier_reference} onChange={(e) => setF({ ...f, supplier_reference: e.target.value })} /></Field>
        </div>
        <LineItems lines={lines} onChange={setLines} makeLine={makeLine} extra={(l, update) => (
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="Unit price" htmlFor={`pp-${l.key}`} hint={l.product ? `Default ${money(l.product.purchase_price)}` : undefined}><Input id={`pp-${l.key}`} inputMode="decimal" value={l.price} onChange={(e) => update({ price: e.target.value })} placeholder={l.product?.purchase_price} /></Field>
            <Field label="Tax %" htmlFor={`pt-${l.key}`}><Input id={`pt-${l.key}`} inputMode="decimal" value={l.tax} onChange={(e) => update({ tax: e.target.value })} /></Field>
          </div>
        )} />
        <Field label="Notes" htmlFor="pn"><Textarea id="pn" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        <Checkbox label="Submit for approval immediately" checked={submit} onChange={(e) => setSubmit(e.target.checked)} />
      </div>
    </Dialog>
  );
}

export function PurchaseOrdersPage() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [overdue, setOverdue] = useState(false);
  const [params] = useSearchParams();
  const [openOnly, setOpenOnly] = useState(params.get("open_for_receipt") === "1");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const dq = useDebounced(q);
  const list = useQuery({ queryKey: ["purchase-orders", dq, status, overdue, openOnly, page], queryFn: () => api.get<Page<PurchaseOrder>>("/purchase-orders", { q: dq, status, overdue, open_for_receipt: openOnly, page, page_size: 25 }), placeholderData: (p) => p });
  return (
    <div>
      <PageHeader title="Purchase orders" description="Draft → approval → sent → received. Partial deliveries are tracked line by line."
        actions={<Can permission="purchasing.po.create"><Button variant="primary" onClick={() => setCreating(true)}><Plus /> New purchase order</Button></Can>} />
      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          <SearchInput value={q} onChange={(v) => { setQ(v); setPage(1); }} placeholder="PO number or supplier…" className="min-w-56 flex-1" />
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="w-52" aria-label="Status">
            <option value="">All statuses</option>{["draft", "pending_approval", "approved", "sent", "partially_received", "fully_received", "closed", "cancelled"].map((s) => <option key={s} value={s}>{label(s)}</option>)}
          </Select>
          <Checkbox label="Awaiting delivery" checked={openOnly} onChange={(e) => { setOpenOnly(e.target.checked); setPage(1); }} />
          <Checkbox label="Overdue only" checked={overdue} onChange={(e) => { setOverdue(e.target.checked); setPage(1); }} />
        </div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(p) => p.id}
          onRowClick={(p) => navigate(`/purchasing/orders/${p.id}`)} page={page} pageSize={25} total={list.data?.total} onPage={setPage}
          columns={[
            { key: "no", header: "PO", cell: (p) => <span className="font-mono text-xs">{p.po_number}</span> },
            { key: "sup", header: "Supplier", cell: (p) => <div><div className="font-medium">{p.supplier_name}</div><div className="text-xs text-muted">to {p.delivery_warehouse_code}</div></div> },
            { key: "order", header: "Ordered", hideBelow: "md", cell: (p) => date(p.order_date) },
            { key: "exp", header: "Expected", cell: (p) => <span className={p.is_overdue ? "font-medium text-bad" : ""}>{date(p.expected_delivery_date)}{p.is_overdue && " · overdue"}</span> },
            { key: "total", header: "Total", align: "right", cell: (p) => <span className="num">{money(p.total_amount)}</span> },
            { key: "st", header: "Status", cell: (p) => <StatusBadge status={p.status} /> },
          ]} />
      </Card>
      {creating && <NewPODialog onClose={() => setCreating(false)} />}
    </div>
  );
}

export function PurchaseOrderDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { can } = useAuth();
  const [dialog, setDialog] = useState<"reject" | "cancel" | "close" | "receive" | null>(null);
  const po = useQuery({ queryKey: ["purchase-order", id], queryFn: () => api.get<PurchaseOrder>(`/purchase-orders/${id}`) });
  const grns = useQuery({ queryKey: ["goods-receipts", { po: id }], queryFn: () => api.get<Page<GoodsReceipt>>("/goods-receipts", { purchase_order_id: id, page_size: 50 }) });
  const act = useAction(({ action, body }: { action: string; body?: unknown }) => api.post<PurchaseOrder>(`/purchase-orders/${id}/${action}`, body),
    { success: (p) => `${p.po_number} is now ${label(p.status).toLowerCase()}`, invalidate: [["purchase-order", id], ["purchase-orders"], ["overview"]], onSuccess: () => setDialog(null) });
  if (po.error) return <ErrorState error={po.error} onRetry={po.refetch} />;
  if (!po.data) return <Skeleton className="h-64" />;
  const d = po.data;
  const receivable = ["approved", "sent", "partially_received"].includes(d.status);
  return (
    <div className="space-y-4">
      <PageHeader breadcrumb={[{ label: "Purchase orders", to: "/purchasing/orders" }]}
        title={<span className="flex items-center gap-3">{d.po_number} <StatusBadge status={d.status} />{d.is_overdue && <Badge tone="bad">Overdue</Badge>}</span>}
        description={`${d.supplier_name} · deliver to ${d.delivery_warehouse_code}`}
        actions={<>
          {d.status === "draft" && can("purchasing.po.create") && <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "submit" })}>Submit for approval</Button>}
          {d.status === "pending_approval" && can("purchasing.po.approve") && <><Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "approve" })}>Approve</Button><Button onClick={() => setDialog("reject")}>Reject</Button></>}
          {d.status === "approved" && can("purchasing.po.create") && <Button loading={act.isPending} onClick={() => act.mutate({ action: "send" })}>Mark as sent</Button>}
          {receivable && can("inventory.receive") && <Button variant="primary" onClick={() => setDialog("receive")}><PackageCheck /> Receive goods</Button>}
          {["sent", "partially_received", "fully_received"].includes(d.status) && can("purchasing.po.create") && <Button variant="ghost" onClick={() => setDialog("close")}>Close</Button>}
          {["draft", "pending_approval", "approved", "sent"].includes(d.status) && can("purchasing.po.create") && <Button variant="ghost" onClick={() => setDialog("cancel")}>Cancel</Button>}
        </>} />
      <Card><div className="p-5"><DetailGrid items={[
        { label: "Order date", value: date(d.order_date) }, { label: "Expected", value: date(d.expected_delivery_date) },
        { label: "Subtotal", value: money(d.subtotal) }, { label: "Tax", value: money(d.tax_amount) }, { label: "Total", value: <b>{money(d.total_amount)}</b> },
        { label: "Payment terms", value: d.payment_terms }, { label: "Supplier ref.", value: d.supplier_reference },
        { label: "Approved", value: dateTime(d.approved_at) }, { label: "Sent", value: dateTime(d.sent_at) },
        { label: "From requisition", value: d.requisition_numbers.join(", ") || "—" }, { label: "Rejection reason", value: d.rejection_reason },
        { label: "Notes", value: d.notes },
      ]} /></div></Card>
      <Card>
        <CardHeader title="Lines" description="Received includes damaged quantity taken into stock; rejected quantity is returned and stays outstanding." />
        <DataTable rows={d.items} rowKey={(i) => i.id} columns={[
          { key: "p", header: "Product", cell: (i) => <div><div>{i.product_name}</div><div className="font-mono text-xs text-muted">{i.product_sku}</div></div> },
          { key: "o", header: "Ordered", align: "right", cell: (i) => <span className="num">{qty(i.quantity_ordered)} {i.uom_code}</span> },
          { key: "r", header: "Received", align: "right", cell: (i) => <span className="num">{qty(i.quantity_received)}</span> },
          { key: "rj", header: "Rejected", align: "right", hideBelow: "md", cell: (i) => <span className="num">{qty(i.quantity_rejected)}</span> },
          { key: "out", header: "Outstanding", align: "right", cell: (i) => <span className="num font-medium">{qty(i.quantity_outstanding)}</span> },
          { key: "pr", header: "Unit price", align: "right", hideBelow: "md", cell: (i) => <span className="num">{money(i.unit_price)}</span> },
          { key: "t", header: "Line total", align: "right", cell: (i) => <span className="num">{money(i.line_total)}</span> },
        ]} />
      </Card>
      <Card>
        <CardHeader title="Goods receipts" />
        <DataTable dense rows={grns.data?.items} loading={grns.isLoading} rowKey={(g) => g.id} onRowClick={(g) => navigate(`/inventory/receipts/${g.id}`)}
          columns={[
            { key: "n", header: "GRN", cell: (g) => <span className="font-mono text-xs">{g.grn_number}</span> },
            { key: "d", header: "Received", cell: (g) => date(g.received_date) }, { key: "dn", header: "Delivery note", cell: (g) => g.delivery_note_number ?? "—" },
            { key: "v", header: "Value", align: "right", cell: (g) => <span className="num">{money(g.total_value)}</span> },
            { key: "s", header: "Status", cell: (g) => <StatusBadge status={g.status} /> },
          ]} />
      </Card>
      <AttachmentsPanel entityType="purchase_order" entityId={d.id} canUpload={can("purchasing.po.create")} title="Order documents" />
      <ConfirmDialog open={dialog === "reject"} onOpenChange={(o) => !o && setDialog(null)} title="Reject purchase order" description="Returns the PO to draft for correction." requireReason tone="danger" confirmLabel="Reject"
        loading={act.isPending} onConfirm={(reason) => act.mutate({ action: "reject", body: { reason } })} />
      <ConfirmDialog open={dialog === "cancel"} onOpenChange={(o) => !o && setDialog(null)} title="Cancel purchase order" requireReason tone="danger" confirmLabel="Cancel PO"
        loading={act.isPending} onConfirm={(reason) => act.mutate({ action: "cancel", body: { reason } })} />
      <ConfirmDialog open={dialog === "close"} onOpenChange={(o) => !o && setDialog(null)} title="Close purchase order" description="Outstanding quantities will no longer be expected." requireReason confirmLabel="Close PO"
        loading={act.isPending} onConfirm={(reason) => act.mutate({ action: "close", body: { reason } })} />
      {dialog === "receive" && <GRNCreateDialog po={d} onClose={() => setDialog(null)} />}
    </div>
  );
}
