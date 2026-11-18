import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AttachmentsPanel } from "@/components/Attachments";
import { LocationSelect } from "@/components/pickers";
import { DataTable } from "@/components/ui/data-table";
import { ConfirmDialog, Dialog } from "@/components/ui/overlay";
import { DetailGrid, EmptyState, ErrorState, PageHeader, SearchInput, StatusBadge } from "@/components/ui/page";
import { Button, Card, CardHeader, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { useAuth } from "@/hooks/auth";
import { useDebounced } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { toast } from "sonner";
import { api, ApiError, errorMessage } from "@/lib/api";
import { date, dateTime, label, money, qty, today } from "@/lib/format";
import { newIdempotencyKey } from "@/lib/utils";
import type { GoodsReceipt, Page, PurchaseOrder } from "@/types/api";

interface GLine { accepted: string; damaged: string; rejected: string; reason: string; location: string; batch: string; expiry: string; serials: string }

/** GRN workflow: choose PO lines, enter received/damaged/rejected quantities, delivery documentation → draft GRN. */
export function GRNCreateDialog({ po, onClose }: { po: PurchaseOrder; onClose: () => void }) {
  const navigate = useNavigate();
  const [header, setHeader] = useState({ received_date: today(), delivery_note_number: "", invoice_number: "", transporter: "", notes: "" });
  const [lines, setLines] = useState<Record<string, GLine>>(() => Object.fromEntries(po.items.filter((i) => Number(i.quantity_outstanding) > 0).map((i) => [i.id,
    { accepted: i.quantity_outstanding.replace(/\.?0+$/, ""), damaged: "", rejected: "", reason: "", location: "", batch: "", expiry: "", serials: "" }])));
  const set = (id: string, patch: Partial<GLine>) => setLines((ls) => ({ ...ls, [id]: { ...ls[id]!, ...patch } }));
  const items = po.items.filter((i) => lines[i.id]);
  const create = useAction(() => api.post<GoodsReceipt>("/goods-receipts", {
    purchase_order_id: po.id, ...Object.fromEntries(Object.entries(header).map(([k, v]) => [k, v || null])),
    items: items.map((i) => ({ i, l: lines[i.id]! })).filter(({ l }) => Number(l.accepted || 0) + Number(l.damaged || 0) + Number(l.rejected || 0) > 0)
      .map(({ i, l }) => ({ purchase_order_item_id: i.id, location_id: l.location || null, quantity_received: l.accepted || "0", quantity_damaged: l.damaged || "0",
        quantity_rejected: l.rejected || "0", rejection_reason: l.reason || null, batch: l.batch ? { batch_number: l.batch, expiry_date: l.expiry || null } : null,
        serial_numbers: l.serials ? l.serials.split(/[\s,;]+/).filter(Boolean) : null })),
  }), { success: (g) => `${g.grn_number} saved as draft — review and confirm to update stock`, invalidate: [["goods-receipts"], ["purchase-order", po.id]],
    onSuccess: (g) => { onClose(); navigate(`/inventory/receipts/${g.id}`); } });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="xl" title={`Receive goods · ${po.po_number}`} description={`${po.supplier_name} → ${po.delivery_warehouse_code}. Saving creates a draft GRN; stock changes only when you confirm it.`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={create.isPending} onClick={() => create.mutate(undefined)}>Save draft GRN</Button></>}>
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="Received date" htmlFor="gd"><Input id="gd" type="date" value={header.received_date} onChange={(e) => setHeader({ ...header, received_date: e.target.value })} /></Field>
          <Field label="Delivery note no." htmlFor="gdn"><Input id="gdn" value={header.delivery_note_number} onChange={(e) => setHeader({ ...header, delivery_note_number: e.target.value })} /></Field>
          <Field label="Invoice no." htmlFor="gin"><Input id="gin" value={header.invoice_number} onChange={(e) => setHeader({ ...header, invoice_number: e.target.value })} /></Field>
          <Field label="Transporter" htmlFor="gt"><Input id="gt" value={header.transporter} onChange={(e) => setHeader({ ...header, transporter: e.target.value })} /></Field>
        </div>
        {items.length === 0 ? <EmptyState title="Nothing outstanding on this order" /> : items.map((i) => {
          const l = lines[i.id]!;
          return (
            <div key={i.id} className="rounded-lg border border-line p-3">
              <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-sm font-medium">{i.product_name} <span className="font-mono text-xs text-muted">{i.product_sku}</span></span>
                <span className="text-xs text-muted">Outstanding <span className="num text-fg">{qty(i.quantity_outstanding)} {i.uom_code}</span> of {qty(i.quantity_ordered)}</span>
              </div>
              <div className="grid gap-2 sm:grid-cols-5">
                <Field label="Accepted" htmlFor={`ga-${i.id}`}><Input id={`ga-${i.id}`} inputMode="decimal" className="num" value={l.accepted} onChange={(e) => set(i.id, { accepted: e.target.value })} /></Field>
                <Field label="Damaged" htmlFor={`gdm-${i.id}`}><Input id={`gdm-${i.id}`} inputMode="decimal" className="num" value={l.damaged} onChange={(e) => set(i.id, { damaged: e.target.value })} /></Field>
                <Field label="Rejected" htmlFor={`gr-${i.id}`}><Input id={`gr-${i.id}`} inputMode="decimal" className="num" value={l.rejected} onChange={(e) => set(i.id, { rejected: e.target.value })} /></Field>
                <Field label="Put-away location" htmlFor={`gl-${i.id}`}><LocationSelect id={`gl-${i.id}`} warehouseId={po.delivery_warehouse_id} value={l.location} onChange={(v) => set(i.id, { location: v })} /></Field>
                <Field label="Batch (if tracked)" htmlFor={`gb-${i.id}`}><Input id={`gb-${i.id}`} value={l.batch} onChange={(e) => set(i.id, { batch: e.target.value })} /></Field>
                {Number(l.rejected) > 0 && <Field label="Rejection reason *" htmlFor={`grr-${i.id}`} className="sm:col-span-3"><Input id={`grr-${i.id}`} value={l.reason} onChange={(e) => set(i.id, { reason: e.target.value })} /></Field>}
                {l.batch && <Field label="Expiry" htmlFor={`ge-${i.id}`}><Input id={`ge-${i.id}`} type="date" value={l.expiry} onChange={(e) => set(i.id, { expiry: e.target.value })} /></Field>}
                <Field label="Serial numbers (if tracked)" htmlFor={`gs-${i.id}`} className="sm:col-span-5"><Input id={`gs-${i.id}`} className="font-mono text-xs" value={l.serials} onChange={(e) => set(i.id, { serials: e.target.value })} placeholder="Comma separated" /></Field>
              </div>
            </div>
          );
        })}
        <Field label="Notes" htmlFor="gn"><Textarea id="gn" value={header.notes} onChange={(e) => setHeader({ ...header, notes: e.target.value })} /></Field>
      </div>
    </Dialog>
  );
}

