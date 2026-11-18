import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Factory, MapPin, Pencil, Plus, Send, Trash2, Truck, Undo2, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { DataTable } from "@/components/ui/data-table";
import { ConfirmDialog, Dialog } from "@/components/ui/overlay";
import { DetailGrid, EmptyState, ErrorState, PageHeader, SearchInput, StatusBadge } from "@/components/ui/page";
import { Badge, Button, Card, CardHeader, Checkbox, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useDebounced } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { date, dateTime, label, money, qty, relative, today } from "@/lib/format";
import { cn, newIdempotencyKey } from "@/lib/utils";
import type { Page } from "@/types/api";
import type { Customer, OrderPart, OrderRow, OrderStage, Position, ProductionOrder, Site, StructureRow } from "@/types/production";

export const useSites = (kind?: "plant" | "vendor") =>
  useQuery({ queryKey: ["production-sites", kind], queryFn: () => api.get<Site[]>("/production/sites", { kind }), staleTime: 60_000 });
export const useCustomers = () =>
  useQuery({ queryKey: ["customers", "all"], queryFn: () => api.get<Page<Customer>>("/customers", { page_size: 200 }), staleTime: 60_000 });

const short = (name: string) => name.replace(" (job work)", "");

// ---------------------------------------------------------------- list
function NewOrderDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const structures = useQuery({ queryKey: ["structures"], queryFn: () => api.get<StructureRow[]>("/production/structures") });
  const plants = useSites("plant");
  const customers = useCustomers();
  const [f, setF] = useState({ product_id: "", quantity: "", plant_id: "", customer_id: "", start_date: today(), due_date: "", notes: "" });
  const create = useAction(() => api.post<ProductionOrder>("/production/orders", {
    ...f, customer_id: f.customer_id || null, due_date: f.due_date || null, notes: f.notes || null,
  }), { success: (o) => `${o.order_number} planned — review the route, then release it`, invalidate: [["production-orders"]],
    onSuccess: (o) => { onClose(); navigate(`/production/orders/${o.id}`); } });
  const plantId = f.plant_id || plants.data?.[0]?.warehouse_id || "";
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="New production order"
      description="The bill of materials expands into parts, each with its default route. You can change any stage before releasing."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={create.isPending}
        disabled={!f.product_id || !(Number(f.quantity) > 0) || !plantId} onClick={() => { if (!f.plant_id) f.plant_id = plantId; create.mutate(undefined); }}>Plan order</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Product to make" htmlFor="po-p" className="sm:col-span-2" required hint={structures.data?.length === 0 ? "Define a bill of materials and route in Product Structure first." : undefined}>
          <Select id="po-p" value={f.product_id} onChange={(e) => setF({ ...f, product_id: e.target.value })}>
            <option value="">Select…</option>{structures.data?.map((s) => <option key={s.product_id} value={s.product_id}>{s.sku} — {s.name}</option>)}
          </Select>
        </Field>
        <Field label="Quantity" htmlFor="po-q" required><Input id="po-q" inputMode="decimal" value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} /></Field>
        <Field label="Plant" htmlFor="po-pl" required><Select id="po-pl" value={plantId} onChange={(e) => setF({ ...f, plant_id: e.target.value })}>
          {plants.data?.map((p) => <option key={p.warehouse_id} value={p.warehouse_id}>{p.name}</option>)}</Select></Field>
        <Field label="Customer" htmlFor="po-c"><Select id="po-c" value={f.customer_id} onChange={(e) => setF({ ...f, customer_id: e.target.value })}>
          <option value="">— (stock order)</option>{customers.data?.items.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
        <Field label="Start date" htmlFor="po-s"><Input id="po-s" type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></Field>
        <Field label="Due date" htmlFor="po-d"><Input id="po-d" type="date" value={f.due_date} onChange={(e) => setF({ ...f, due_date: e.target.value })} /></Field>
        <Field label="Notes" htmlFor="po-n" className="sm:col-span-2"><Textarea id="po-n" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      </div>
    </Dialog>
  );
}

function Progress({ value, className }: { value: number; className?: string }) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-surface-2"><div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(value, 100)}%` }} /></div>
      <span className="num text-xs text-fg-2">{Math.round(value)}%</span>
    </div>
  );
}

