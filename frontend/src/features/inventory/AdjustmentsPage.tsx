import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { LineItems, newLine, type LineBase } from "@/components/LineItems";
import { LocationSelect, WarehouseSelect } from "@/components/pickers";
import { DataTable } from "@/components/ui/data-table";
import { ConfirmDialog, Dialog } from "@/components/ui/overlay";
import { DetailGrid, ErrorState, PageHeader, StatusBadge } from "@/components/ui/page";
import { Badge, Button, Card, CardHeader, Checkbox, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { dateTime, label, money, qty } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Adjustment, Page } from "@/types/api";

interface ALine extends LineBase { location: string; status: string; unitCost: string }
const makeLine = () => newLine<ALine>({ location: "", status: "unrestricted", unitCost: "" });

function NewAdjustmentDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [type, setType] = useState("adjustment");
  const [warehouse, setWarehouse] = useState("");
  const [reason, setReason] = useState("");
  const [submit, setSubmit] = useState(true);
  const [lines, setLines] = useState<ALine[]>([makeLine()]);
  const isCount = type === "reconciliation";
  const create = useAction(() => api.post<Adjustment>("/inventory/adjustments", {
    adjustment_type: type, warehouse_id: warehouse, reason, submit,
    items: lines.map((l) => ({ product_id: l.product!.id, location_id: l.location, stock_status: l.status,
      ...(isCount ? { counted_quantity: l.quantity } : { quantity_delta: type === "write_off" ? `-${String(l.quantity).replace(/^-/, "")}` : l.quantity }),
      unit_cost: l.unitCost || null })),
  }), { success: (a) => `${a.adjustment_number} ${a.status === "pending_approval" ? "submitted for approval" : "saved as draft"}`, invalidate: [["adjustments"]],
    onSuccess: (a) => { onClose(); navigate(`/inventory/adjustments/${a.id}`); } });
  const valid = !!warehouse && reason.trim().length >= 3 && lines.every((l) => l.product && l.location && l.quantity !== "" && (isCount ? Number(l.quantity) >= 0 : Number(l.quantity) !== 0));
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title="New adjustment or count"
      description="Stock only changes after an authorised approver approves. Counts record the system quantity at count time."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={create.isPending} disabled={!valid} onClick={() => create.mutate(undefined)}>{submit ? "Submit for approval" : "Save draft"}</Button></>}>
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Type" htmlFor="at"><Select id="at" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="adjustment">Adjustment (+/−)</option><option value="reconciliation">Cycle count / reconciliation</option><option value="write_off">Write-off</option>
          </Select></Field>
          <Field label="Warehouse" htmlFor="aw" required><WarehouseSelect id="aw" value={warehouse} onChange={(v) => { setWarehouse(v); setLines((ls) => ls.map((l) => ({ ...l, location: "" }))); }} /></Field>
          <Field label="Reason" htmlFor="ar" required className="sm:col-span-2"><Textarea id="ar" value={reason} onChange={(e) => setReason(e.target.value)} className="min-h-[52px]" /></Field>
        </div>
        <LineItems lines={lines} onChange={setLines} makeLine={makeLine} quantityLabel={isCount ? "Counted qty" : type === "write_off" ? "Write-off qty" : "Change (+/−)"} extra={(l, update) => (
          <div className="grid gap-2 sm:grid-cols-3">
            <Field label="Location *" htmlFor={`al-${l.key}`}><LocationSelect id={`al-${l.key}`} warehouseId={warehouse} value={l.location} onChange={(v) => update({ location: v })} placeholder="Select location" /></Field>
            <Field label="Stock status" htmlFor={`as-${l.key}`}><Select id={`as-${l.key}`} value={l.status} onChange={(e) => update({ status: e.target.value })}><option value="unrestricted">Unrestricted</option><option value="damaged">Damaged</option><option value="returned">Returned</option></Select></Field>
            {type === "adjustment" && <Field label="Unit cost (increases)" htmlFor={`ac-${l.key}`} hint="Blank = current average"><Input id={`ac-${l.key}`} inputMode="decimal" value={l.unitCost} onChange={(e) => update({ unitCost: e.target.value })} /></Field>}
          </div>
        )} />
        <Checkbox label="Submit for approval immediately" checked={submit} onChange={(e) => setSubmit(e.target.checked)} />
      </div>
    </Dialog>
  );
}

