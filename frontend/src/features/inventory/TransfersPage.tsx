import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Plus } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { LineItems, linesValid, newLine, type LineBase } from "@/components/LineItems";
import { LocationSelect, WarehouseSelect } from "@/components/pickers";
import { DataTable } from "@/components/ui/data-table";
import { ConfirmDialog, Dialog } from "@/components/ui/overlay";
import { DetailGrid, ErrorState, PageHeader, SearchInput, StatusBadge } from "@/components/ui/page";
import { Badge, Button, Card, CardHeader, Field, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useDebounced } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { dateTime, qty } from "@/lib/format";
import { newIdempotencyKey } from "@/lib/utils";
import type { Page, Transfer } from "@/types/api";

interface TLine extends LineBase { source: string; dest: string }
const makeLine = () => newLine<TLine>({ source: "", dest: "" });

function NewTransferDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [src, setSrc] = useState("");
  const [dst, setDst] = useState("");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<TLine[]>([makeLine()]);
  const create = useAction(() => api.post<Transfer>("/inventory/transfers", {
    source_warehouse_id: src, destination_warehouse_id: dst, notes: notes || null,
    items: lines.map((l) => ({ product_id: l.product!.id, quantity: l.quantity, source_location_id: l.source || null, destination_location_id: l.dest || null })),
  }), { success: (t) => `Draft ${t.transfer_number} created`, invalidate: [["transfers"]], onSuccess: (t) => { onClose(); navigate(`/inventory/transfers/${t.id}`); } });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title="New stock transfer" description="Creates a draft. Submit → approve → dispatch (in transit) → receive at destination."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={create.isPending} disabled={!src || !dst || src === dst || !linesValid(lines)} onClick={() => create.mutate(undefined)}>Create draft</Button></>}>
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="From warehouse" htmlFor="ts" required><WarehouseSelect id="ts" value={src} onChange={setSrc} exclude={dst} /></Field>
          <Field label="To warehouse" htmlFor="td" required><WarehouseSelect id="td" value={dst} onChange={setDst} exclude={src} /></Field>
        </div>
        <LineItems lines={lines} onChange={setLines} makeLine={makeLine} extra={(l, update) => (
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="Source location" htmlFor={`sl-${l.key}`}><LocationSelect id={`sl-${l.key}`} warehouseId={src} value={l.source} onChange={(v) => update({ source: v })} /></Field>
            <Field label="Destination location" htmlFor={`dl-${l.key}`}><LocationSelect id={`dl-${l.key}`} warehouseId={dst} value={l.dest} onChange={(v) => update({ dest: v })} /></Field>
          </div>
        )} />
        <Field label="Notes" htmlFor="tn"><Textarea id="tn" value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
      </div>
    </Dialog>
  );
}