export function GoodsReceiptsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  const list = useQuery({ queryKey: ["goods-receipts", status, dq, page], queryFn: () => api.get<Page<GoodsReceipt>>("/goods-receipts", { status, q: dq, page, page_size: 25 }), placeholderData: (p) => p });
  return (
    <div>
      <PageHeader title="Goods receipts" description="Receipts against purchase orders. Start a receipt from the purchase order (Receive goods); confirm drafts here to post stock." />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <SearchInput value={q} onChange={(v) => { setQ(v); setPage(1); }} placeholder="GRN number…" className="w-56" />
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="w-44" aria-label="Status">
            <option value="">All statuses</option><option value="draft">Draft</option><option value="confirmed">Confirmed</option><option value="cancelled">Cancelled</option>
          </Select>
          <Button variant="ghost" onClick={() => navigate("/purchasing/orders?open_for_receipt=1")}>Find a PO to receive →</Button>
        </div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(g) => g.id}
          onRowClick={(g) => navigate(`/inventory/receipts/${g.id}`)} page={page} pageSize={25} total={list.data?.total} onPage={setPage}
          empty={<EmptyState title="No goods receipts" description="Open an approved purchase order and choose “Receive goods”." />}
          columns={[
            { key: "n", header: "GRN", cell: (g) => <span className="font-mono text-xs">{g.grn_number}</span> },
            { key: "po", header: "PO / supplier", cell: (g) => <div><div className="font-mono text-xs">{g.po_number}</div><div className="text-xs text-muted">{g.supplier_name}</div></div> },
            { key: "wh", header: "Warehouse", hideBelow: "md", cell: (g) => g.warehouse_code },
            { key: "d", header: "Received", cell: (g) => date(g.received_date) },
            { key: "dn", header: "Delivery note", hideBelow: "lg", cell: (g) => g.delivery_note_number ?? "—" },
            { key: "v", header: "Value", align: "right", cell: (g) => <span className="num">{money(g.total_value)}</span> },
            { key: "s", header: "Status", cell: (g) => <StatusBadge status={g.status} /> },
          ]} />
      </Card>
    </div>
  );
}