export function ProductionOrdersPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("planned,released,in_progress");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const dq = useDebounced(q);
  const list = useQuery({ queryKey: ["production-orders", status, dq, page], queryFn: () => api.get<Page<OrderRow>>("/production/orders", { status, q: dq, page, page_size: 25 }), placeholderData: (p) => p });
  return (
    <div>
      <PageHeader title="Production orders" description="Each order is a tree of parts. Track every part across the plant and job-work vendors, stage by stage."
        actions={<Can permission="production.manage"><Button variant="primary" onClick={() => setCreating(true)}><Plus /> New order</Button></Can>} />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <SearchInput value={q} onChange={(v) => { setQ(v); setPage(1); }} placeholder="Order number or product…" className="min-w-56 flex-1" />
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="w-48" aria-label="Status">
            <option value="planned,released,in_progress">Open</option><option value="planned">Planned</option><option value="in_progress">In progress</option>
            <option value="completed">Completed</option><option value="cancelled">Cancelled</option><option value="">All</option>
          </Select>
        </div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(o) => o.id}
          onRowClick={(o) => navigate(`/production/orders/${o.id}`)} page={page} pageSize={25} total={list.data?.total} onPage={setPage}
          empty={<EmptyState title="No production orders" description="Plan an order for a product that has a bill of materials and route." icon={<Factory />} />}
          columns={[
            { key: "n", header: "Order", cell: (o) => <span className="font-mono text-xs">{o.order_number}</span> },
            { key: "p", header: "Product", cell: (o) => <div><div className="font-medium">{o.product_name}</div><div className="text-xs text-muted">{qty(o.quantity)} {o.uom} · {o.plant_name}</div></div> },
            { key: "c", header: "Customer", hideBelow: "md", cell: (o) => o.customer_name ?? <span className="text-muted">Stock</span> },
            { key: "pr", header: "Progress", cell: (o) => <Progress value={o.progress} /> },
            { key: "f", header: "Finish / due", hideBelow: "md", cell: (o) => <div className="text-xs"><div className={cn(o.at_risk && "font-medium text-bad")}>{date(o.planned_finish)}</div><div className="text-muted">due {date(o.due_date)}</div></div> },
            { key: "l", header: "", hideBelow: "lg", cell: (o) => <div className="flex gap-1">{o.at_risk && <Badge tone="bad">At risk</Badge>}{o.late_stages > 0 && <Badge tone="warn">{o.late_stages} late</Badge>}</div> },
            { key: "s", header: "Status", cell: (o) => <StatusBadge status={o.status} /> },
          ]} />
      </Card>
      {creating && <NewOrderDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

// ---------------------------------------------------------------- stage dialogs
type Dlg = { kind: "send" | "receive" | "complete" | "edit"; stage: OrderStage; part: OrderPart } | { kind: "add"; part: OrderPart } | { kind: "remove"; stage: OrderStage; part: OrderPart };

function StageDialogs({ dlg, order, onClose }: { dlg: Dlg; order: ProductionOrder; onClose: () => void }) {
  const vendors = useSites("vendor");
  const plants = useSites("plant");
  const [key] = useState(newIdempotencyKey);
  const inv: unknown[][] = [["production-order", order.id], ["production-orders"], ["vendor-workload"], ["challans"], ["balances"], ["products"], ["overview"]];
  const stage = "stage" in dlg ? dlg.stage : null;
  const part = dlg.part;
  const next = stage ? part.stages.find((s) => s.sequence === stage.sequence + 1) : undefined;
  const [f, setF] = useState<Record<string, string | boolean>>((): Record<string, string | boolean> => {
    if (!stage) return { name: "", performer: "plant", site_id: "", rate_per_unit: "0", planned_days: "1", after_sequence: String(part.stages.length) };
    const held = stage.at_vendors.find((v) => Number(v.units) > 0);
    return {
      vendor_site_id: dlg.kind === "receive" ? held?.site_id ?? "" : stage.site_id, units: dlg.kind === "send" ? stage.waiting_at_plant.replace(/\.?0+$/, "") : "",
      accepted: held ? held.units.replace(/\.?0+$/, "") : "", rework: "", scrapped: "", scrap_reason: "", direct: false,
      next_vendor: next?.site_id ?? "", vendor_doc: "", expected: stage.planned_end ?? "", vehicle: "", notes: "",
      completed: stage.waiting_at_plant.replace(/\.?0+$/, ""), name: stage.name, performer: stage.performer, site_id: stage.site_id,
      rate_per_unit: stage.rate_per_unit, planned_start: stage.planned_start ?? "", planned_end: stage.planned_end ?? "",
    };
  });
  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const s = (k: string) => String(f[k] ?? "");

  const run = useAction(async () => {
    if (dlg.kind === "send") return api.post(`/production/stages/${stage!.id}/challans`, { vendor_site_id: s("vendor_site_id"), units: s("units"),
      expected_return_date: s("expected") || null, vehicle_number: s("vehicle") || null, notes: s("notes") || null }, key);
    if (dlg.kind === "receive") return api.post(`/production/stages/${stage!.id}/receipts`, {
      vendor_site_id: s("vendor_site_id"), units_accepted: s("accepted") || "0", units_rework: s("rework") || "0", units_scrapped: s("scrapped") || "0",
      scrap_reason: s("scrap_reason") || null, direct_to_next: !!f.direct, receive_to_site_id: f.direct ? s("next_vendor") || null : null,
      vendor_document_number: s("vendor_doc") || null, notes: s("notes") || null }, key);
    if (dlg.kind === "complete") return api.post(`/production/stages/${stage!.id}/complete`, { units_completed: s("completed") || "0",
      units_scrapped: s("scrapped") || "0", scrap_reason: s("scrap_reason") || null, notes: s("notes") || null }, key);
    if (dlg.kind === "edit") return api.patch(`/production/stages/${stage!.id}`, { name: s("name"), performer: s("performer"),
      site_id: s("site_id") || null, rate_per_unit: s("rate_per_unit"), planned_start: s("planned_start") || null, planned_end: s("planned_end") || null });
    if (dlg.kind === "add") return api.post(`/production/parts/${part.id}/stages`, { after_sequence: Number(s("after_sequence")), name: s("name"),
      performer: s("performer"), site_id: s("site_id") || null, rate_per_unit: s("rate_per_unit") || "0", planned_days: Number(s("planned_days") || 1) });
    return api.del(`/production/stages/${stage!.id}`);
  }, { success: { send: "Challan issued — material is now at the vendor", receive: "Receipt recorded", complete: "Progress recorded",
    edit: "Stage updated", add: "Stage added", remove: "Stage removed" }[dlg.kind], invalidate: inv, onSuccess: onClose });

  const siteOptions = (performer: string) => (performer === "vendor" ? vendors.data : plants.data) ?? [];
  if (dlg.kind === "remove") {
    return <ConfirmDialog open onOpenChange={(o) => !o && onClose()} title={`Remove stage “${dlg.stage.name}”?`} description="Only stages that haven't started can be removed. The product's default route is unchanged."
      tone="danger" confirmLabel="Remove stage" loading={run.isPending} onConfirm={() => run.mutate(undefined)} />;
  }
  const titles = { send: `Send to vendor · ${stage?.name}`, receive: `Record vendor return · ${stage?.name}`, complete: `Record progress · ${stage?.name}`,
    edit: `Edit stage · ${stage?.name}`, add: `Add stage · ${part.name}` };
  const desc = {
    send: `${qty(stage?.waiting_at_plant)} ${part.uom} of ${part.name} waiting at ${order.plant_name}. Issues a job-work challan and moves the material to the vendor's location.`,
    receive: `Accepted units ${stage?.is_last ? `become finished ${part.name} (material consumed, cost = material + stage rates)` : "return to the plant, or ship directly to the next vendor"}. Rework stays at the vendor; scrap is written off.`,
    complete: `${qty(stage?.waiting_at_plant)} available at ${order.plant_name}.${stage?.is_last ? ` Completing the last stage turns the material into ${part.name}.` : ""}`,
    edit: "Changes apply to this order only. Vendor and rate can't change once material has moved.", add: "Inserted into this order's route only.",
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={titles[dlg.kind]} description={desc[dlg.kind]}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={run.isPending} onClick={() => run.mutate(undefined)}>
        {{ send: "Issue challan", receive: "Record receipt", complete: "Record", edit: "Save", add: "Add stage" }[dlg.kind]}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        {dlg.kind === "send" && <>
          <Field label="Vendor" htmlFor="sd-v" hint="Split a stage by sending part of the quantity to another vendor."><Select id="sd-v" value={s("vendor_site_id")} onChange={set("vendor_site_id")}>
            {vendors.data?.map((v) => <option key={v.warehouse_id} value={v.warehouse_id}>{short(v.name)}{v.warehouse_id === stage!.site_id ? " (planned)" : ""}</option>)}</Select></Field>
          <Field label={`Units (${part.uom})`} htmlFor="sd-u"><Input id="sd-u" inputMode="decimal" value={s("units")} onChange={set("units")} /></Field>
          <Field label="Expected back" htmlFor="sd-e"><Input id="sd-e" type="date" value={s("expected")} onChange={set("expected")} /></Field>
          <Field label="Vehicle no." htmlFor="sd-vh"><Input id="sd-vh" value={s("vehicle")} onChange={set("vehicle")} /></Field>
          <Field label="Notes" htmlFor="sd-n" className="sm:col-span-2"><Textarea id="sd-n" value={s("notes")} onChange={set("notes")} /></Field>
        </>}
        {dlg.kind === "receive" && <>
          <Field label="From vendor" htmlFor="rc-v" className="sm:col-span-2"><Select id="rc-v" value={s("vendor_site_id")} onChange={set("vendor_site_id")}>
            {stage!.at_vendors.filter((v) => Number(v.units) > 0).map((v) => <option key={v.site_id} value={v.site_id}>{short(v.site_name)} — holds {qty(v.units)}</option>)}</Select></Field>
          <Field label="Accepted" htmlFor="rc-a"><Input id="rc-a" inputMode="decimal" value={s("accepted")} onChange={set("accepted")} /></Field>
          <Field label="Held for rework" htmlFor="rc-r" hint="Stays at the vendor"><Input id="rc-r" inputMode="decimal" value={s("rework")} onChange={set("rework")} /></Field>
          <Field label="Scrapped" htmlFor="rc-s"><Input id="rc-s" inputMode="decimal" value={s("scrapped")} onChange={set("scrapped")} /></Field>
          <Field label="Vendor document no." htmlFor="rc-d"><Input id="rc-d" value={s("vendor_doc")} onChange={set("vendor_doc")} /></Field>
          {Number(s("scrapped")) > 0 && <Field label="Scrap reason *" htmlFor="rc-sr" className="sm:col-span-2"><Input id="rc-sr" value={s("scrap_reason")} onChange={set("scrap_reason")} /></Field>}
          {!stage!.is_last && next?.performer === "vendor" && <div className="space-y-2 sm:col-span-2">
            <Checkbox label={`Ship accepted units directly to the next vendor for ${next.name}`} checked={!!f.direct} onChange={(e) => setF({ ...f, direct: e.target.checked })} />
            {f.direct && <Select aria-label="Next vendor" value={s("next_vendor")} onChange={set("next_vendor")}>
              {vendors.data?.filter((v) => v.warehouse_id !== s("vendor_site_id")).map((v) => <option key={v.warehouse_id} value={v.warehouse_id}>{short(v.name)}</option>)}</Select>}
          </div>}
        </>}
        {dlg.kind === "complete" && <>
          <Field label={`Completed (${part.uom})`} htmlFor="cp-c"><Input id="cp-c" inputMode="decimal" value={s("completed")} onChange={set("completed")} /></Field>
          <Field label="Scrapped" htmlFor="cp-s"><Input id="cp-s" inputMode="decimal" value={s("scrapped")} onChange={set("scrapped")} /></Field>
          {Number(s("scrapped")) > 0 && <Field label="Scrap reason *" htmlFor="cp-r" className="sm:col-span-2"><Input id="cp-r" value={s("scrap_reason")} onChange={set("scrap_reason")} /></Field>}
          <Field label="Notes" htmlFor="cp-n" className="sm:col-span-2"><Textarea id="cp-n" value={s("notes")} onChange={set("notes")} /></Field>
        </>}
        {(dlg.kind === "edit" || dlg.kind === "add") && <>
          <Field label="Stage name" htmlFor="ed-n" className="sm:col-span-2"><Input id="ed-n" value={s("name")} onChange={set("name")} /></Field>
          <Field label="Done by" htmlFor="ed-p"><Select id="ed-p" value={s("performer")} onChange={(e) => setF({ ...f, performer: e.target.value, site_id: "" })}>
            <option value="plant">Plant (in-house)</option><option value="vendor">Job-work vendor</option></Select></Field>
          <Field label={s("performer") === "vendor" ? "Vendor" : "Plant"} htmlFor="ed-s"><Select id="ed-s" value={s("site_id")} onChange={set("site_id")}>
            {s("performer") === "plant" && <option value="">Order plant</option>}{s("performer") === "vendor" && <option value="">Select…</option>}
            {siteOptions(s("performer")).map((x) => <option key={x.warehouse_id} value={x.warehouse_id}>{short(x.name)}</option>)}</Select></Field>
          <Field label="Rate per unit" htmlFor="ed-r" hint="Added to the part's cost"><Input id="ed-r" inputMode="decimal" value={s("rate_per_unit")} onChange={set("rate_per_unit")} /></Field>
          {dlg.kind === "edit" ? <>
            <Field label="Planned start" htmlFor="ed-ps"><Input id="ed-ps" type="date" value={s("planned_start")} onChange={set("planned_start")} /></Field>
            <Field label="Planned end" htmlFor="ed-pe"><Input id="ed-pe" type="date" value={s("planned_end")} onChange={set("planned_end")} /></Field>
          </> : <>
            <Field label="Planned days" htmlFor="ed-d"><Input id="ed-d" type="number" min={1} value={s("planned_days")} onChange={set("planned_days")} /></Field>
            <Field label="Insert after" htmlFor="ed-a"><Select id="ed-a" value={s("after_sequence")} onChange={set("after_sequence")}>
              <option value="0">At the start</option>{part.stages.map((x) => <option key={x.id} value={x.sequence}>{x.sequence}. {x.name}</option>)}</Select></Field>
          </>}
        </>}
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------- detail
const POS_STYLE: Record<Position["kind"], string> = {
  at_vendor: "border-accent/50 bg-accent-soft/60", waiting: "border-line bg-surface", not_started: "border-dashed border-line bg-transparent text-muted",
  done: "border-transparent bg-ok-soft text-ok", consumed: "border-transparent bg-surface-2 text-muted", stock: "border-line bg-surface",
};

function Where({ part }: { part: OrderPart }) {
  return (
    <div className="flex flex-col gap-1.5">
      {part.positions.length === 0 && <span className="text-xs text-muted">—</span>}
      {part.positions.map((p, i) => (
        <div key={i} className={cn("rounded-md border px-2 py-1 text-xs", POS_STYLE[p.kind], p.overdue && "border-bad/60 bg-bad-soft text-bad")}>
          <span className="num font-medium">{qty(p.units)}</span>{p.kind === "stock" && <span className="text-muted"> / {qty(p.required)} needed</span>}
          {" · "}{p.label}{" "}<span className="inline-flex items-center gap-0.5 text-muted"><MapPin className="size-3" />{short(p.site_name)}</span>
          {p.kind === "at_vendor" && p.since && <span className="text-muted"> · since {date(p.since)}</span>}
          {p.overdue && <b> · overdue</b>}
          {Number(p.rework ?? 0) > 0 && <span> · {qty(p.rework)} in rework</span>}
          {Number(p.short ?? 0) > 0 && <span className="text-bad"> · short {qty(p.short)}</span>}
        </div>
      ))}
    </div>
  );
}

function StageChip({ st }: { st: OrderStage }) {
  const tone = st.status === "completed" ? "bg-ok-soft text-ok border-transparent" : st.late ? "bg-bad-soft text-bad border-transparent"
    : st.status === "in_progress" ? "border-accent text-fg" : "border-line text-fg-2";
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11.5px]", tone)}>
      {st.performer === "vendor" ? <Truck className="size-3" /> : <Factory className="size-3" />}{st.name} · {short(st.site_name)}{st.late && st.status !== "completed" ? " · late" : ""}
    </span>
  );
}

function Timeline({ order }: { order: ProductionOrder }) {
  const rows = order.parts.flatMap((p) => p.stages.map((st) => ({ p, st })));
  const { start, days } = useMemo(() => {
    const ds = rows.flatMap(({ st }) => [st.planned_start, st.planned_end, st.actual_start?.slice(0, 10), st.actual_end?.slice(0, 10)]).filter(Boolean) as string[];
    ds.push(today());
    const min = ds.reduce((a, b) => (a < b ? a : b));
    const max = ds.reduce((a, b) => (a > b ? a : b));
    const n = Math.min(Math.round((Date.parse(max) - Date.parse(min)) / 864e5) + 2, 90);
    return { start: Date.parse(min), days: n };
  }, [order]);
  if (!rows.length) return null;
  const col = (d: string) => Math.round((Date.parse(d.slice(0, 10)) - start) / 864e5) + 1;
  const todayCol = col(today());
  return (
    <div className="overflow-x-auto">
      <div className="grid min-w-[760px] text-xs" style={{ gridTemplateColumns: `220px repeat(${days}, minmax(18px, 1fr))` }}>
        <div className="border-b border-line" />
        {Array.from({ length: days }, (_, i) => {
          const d = new Date(start + i * 864e5);
          return <div key={i} className={cn("border-b border-line py-1.5 text-center font-mono text-[10px] text-muted", i + 1 === todayCol && "font-semibold text-accent")}>{d.getDate()}</div>;
        })}
        {rows.map(({ p, st }) => {
          const late = st.late && st.status !== "completed";
          const actEnd = st.actual_end ?? (st.actual_start ? new Date().toISOString() : null);
          return [
            <div key={`${st.id}-n`} className="truncate border-t border-line px-3 py-2" title={`${p.name} · ${st.name}`}>
              <span className={cn(p.level > 0 && "text-fg-2")}>{p.name}</span> <span className="text-muted">· {st.name}</span>
            </div>,
            <div key={`${st.id}-l`} className="relative grid border-t border-line py-1.5" style={{ gridColumn: `2 / span ${days}`, gridTemplateColumns: `repeat(${days}, minmax(18px, 1fr))`, rowGap: 3 }}>
              {st.planned_start && st.planned_end && <div className="h-2 rounded-sm border border-fg-2/60" style={{ gridColumn: `${col(st.planned_start)} / ${col(st.planned_end) + 1}`, gridRow: 1 }} />}
              {st.actual_start && actEnd ? <div className={cn("h-2 rounded-sm", st.status === "completed" ? "bg-ok" : late ? "bg-bad" : "bg-accent")}
                style={{ gridColumn: `${col(st.actual_start)} / ${col(actEnd) + 1}`, gridRow: 2 }} /> : <div className="h-2" style={{ gridColumn: "1 / 2", gridRow: 2 }} />}
              <div className="pointer-events-none absolute inset-y-0 border-l border-dashed border-accent" style={{ left: `${((todayCol - 0.5) / days) * 100}%` }} />
            </div>,
          ];
        })}
      </div>
      <div className="flex flex-wrap gap-4 border-t border-line px-4 py-2 text-[11.5px] text-muted">
        <span className="inline-flex items-center gap-1.5"><i className="inline-block h-2 w-5 rounded-sm border border-fg-2/60" />Planned</span>
        <span className="inline-flex items-center gap-1.5"><i className="inline-block h-2 w-5 rounded-sm bg-ok" />Done</span>
        <span className="inline-flex items-center gap-1.5"><i className="inline-block h-2 w-5 rounded-sm bg-accent" />In progress</span>
        <span className="inline-flex items-center gap-1.5"><i className="inline-block h-2 w-5 rounded-sm bg-bad" />Past planned end</span>
        <span className="text-accent">┆ today</span>
      </div>
    </div>
  );
}

export function ProductionOrderPage() {
  const { id } = useParams();
  const { can } = useAuth();
  const [dlg, setDlg] = useState<Dlg | null>(null);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [openPart, setOpenPart] = useState<string | null>(null);
  const o = useQuery({ queryKey: ["production-order", id], queryFn: () => api.get<ProductionOrder>(`/production/orders/${id}`) });
  const act = useAction(({ action, body }: { action: string; body?: unknown }) => api.post<ProductionOrder>(`/production/orders/${id}/${action}`, body),
    { success: (r) => `Order ${label(r.status).toLowerCase()}`, invalidate: [["production-order", id], ["production-orders"]], onSuccess: () => setCancelOpen(false) });
  if (o.error) return <ErrorState error={o.error} onRetry={o.refetch} />;
  if (!o.data) return <Skeleton className="h-96" />;
  const d = o.data;
  const active = d.status === "released" || d.status === "in_progress";
  const editable = d.status !== "completed" && d.status !== "cancelled" && can("production.manage");
  const exec = active && can("production.execute");
  return (
    <div className="space-y-4">
      <PageHeader breadcrumb={[{ label: "Production orders", to: "/production/orders" }]}
        title={<span className="flex flex-wrap items-center gap-3">{d.order_number} · {d.product_name} <StatusBadge status={d.status} />{d.at_risk && <Badge tone="bad">At risk</Badge>}</span>}
        description={`${qty(d.quantity)} ${d.uom} · ${d.plant_name}${d.customer_name ? ` · for ${d.customer_name}` : ""}`}
        actions={<>
          {d.status === "planned" && can("production.manage") && <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "release" })}>Release to plant</Button>}
          {(d.status === "planned" || d.status === "released") && can("production.manage") && <Button variant="ghost" onClick={() => setCancelOpen(true)}>Cancel</Button>}
        </>} />
      <Card><div className="flex flex-wrap items-center gap-x-8 gap-y-4 p-5">
        <div><div className="text-xs text-muted">Progress</div><Progress value={d.progress} className="mt-1" /></div>
        <DetailGrid items={[
          { label: "Start", value: date(d.start_date) }, { label: "Planned finish", value: <span className={cn(d.at_risk && "font-medium text-bad")}>{date(d.planned_finish)}</span> },
          { label: "Due", value: date(d.due_date) }, { label: "Late stages", value: d.late_stages ? <span className="text-bad">{d.late_stages}</span> : "None" },
        ]} />
      </div>{d.status === "planned" && <p className="border-t border-line bg-accent-soft/40 px-5 py-2.5 text-xs text-fg-2">Planned: review each part's route below (change vendors, rates or dates, add or remove stages), then release the order. Material can only move after release.</p>}</Card>

      <Card>
        <CardHeader title="Where every part is" description="Live from challans, vendor receipts and stage completions. Click a part to act on its stages." />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm">
            <thead><tr className="bg-surface-2/60 text-left text-[11px] uppercase tracking-wider text-muted">
              <th className="px-4 py-2 font-medium">Part</th><th className="px-4 py-2 font-medium">Route</th><th className="px-4 py-2 font-medium">Where now</th><th className="px-4 py-2 font-medium">Progress</th>
            </tr></thead>
            <tbody>
              {d.parts.map((p) => (
                <PartRow key={p.id} part={p} order={d} open={openPart === p.id} onToggle={() => setOpenPart(openPart === p.id ? null : p.id)}
                  onAction={setDlg} exec={exec} editable={editable} />
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card><CardHeader title="Timeline" description="Planned dates against what actually happened, per stage" /><Timeline order={d} /></Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <OrderChallans orderId={d.id} />
        <Card>
          <CardHeader title="Activity" />
          <ul className="max-h-96 divide-y divide-line overflow-y-auto">
            {d.events.map((e) => (
              <li key={e.id} className="px-5 py-2.5 text-sm"><div>{e.message}</div><div className="text-xs text-muted">{dateTime(e.created_at)}{e.user_name ? ` · ${e.user_name}` : ""}</div></li>
            ))}
          </ul>
        </Card>
      </div>
      {dlg && <StageDialogs dlg={dlg} order={d} onClose={() => setDlg(null)} />}
      <ConfirmDialog open={cancelOpen} onOpenChange={setCancelOpen} title="Cancel production order" requireReason tone="danger" confirmLabel="Cancel order"
        loading={act.isPending} onConfirm={(reason) => act.mutate({ action: "cancel", body: { reason } })} />
    </div>
  );
}

function PartRow({ part: p, order, open, onToggle, onAction, exec, editable }: {
  part: OrderPart; order: ProductionOrder; open: boolean; onToggle: () => void; onAction: (d: Dlg) => void; exec: boolean; editable: boolean;
}) {
  const made = p.source === "make";
  return (
    <>
      <tr className={cn("border-t border-line align-top", made && "cursor-pointer hover:bg-surface-2/50")} onClick={made ? onToggle : undefined}>
        <td className="px-4 py-3" style={{ paddingLeft: 16 + p.level * 20 }}>
          <div className="font-medium">{p.level > 0 && <span className="text-muted">└ </span>}{p.name}</div>
          <div className="text-xs text-muted">{qty(p.quantity)} {p.uom}{p.level > 0 && ` · ${qty(p.qty_per_parent)} per parent`} · {made ? "made" : "bought-in"}</div>
        </td>
        <td className="px-4 py-3">{made ? <div className="flex flex-wrap items-center gap-1">{p.stages.map((st, i) => <span key={st.id} className="inline-flex items-center gap-1">{i > 0 && <ArrowRight className="size-3 text-muted" />}<StageChip st={st} /></span>)}</div>
          : <span className="text-xs text-muted">From stock</span>}</td>
        <td className="px-4 py-3"><Where part={p} /></td>
        <td className="px-4 py-3">{made ? <Progress value={p.progress} /> : null}</td>
      </tr>
      {open && made && (
        <tr className="border-t border-line bg-surface-2/30">
          <td colSpan={4} className="px-4 py-3">
            {p.kit.some((k) => Number(k.short) > 0) && (
              <div className="mb-3 rounded-lg border border-warn/40 bg-warn-soft/60 px-3 py-2 text-xs text-warn">
                Material short at {order.plant_name}: {p.kit.filter((k) => Number(k.short) > 0).map((k) => `${k.name} ${qty(k.short)} ${k.uom}`).join(", ")}. Transfer it from the warehouse (Stock Transfers).
              </div>
            )}
            <div className="space-y-2">
              {p.stages.map((st) => (
                <div key={st.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface px-3 py-2.5">
                  <div className="min-w-0 text-sm">
                    <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{st.sequence}. {st.name}</span><StatusBadge status={st.status} />{st.late && st.status !== "completed" && <Badge tone="bad">Late</Badge>}</div>
                    <div className="mt-0.5 text-xs text-muted">
                      {st.performer === "vendor" ? "Vendor" : "In-house"}: {short(st.site_name)} · rate {money(st.rate_per_unit)}/unit · planned {date(st.planned_start)}–{date(st.planned_end)}
                      {" · "}done {qty(st.units_completed)}/{qty(st.target)}{Number(st.units_scrapped) > 0 && ` · scrapped ${qty(st.units_scrapped)}`}
                      {Number(st.waiting_at_plant) > 0 && ` · ${qty(st.waiting_at_plant)} waiting at plant`}
                      {st.at_vendors.filter((v) => Number(v.units) > 0).map((v) => ` · ${qty(v.units)} at ${short(v.site_name)}`).join("")}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5" onClick={(e) => e.stopPropagation()}>
                    {exec && st.performer === "vendor" && Number(st.waiting_at_plant) > 0 && <Button size="sm" variant="primary" onClick={() => onAction({ kind: "send", stage: st, part: p })}><Send /> Send to vendor</Button>}
                    {exec && st.performer === "vendor" && st.at_vendors.some((v) => Number(v.units) > 0) && <Button size="sm" onClick={() => onAction({ kind: "receive", stage: st, part: p })}><Undo2 /> Record return</Button>}
                    {exec && st.performer === "plant" && Number(st.waiting_at_plant) > 0 && <Button size="sm" variant="primary" onClick={() => onAction({ kind: "complete", stage: st, part: p })}><Wrench /> Record progress</Button>}
                    {editable && <Button size="sm" variant="ghost" onClick={() => onAction({ kind: "edit", stage: st, part: p })} aria-label={`Edit ${st.name}`}><Pencil /></Button>}
                    {editable && st.status === "planned" && p.stages.length > 1 && <Button size="sm" variant="ghost" onClick={() => onAction({ kind: "remove", stage: st, part: p })} aria-label={`Remove ${st.name}`}><Trash2 /></Button>}
                  </div>
                </div>
              ))}
              {editable && <Button size="sm" variant="ghost" onClick={() => onAction({ kind: "add", part: p })}><Plus /> Add stage</Button>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function OrderChallans({ orderId }: { orderId: string }) {
  const list = useQuery({ queryKey: ["challans", { orderId }], queryFn: () => api.get<Page<{ id: string; challan_number: string; part_name: string; stage_name: string; vendor_name: string; units: string; challan_date: string }>>("/production/challans", { order_id: orderId, page_size: 100 }) });
  return (
    <Card>
      <CardHeader title="Job-work challans" />
      <DataTable dense rows={list.data?.items} loading={list.isLoading} rowKey={(c) => c.id} empty={<EmptyState title="No challans yet" />}
        columns={[
          { key: "n", header: "Challan", cell: (c) => <Link to={`/production/challans/${c.id}/print`} className="font-mono text-xs text-accent hover:underline">{c.challan_number}</Link> },
          { key: "p", header: "Part · stage", cell: (c) => <span className="text-xs">{c.part_name} · {c.stage_name}</span> },
          { key: "v", header: "Vendor", cell: (c) => <span className="text-xs">{short(c.vendor_name)}</span> },
          { key: "u", header: "Units", align: "right", cell: (c) => <span className="num">{qty(c.units)}</span> },
          { key: "d", header: "Date", hideBelow: "md", cell: (c) => <span className="text-xs text-muted">{relative(c.challan_date)}</span> },
        ]} />
    </Card>
  );
}