export function TransfersPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const dq = useDebounced(q);
  const list = useQuery({ queryKey: ["transfers", status, dq, page], queryFn: () => api.get<Page<Transfer>>("/inventory/transfers", { status, q: dq, page, page_size: 25 }), placeholderData: (p) => p });
  return (
    <div>
      <PageHeader title="Stock transfers" description="Inter-warehouse transfers. Dispatched stock is in transit and not available at the destination until received."
        actions={<Can permission="inventory.transfer"><Button variant="primary" onClick={() => setCreating(true)}><Plus /> New transfer</Button></Can>} />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <SearchInput value={q} onChange={setQ} placeholder="Transfer number…" className="w-56" />
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="w-48" aria-label="Status">
            <option value="">All statuses</option>
            {["draft", "pending_approval", "approved", "in_transit", "completed", "cancelled"].map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
          </Select>
        </div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(t) => t.id}
          onRowClick={(t) => navigate(`/inventory/transfers/${t.id}`)} page={page} pageSize={25} total={list.data?.total} onPage={setPage}
          columns={[
            { key: "no", header: "Transfer", cell: (t) => <span className="font-mono text-xs">{t.transfer_number}</span> },
            { key: "route", header: "Route", cell: (t) => <span className="inline-flex items-center gap-1.5">{t.source_warehouse_code} <ArrowRight className="size-3 text-muted" /> {t.destination_warehouse_code}</span> },
            { key: "items", header: "Lines", cell: (t) => <span className="text-xs text-fg-2">{t.items.map((i) => `${qty(i.quantity)} ${i.uom_code} ${i.product_sku}`).join(", ")}</span> },
            { key: "src", header: "Origin", hideBelow: "lg", cell: (t) => t.source === "ai" ? <Badge tone="accent">AI draft</Badge> : <span className="text-xs text-muted">Manual</span> },
            { key: "status", header: "Status", cell: (t) => <StatusBadge status={t.status} /> },
            { key: "date", header: "Created", hideBelow: "md", cell: (t) => <span className="text-xs text-muted">{dateTime(t.created_at)}</span> },
          ]} />
      </Card>
      {creating && <NewTransferDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

export function TransferDetailPage() {
  const { id } = useParams();
  const { can } = useAuth();
  const [cancelOpen, setCancelOpen] = useState(false);
  const [idemKey] = useState(newIdempotencyKey);
  const t = useQuery({ queryKey: ["transfer", id], queryFn: () => api.get<Transfer>(`/inventory/transfers/${id}`) });
  const inv = [["transfer", id], ["transfers"], ["balances"], ["movements"], ["products"], ["overview"]];
  const act = useAction(({ action, body }: { action: string; body?: unknown }) =>
    api.post<Transfer>(`/inventory/transfers/${id}/${action}`, body, action === "dispatch" || action === "receive" ? `${idemKey}-${action}` : undefined),
  { success: (r) => `Transfer is now ${r.status.replace(/_/g, " ")}`, invalidate: inv, onSuccess: () => setCancelOpen(false) });
  if (t.error) return <ErrorState error={t.error} onRetry={t.refetch} />;
  if (!t.data) return <Skeleton className="h-64" />;
  const d = t.data;
  return (
    <div className="space-y-4">
      <PageHeader breadcrumb={[{ label: "Stock transfers", to: "/inventory/transfers" }]}
        title={<span className="flex items-center gap-3">{d.transfer_number} <StatusBadge status={d.status} /></span>}
        description={`${d.source_warehouse_code} → ${d.destination_warehouse_code}`}
        actions={can("inventory.transfer") && <>
          {d.status === "draft" && <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "submit" })}>Submit</Button>}
          {d.status === "pending_approval" && can("inventory.transfer.approve") && <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "approve" })}>Approve</Button>}
          {d.status === "approved" && <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "dispatch" })}>Dispatch</Button>}
          {d.status === "in_transit" && <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "receive" })}>Confirm receipt</Button>}
          {["draft", "pending_approval", "approved"].includes(d.status) && <Button onClick={() => setCancelOpen(true)}>Cancel transfer</Button>}
        </>} />
      <Card><div className="p-5"><DetailGrid items={[
        { label: "Created", value: dateTime(d.created_at) }, { label: "Submitted", value: dateTime(d.submitted_at) },
        { label: "Approved", value: dateTime(d.approved_at) }, { label: "Dispatched", value: dateTime(d.dispatched_at) },
        { label: "Received", value: dateTime(d.received_at) }, { label: "Origin", value: d.source === "ai" ? "AI Command Center (approved draft)" : "Manual" },
        { label: "Notes", value: d.notes }, { label: "Cancel reason", value: d.cancel_reason },
      ]} /></div></Card>
      <Card>
        <CardHeader title="Lines" />
        <DataTable rows={d.items} rowKey={(i) => i.id} columns={[
          { key: "p", header: "Product", cell: (i) => <div><div>{i.product_name}</div><div className="font-mono text-xs text-muted">{i.product_sku}{i.batch_number ? ` · ${i.batch_number}` : ""}</div></div> },
          { key: "from", header: "From", cell: (i) => <span className="font-mono text-xs">{d.source_warehouse_code}/{i.source_location_path}</span> },
          { key: "to", header: "To", cell: (i) => <span className="font-mono text-xs">{d.destination_warehouse_code}/{i.destination_location_path}</span> },
          { key: "q", header: "Quantity", align: "right", cell: (i) => <span className="num">{qty(i.quantity)} <span className="text-xs text-muted">{i.uom_code}</span></span> },
        ]} />
      </Card>
      <ConfirmDialog open={cancelOpen} onOpenChange={setCancelOpen} title="Cancel transfer" requireReason tone="danger" confirmLabel="Cancel transfer"
        loading={act.isPending} onConfirm={(reason) => act.mutate({ action: "cancel", body: { reason } })} />
    </div>
  );
}