export function GoodsReceiptDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { can } = useAuth();
  const [dialog, setDialog] = useState<"over" | "cancel" | null>(null);
  const [overReason, setOverReason] = useState("");
  const [idemKey] = useState(newIdempotencyKey);
  const g = useQuery({ queryKey: ["goods-receipt", id], queryFn: () => api.get<GoodsReceipt>(`/goods-receipts/${id}`) });
  const inv = [["goods-receipt", id], ["goods-receipts"], ["purchase-order"], ["purchase-orders"], ["balances"], ["movements"], ["products"], ["overview"]];
  const confirm = useAction((reason: string | null) => api.post<GoodsReceipt>(`/goods-receipts/${id}/confirm`, { over_receipt_reason: reason }, `${idemKey}-${reason ?? ""}`),
    { success: (r) => `${r.grn_number} confirmed — stock updated`, invalidate: inv, onSuccess: () => setDialog(null),
      onError: (e) => { if (e instanceof ApiError && (e.code === "over_receipt" || e.details?.lines)) setDialog("over"); else toast.error(errorMessage(e)); } });
  const cancel = useAction((reason: string) => api.post<GoodsReceipt>(`/goods-receipts/${id}/cancel`, { reason }), { success: "Goods receipt cancelled", invalidate: inv, onSuccess: () => setDialog(null) });
  if (g.error) return <ErrorState error={g.error} onRetry={g.refetch} />;
  if (!g.data) return <Skeleton className="h-64" />;
  const d = g.data;
  return (
    <div className="space-y-4">
      <PageHeader breadcrumb={[{ label: "Goods receipts", to: "/inventory/receipts" }]} title={<span className="flex items-center gap-3">{d.grn_number} <StatusBadge status={d.status} /></span>}
        description={`${d.supplier_name} · ${d.po_number} · ${d.warehouse_code}`}
        actions={<>
          <Button variant="ghost" onClick={() => navigate(`/purchasing/orders/${d.purchase_order_id}`)}>Open PO</Button>
          {d.status === "draft" && can("inventory.receive") && <>
            <Button variant="primary" loading={confirm.isPending} onClick={() => confirm.mutate(null)}>Confirm receipt</Button>
            <Button onClick={() => setDialog("cancel")}>Cancel</Button></>}
        </>} />
      <Card><div className="p-5"><DetailGrid items={[
        { label: "Received date", value: date(d.received_date) }, { label: "Delivery note", value: d.delivery_note_number }, { label: "Invoice", value: d.invoice_number },
        { label: "Transporter", value: d.transporter }, { label: "Value (accepted + damaged)", value: money(d.total_value) }, { label: "Confirmed", value: dateTime(d.confirmed_at) },
        { label: "Over-receipt reason", value: d.over_receipt_reason }, { label: "Notes", value: d.notes },
      ]} /></div></Card>
      <Card>
        <CardHeader title="Lines" description="Accepted → unrestricted stock; damaged → damaged status; rejected is not received." />
        <DataTable rows={d.items} rowKey={(i) => i.id} columns={[
          { key: "p", header: "Product", cell: (i) => <div><div>{i.product_name}</div><div className="font-mono text-xs text-muted">{i.product_sku}{i.batch_number ? ` · ${i.batch_number}` : ""}</div></div> },
          { key: "l", header: "Location", cell: (i) => <span className="font-mono text-xs">{i.location_path}</span> },
          { key: "a", header: "Accepted", align: "right", cell: (i) => <span className="num">{qty(i.quantity_received)} {i.uom_code}</span> },
          { key: "d", header: "Damaged", align: "right", cell: (i) => <span className="num">{qty(i.quantity_damaged)}</span> },
          { key: "r", header: "Rejected", align: "right", cell: (i) => <span className="num">{qty(i.quantity_rejected)}</span> },
          { key: "c", header: "Unit cost", align: "right", hideBelow: "md", cell: (i) => <span className="num">{money(i.unit_cost)}</span> },
          { key: "rr", header: "Rejection reason", hideBelow: "lg", cell: (i) => <span className="text-xs">{i.rejection_reason ?? "—"}</span> },
        ]} />
      </Card>
      <AttachmentsPanel entityType="goods_receipt" entityId={d.id} canUpload={can("inventory.receive")} title="Delivery documentation"
        description="Delivery note, invoice, inspection photos. PDF or images up to 10 MB." />
      <Dialog open={dialog === "over"} onOpenChange={(o) => !o && setDialog(null)} title="Over-receipt requires authorisation"
        description="One or more lines exceed the outstanding purchase order quantity. An authorised user must record a reason."
        footer={<><Button onClick={() => setDialog(null)}>Cancel</Button><Button variant="primary" disabled={overReason.trim().length < 3 || !can("purchasing.over_receipt")} loading={confirm.isPending} onClick={() => confirm.mutate(overReason.trim())}>Confirm with exception</Button></>}>
        {can("purchasing.over_receipt") ? <Field label="Exception reason" htmlFor="or"><Textarea id="or" value={overReason} onChange={(e) => setOverReason(e.target.value)} /></Field>
          : <p className="text-sm text-bad">You don’t have the “{label("purchasing.over_receipt")}” permission. Reduce the quantities or ask a purchase manager.</p>}
      </Dialog>
      <ConfirmDialog open={dialog === "cancel"} onOpenChange={(o) => !o && setDialog(null)} title="Cancel goods receipt" requireReason confirmLabel="Cancel GRN" loading={cancel.isPending} onConfirm={(r) => cancel.mutate(r)} />
    </div>
  );
}