export function AdjustmentsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const list = useQuery({ queryKey: ["adjustments", status, type, page], queryFn: () => api.get<Page<Adjustment>>("/inventory/adjustments", { status, adjustment_type: type, page, page_size: 25 }), placeholderData: (p) => p });
  return (
    <div>
      <PageHeader title="Adjustments & counts" description="Controlled corrections and stock reconciliation. Every posting requires approval."
        actions={<Can permission="inventory.adjust"><Button variant="primary" onClick={() => setCreating(true)}><Plus /> New adjustment</Button></Can>} />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="w-48" aria-label="Status">
            <option value="">All statuses</option>{["draft", "pending_approval", "posted", "rejected", "cancelled"].map((s) => <option key={s} value={s}>{label(s)}</option>)}
          </Select>
          <Select value={type} onChange={(e) => { setType(e.target.value); setPage(1); }} className="w-48" aria-label="Type">
            <option value="">All types</option><option value="adjustment">Adjustment</option><option value="reconciliation">Reconciliation</option><option value="write_off">Write-off</option>
          </Select>
        </div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(a) => a.id}
          onRowClick={(a) => navigate(`/inventory/adjustments/${a.id}`)} page={page} pageSize={25} total={list.data?.total} onPage={setPage}
          columns={[
            { key: "no", header: "Number", cell: (a) => <span className="font-mono text-xs">{a.adjustment_number}</span> },
            { key: "type", header: "Type", cell: (a) => label(a.adjustment_type) },
            { key: "wh", header: "Warehouse", cell: (a) => a.warehouse_code },
            { key: "reason", header: "Reason", hideBelow: "md", cell: (a) => <span className="line-clamp-1 text-xs text-fg-2">{a.reason}</span> },
            { key: "lines", header: "Lines", align: "right", cell: (a) => a.items.length },
            { key: "value", header: "Est. value", align: "right", hideBelow: "lg", cell: (a) => <span className={cn("num", Number(a.estimated_value) < 0 && "text-bad")}>{money(a.estimated_value)}</span> },
            { key: "src", header: "", hideBelow: "lg", cell: (a) => a.source === "ai" ? <Badge tone="accent">AI draft</Badge> : null },
            { key: "status", header: "Status", cell: (a) => <StatusBadge status={a.status} /> },
          ]} />
      </Card>
      {creating && <NewAdjustmentDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

export function AdjustmentDetailPage() {
  const { id } = useParams();
  const { can, user } = useAuth();
  const [dialog, setDialog] = useState<"reject" | "cancel" | null>(null);
  const a = useQuery({ queryKey: ["adjustment", id], queryFn: () => api.get<Adjustment>(`/inventory/adjustments/${id}`) });
  const act = useAction(({ action, body }: { action: string; body?: unknown }) => api.post<Adjustment>(`/inventory/adjustments/${id}/${action}`, body),
    { success: (r) => `Adjustment ${label(r.status).toLowerCase()}`, invalidate: [["adjustment", id], ["adjustments"], ["balances"], ["movements"], ["products"], ["overview"]], onSuccess: () => setDialog(null) });
  if (a.error) return <ErrorState error={a.error} onRetry={a.refetch} />;
  if (!a.data) return <Skeleton className="h-64" />;
  const d = a.data;
  const isCount = d.adjustment_type === "reconciliation";
  return (
    <div className="space-y-4">
      <PageHeader breadcrumb={[{ label: "Adjustments & counts", to: "/inventory/adjustments" }]}
        title={<span className="flex items-center gap-3">{d.adjustment_number} <StatusBadge status={d.status} /></span>} description={`${label(d.adjustment_type)} · ${d.warehouse_code}`}
        actions={<>
          {d.status === "draft" && can("inventory.adjust") && <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "submit" })}>Submit for approval</Button>}
          {d.status === "pending_approval" && can("inventory.adjust.approve") && <>
            <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "approve" })}>Approve & post</Button>
            <Button onClick={() => setDialog("reject")}>Reject</Button>
          </>}
          {["draft", "pending_approval"].includes(d.status) && (d.created_by === user?.id || can("inventory.adjust.approve")) && <Button variant="ghost" onClick={() => setDialog("cancel")}>Cancel</Button>}
        </>} />
      <Card><div className="p-5"><DetailGrid items={[
        { label: "Reason", value: d.reason }, { label: "Estimated value", value: money(d.estimated_value) },
        { label: "Created", value: dateTime(d.created_at) }, { label: "Submitted", value: dateTime(d.submitted_at) },
        { label: "Decided", value: dateTime(d.approved_at) }, { label: "Rejection / cancel reason", value: d.rejection_reason },
        { label: "Origin", value: d.source === "ai" ? "AI Command Center (approved draft)" : "Manual" },
        { label: "Ledger transaction", value: d.transaction_id ? "Posted" : "Not posted" },
      ]} /></div></Card>
      <Card>
        <CardHeader title="Lines" description={isCount ? "System quantity captured at count time; variance = counted − system." : undefined} />
        <DataTable rows={d.items} rowKey={(i) => i.id} columns={[
          { key: "p", header: "Product", cell: (i) => <div><div>{i.product_name}</div><div className="font-mono text-xs text-muted">{i.product_sku}</div></div> },
          { key: "l", header: "Location", cell: (i) => <span className="font-mono text-xs">{i.location_path}</span> },
          { key: "s", header: "Status", cell: (i) => <StatusBadge status={i.stock_status} /> },
          { key: "sys", header: "System", align: "right", cell: (i) => <span className="num">{qty(i.system_quantity)}</span> },
          ...(isCount ? [{ key: "c", header: "Counted", align: "right" as const, cell: (i: Adjustment["items"][number]) => <span className="num">{qty(i.counted_quantity)}</span> }] : []),
          { key: "d", header: isCount ? "Variance" : "Change", align: "right", cell: (i) => <span className={cn("num font-medium", Number(i.quantity_delta) < 0 ? "text-bad" : Number(i.quantity_delta) > 0 ? "text-ok" : "")}>{Number(i.quantity_delta) > 0 ? "+" : ""}{qty(i.quantity_delta)} {i.uom_code}</span> },
        ]} />
      </Card>
      <ConfirmDialog open={dialog === "reject"} onOpenChange={(o) => !o && setDialog(null)} title="Reject adjustment" requireReason tone="danger" confirmLabel="Reject"
        loading={act.isPending} onConfirm={(reason) => act.mutate({ action: "reject", body: { reason } })} />
      <ConfirmDialog open={dialog === "cancel"} onOpenChange={(o) => !o && setDialog(null)} title="Cancel adjustment" requireReason confirmLabel="Cancel adjustment"
        loading={act.isPending} onConfirm={(reason) => act.mutate({ action: "cancel", body: { reason } })} />
    </div>
  );
}
