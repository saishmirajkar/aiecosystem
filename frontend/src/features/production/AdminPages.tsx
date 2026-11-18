import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Factory, FileText, Plus, Printer, Trash2, Truck } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ProductPicker } from "@/components/pickers";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/overlay";
import { EmptyState, ErrorState, PageHeader, Stat } from "@/components/ui/page";
import { Badge, Button, Card, CardHeader, Field, Input, Select, Skeleton } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useSuppliers, useWarehouses } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { date, money, qty } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Page, Product } from "@/types/api";
import type { Challan, Site, Structure, StructureRow, VendorWorkload } from "@/types/production";
import { useSites } from "./OrdersPages";

const short = (name: string) => name.replace(" (job work)", "");

// ---------------------------------------------------------------- vendor workload
export function VendorWorkloadPage() {
  const wl = useQuery({ queryKey: ["vendor-workload"], queryFn: () => api.get<VendorWorkload[]>("/production/vendors/workload") });
  if (wl.error) return <ErrorState error={wl.error} onRetry={wl.refetch} />;
  const rows = wl.data ?? [];
  const totalUnits = rows.reduce((a, v) => a + Number(v.units_held), 0);
  const totalValue = rows.reduce((a, v) => a + Number(v.material_value), 0);
  const overdue = rows.reduce((a, v) => a + v.overdue_jobs, 0);
  return (
    <div className="space-y-4">
      <PageHeader title="Vendor workload" description="What each job-work vendor is holding right now, what is overdue, and how they have performed." />
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Units at vendors" value={qty(totalUnits)} />
        <Stat label="Company material at vendors" value={money(totalValue)} hint="Valued at ledger cost" />
        <Stat label="Overdue jobs" value={overdue} tone={overdue ? "bad" : "ok"} />
      </div>
      {wl.isLoading && <Skeleton className="h-64" />}
      {!wl.isLoading && rows.length === 0 && <Card><EmptyState title="No job-work vendors yet" description="Add a vendor location under Plants & Vendors." icon={<Truck />} /></Card>}
      <div className="grid gap-4 xl:grid-cols-2">
        {rows.map((v) => (
          <Card key={v.warehouse_id}>
            <CardHeader title={short(v.name)} description={[v.supplier_name !== short(v.name) ? v.supplier_name : null, v.city, `${qty(v.units_processed)} units processed`].filter(Boolean).join(" · ")}
              actions={v.overdue_jobs > 0 ? <Badge tone="bad">{v.overdue_jobs} overdue</Badge> : undefined} />
            <div className="grid grid-cols-2 gap-4 border-b border-line px-5 pb-4 text-sm sm:grid-cols-4">
              <div><div className="text-xs text-muted">Holding</div><div className="num font-medium">{qty(v.units_held)}</div></div>
              <div><div className="text-xs text-muted">Material value</div><div className="num font-medium">{money(v.material_value)}</div></div>
              <div><div className="text-xs text-muted">Rejection rate</div><div className={cn("num font-medium", (v.rejection_rate ?? 0) > 5 && "text-bad")}>{v.rejection_rate == null ? "—" : `${v.rejection_rate}%`}</div></div>
              <div><div className="text-xs text-muted">Avg turnaround</div><div className="num font-medium">{v.avg_turnaround_days == null ? "—" : `${v.avg_turnaround_days} d`}</div></div>
            </div>
            {v.open_jobs.length === 0 ? <p className="px-5 py-4 text-sm text-muted">Nothing at this vendor.</p> : (
              <ul className="divide-y divide-line">
                {v.open_jobs.map((j) => (
                  <li key={`${j.stage_id}`} className="flex flex-wrap items-center justify-between gap-2 px-5 py-2.5 text-sm">
                    <div className="min-w-0">
                      <Link to={`/production/orders/${j.order_id}`} className="font-mono text-xs text-accent hover:underline">{j.order_number}</Link>
                      <span className="ml-2 font-medium">{j.part}</span><span className="text-muted"> · {j.stage}</span>
                      <div className="text-xs text-muted">since {date(j.since)} · expected {date(j.expected_return)}{Number(j.rework) > 0 && ` · ${qty(j.rework)} in rework`}</div>
                    </div>
                    <div className="flex items-center gap-2"><span className="num font-medium">{qty(j.units)}</span>{j.overdue && <Badge tone="bad">Overdue</Badge>}</div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- challans
export function ChallansPage() {
  const vendors = useSites("vendor");
  const [vendor, setVendor] = useState("");
  const [page, setPage] = useState(1);
  const list = useQuery({ queryKey: ["challans", vendor, page], queryFn: () => api.get<Page<Challan>>("/production/challans", { vendor_site_id: vendor, page, page_size: 25 }), placeholderData: (p) => p });
  const navigate = useNavigate();
  return (
    <div>
      <PageHeader title="Job-work challans" description="Every dispatch of company material to a job-work vendor. Challans are issued from a production order stage." />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <Select value={vendor} onChange={(e) => { setVendor(e.target.value); setPage(1); }} className="w-64" aria-label="Vendor">
            <option value="">All vendors</option>{vendors.data?.map((v) => <option key={v.warehouse_id} value={v.warehouse_id}>{short(v.name)}</option>)}
          </Select>
        </div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(c) => c.id}
          onRowClick={(c) => navigate(`/production/challans/${c.id}/print`)} page={page} pageSize={25} total={list.data?.total} onPage={setPage}
          empty={<EmptyState title="No challans" icon={<FileText />} />}
          columns={[
            { key: "n", header: "Challan", cell: (c) => <span className="font-mono text-xs">{c.challan_number}</span> },
            { key: "o", header: "Order", cell: (c) => <span className="font-mono text-xs">{c.order_number}</span> },
            { key: "p", header: "Part · stage", cell: (c) => <div><div className="font-medium">{c.part_name}</div><div className="text-xs text-muted">{c.stage_name}</div></div> },
            { key: "v", header: "Vendor", cell: (c) => short(c.vendor_name) },
            { key: "u", header: "Units", align: "right", cell: (c) => <span className="num">{qty(c.units)}</span> },
            { key: "val", header: "Material value", align: "right", hideBelow: "md", cell: (c) => <span className="num">{money(c.total_value)}</span> },
            { key: "d", header: "Date", hideBelow: "md", cell: (c) => <span className="text-xs text-muted">{date(c.challan_date)}</span> },
          ]} />
      </Card>
    </div>
  );
}

/** Standalone printable delivery challan for job work (rendered outside the app shell). */
export function ChallanPrintPage() {
  const { id } = useParams();
  const c = useQuery({ queryKey: ["challan", id], queryFn: () => api.get<Challan>(`/production/challans/${id}`) });
  useEffect(() => { if (c.data) document.title = `${c.data.challan_number} — Job-work challan`; }, [c.data]);
  if (c.error) return <div className="p-8"><ErrorState error={c.error} onRetry={c.refetch} /></div>;
  if (!c.data) return <div className="p-8"><Skeleton className="h-96" /></div>;
  const d = c.data;
  return (
    <div className="min-h-full bg-surface-2 py-8 print:bg-white print:py-0">
      <div className="no-print mx-auto mb-4 flex max-w-3xl items-center justify-between px-4">
        <Link to={`/production/orders/${d.order_id}`} className="text-sm text-muted hover:text-fg">← Back to {d.order_number}</Link>
        <Button variant="primary" onClick={() => window.print()}><Printer /> Print</Button>
      </div>
      <article className="mx-auto max-w-3xl bg-white p-10 text-[13px] leading-relaxed text-black shadow-sm print:max-w-none print:p-0 print:shadow-none">
        <header className="flex items-start justify-between border-b-2 border-black pb-4">
          <div>
            <div className="text-lg font-bold">{d.company?.name ?? "Company"}</div>
            <div className="whitespace-pre-line text-xs">{d.company?.address}</div>
            {d.company?.tax_id && <div className="text-xs">GSTIN: {d.company.tax_id}</div>}
          </div>
          <div className="text-right">
            <div className="text-base font-bold uppercase tracking-wide">Delivery challan</div>
            <div className="text-xs">For job work — goods sent for processing, not for sale</div>
            <div className="mt-2 font-mono">{d.challan_number}</div>
            <div className="text-xs">Date: {date(d.challan_date)}</div>
          </div>
        </header>
        <section className="grid grid-cols-2 gap-6 border-b border-black/30 py-4">
          <div>
            <div className="text-[11px] font-semibold uppercase">Job worker</div>
            <div className="font-semibold">{short(d.vendor_name)}</div>
            <div className="whitespace-pre-line text-xs">{d.vendor_address}</div>
            {d.vendor_gstin && <div className="text-xs">GSTIN: {d.vendor_gstin}</div>}
          </div>
          <div className="text-xs">
            <div><b>Dispatched from:</b> {short(d.from_site)}</div>
            <div><b>Production order:</b> {d.order_number}</div>
            <div><b>Process:</b> {d.stage_name} on {d.part_name} ({d.part_sku})</div>
            <div><b>Units to process:</b> {qty(d.units)}</div>
            <div><b>Expected return:</b> {date(d.expected_return_date)}</div>
            <div><b>Vehicle no.:</b> {d.vehicle_number ?? "—"}</div>
          </div>
        </section>
        <table className="mt-4 w-full border-collapse text-xs">
          <thead><tr className="border-y border-black">
            <th className="py-1.5 text-left">#</th><th className="py-1.5 text-left">Item</th><th className="py-1.5 text-right">Quantity</th>
            <th className="py-1.5 text-left pl-2">UoM</th><th className="py-1.5 text-right">Rate</th><th className="py-1.5 text-right">Value</th>
          </tr></thead>
          <tbody>
            {d.lines.map((l, i) => (
              <tr key={i} className="border-b border-black/20">
                <td className="py-1.5">{i + 1}</td><td className="py-1.5">{l.name}<div className="font-mono text-[10px]">{l.sku}</div></td>
                <td className="py-1.5 text-right">{qty(l.quantity, 4)}</td><td className="py-1.5 pl-2">{l.uom}</td>
                <td className="py-1.5 text-right">{money(l.unit_value)}</td><td className="py-1.5 text-right">{money(l.value)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot><tr className="border-b border-black font-semibold"><td colSpan={5} className="py-1.5 text-right">Total value (for reference, not for sale)</td><td className="py-1.5 text-right">{money(d.total_value)}</td></tr></tfoot>
        </table>
        {d.notes && <p className="mt-3 text-xs"><b>Notes:</b> {d.notes}</p>}
        <p className="mt-4 text-[11px]">The goods above remain the property of {d.company?.name ?? "the company"} and are sent for job work only. Processed goods, scrap and unused material are to be returned against this challan.</p>
        <footer className="mt-16 grid grid-cols-2 gap-10 text-xs">
          <div className="border-t border-black pt-1">Received by (job worker)</div>
          <div className="border-t border-black pt-1 text-right">For {d.company?.name ?? "the company"} — authorised signatory</div>
        </footer>
      </article>
    </div>
  );
}

// ---------------------------------------------------------------- product structure
type BomDraft = { key: string; product: Pick<Product, "id" | "sku" | "name" | "uom_code"> | null; quantity: string };
type StageDraft = { key: string; name: string; performer: "plant" | "vendor"; site_id: string; rate_per_unit: string; planned_days: string };
let seq = 0;
const k = () => `r${++seq}`;

function StructureEditor({ productId, onClose }: { productId: string | null; onClose: () => void }) {
  const [product, setProduct] = useState<Product | null>(null);
  const pid = productId ?? product?.id ?? null;
  const existing = useQuery({ queryKey: ["structure", pid], queryFn: () => api.get<Structure>(`/production/products/${pid}/structure`), enabled: !!pid });
  const vendors = useSites("vendor");
  const plants = useSites("plant");
  const [bom, setBom] = useState<BomDraft[] | null>(null);
  const [route, setRoute] = useState<StageDraft[] | null>(null);
  useEffect(() => {
    if (!existing.data) return;
    setBom(existing.data.bom.map((b) => ({ key: k(), product: { id: b.component_id, sku: b.component_sku, name: b.component_name, uom_code: b.uom }, quantity: b.quantity.replace(/\.?0+$/, "") })));
    setRoute(existing.data.route.map((s) => ({ key: k(), name: s.name, performer: s.performer, site_id: s.site_id ?? "", rate_per_unit: s.rate_per_unit.replace(/\.?0+$/, "") || "0", planned_days: String(s.planned_days) })));
  }, [existing.data]);
  const save = useAction(() => api.put<Structure>(`/production/products/${pid}/structure`, {
    bom: (bom ?? []).filter((b) => b.product).map((b) => ({ component_id: b.product!.id, quantity: b.quantity })),
    route: (route ?? []).map((s) => ({ name: s.name, performer: s.performer, site_id: s.site_id || null, rate_per_unit: s.rate_per_unit || "0", planned_days: Number(s.planned_days || 0) })),
  }), { success: "Structure saved — new orders use it; existing orders keep their own route", invalidate: [["structures"], ["structure", pid]], onSuccess: onClose });
  const move = (i: number, d: number) => setRoute((r) => { const n = [...(r ?? [])]; const a = n[i]!, b = n[i + d]!; n[i] = b; n[i + d] = a; return n; });
  const upd = (i: number, patch: Partial<StageDraft>) => setRoute((r) => (r ?? []).map((s, j) => (j === i ? { ...s, ...patch } : s)));
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="xl" title={existing.data ? `Structure · ${existing.data.sku} ${existing.data.name}` : "New product structure"}
      description="Bill of materials (what goes in, per unit made) and the default route (who does each stage, at what rate). Orders copy this and can change it."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!pid || !bom || !route} onClick={() => save.mutate(undefined)}>Save structure</Button></>}>
      {!productId && <Field label="Product to make" htmlFor="st-p" className="mb-4"><ProductPicker id="st-p" value={product} onChange={(p) => { setProduct(p); setBom(null); setRoute(null); }} /></Field>}
      {pid && (!bom || !route) && <Skeleton className="h-40" />}
      {bom && route && (
        <div className="space-y-6">
          <section>
            <h3 className="mb-2 text-sm font-semibold">Bill of materials <span className="font-normal text-muted">— per 1 unit</span></h3>
            <div className="space-y-2">
              {bom.map((b, i) => (
                <div key={b.key} className="grid grid-cols-[1fr_120px_auto] items-center gap-2">
                  <ProductPicker value={b.product as Product | null} onChange={(p) => setBom(bom.map((x, j) => (j === i ? { ...x, product: p } : x)))} />
                  <div className="flex items-center gap-1.5"><Input aria-label="Quantity" inputMode="decimal" value={b.quantity} onChange={(e) => setBom(bom.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} /><span className="text-xs text-muted">{b.product?.uom_code}</span></div>
                  <Button variant="ghost" size="icon" aria-label="Remove component" onClick={() => setBom(bom.filter((_, j) => j !== i))}><Trash2 /></Button>
                </div>
              ))}
              <Button size="sm" variant="ghost" onClick={() => setBom([...bom, { key: k(), product: null, quantity: "1" }])}><Plus /> Add component</Button>
            </div>
          </section>
          <section>
            <h3 className="mb-2 text-sm font-semibold">Route <span className="font-normal text-muted">— the material is consumed and the product is made at the last stage</span></h3>
            <div className="space-y-2">
              {route.map((s, i) => (
                <div key={s.key} className="grid gap-2 rounded-lg border border-line p-2.5 sm:grid-cols-[28px_1.4fr_130px_1.2fr_100px_80px_auto] sm:items-center">
                  <span className="num text-center text-xs text-muted">{i + 1}</span>
                  <Input aria-label="Stage name" placeholder="Stage name" value={s.name} onChange={(e) => upd(i, { name: e.target.value })} />
                  <Select aria-label="Done by" value={s.performer} onChange={(e) => upd(i, { performer: e.target.value as "plant" | "vendor", site_id: "" })}>
                    <option value="plant">In-house</option><option value="vendor">Vendor</option></Select>
                  <Select aria-label="Site" value={s.site_id} onChange={(e) => upd(i, { site_id: e.target.value })}>
                    <option value="">{s.performer === "plant" ? "Order plant" : "Select vendor…"}</option>
                    {((s.performer === "vendor" ? vendors.data : plants.data) ?? []).map((x) => <option key={x.warehouse_id} value={x.warehouse_id}>{short(x.name)}</option>)}
                  </Select>
                  <Input aria-label="Rate per unit" placeholder="Rate" inputMode="decimal" value={s.rate_per_unit} onChange={(e) => upd(i, { rate_per_unit: e.target.value })} />
                  <Input aria-label="Planned days" type="number" min={0} value={s.planned_days} onChange={(e) => upd(i, { planned_days: e.target.value })} />
                  <div className="flex">
                    <Button variant="ghost" size="icon" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp /></Button>
                    <Button variant="ghost" size="icon" aria-label="Move down" disabled={i === route.length - 1} onClick={() => move(i, 1)}><ArrowDown /></Button>
                    <Button variant="ghost" size="icon" aria-label="Remove stage" onClick={() => setRoute(route.filter((_, j) => j !== i))}><Trash2 /></Button>
                  </div>
                </div>
              ))}
              <p className="text-[11.5px] text-muted">Columns: stage · done by · where · rate per unit · planned days.</p>
              <Button size="sm" variant="ghost" onClick={() => setRoute([...route, { key: k(), name: "", performer: "plant", site_id: "", rate_per_unit: "0", planned_days: "1" }])}><Plus /> Add stage</Button>
            </div>
          </section>
        </div>
      )}
    </Dialog>
  );
}

export function StructuresPage() {
  const list = useQuery({ queryKey: ["structures"], queryFn: () => api.get<StructureRow[]>("/production/structures") });
  const [editing, setEditing] = useState<string | null | undefined>(undefined);
  const { can } = useAuth();
  return (
    <div>
      <PageHeader title="Product structure" description="For each product you make: its bill of materials and default route through the plant and job-work vendors."
        actions={<Can permission="production.manage"><Button variant="primary" onClick={() => setEditing(null)}><Plus /> New structure</Button></Can>} />
      <Card>
        <DataTable rows={list.data} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(r) => r.product_id}
          onRowClick={can("production.manage") ? (r) => setEditing(r.product_id) : undefined}
          empty={<EmptyState title="No structures yet" description="Define what a product is made of and the stages it goes through." icon={<Factory />} />}
          columns={[
            { key: "s", header: "SKU", cell: (r) => <span className="font-mono text-xs">{r.sku}</span> },
            { key: "n", header: "Product", cell: (r) => <span className="font-medium">{r.name}</span> },
            { key: "c", header: "Components", align: "right", cell: (r) => <span className="num">{r.components}</span> },
            { key: "st", header: "Stages", align: "right", cell: (r) => <span className="num">{r.stages}</span> },
          ]} />
      </Card>
      {editing !== undefined && <StructureEditor productId={editing} onClose={() => setEditing(undefined)} />}
    </div>
  );
}

// ---------------------------------------------------------------- plants & vendors
function AddSiteDialog({ kind, onClose }: { kind: "plant" | "vendor" | "designate"; onClose: () => void }) {
  const suppliers = useSuppliers();
  const warehouses = useWarehouses();
  const sites = useSites();
  const [f, setF] = useState({ code: "", name: "", city: "", supplier_id: "", warehouse_id: "" });
  const taken = new Set((sites.data ?? []).map((s) => s.warehouse_id));
  const takenSup = new Set((sites.data ?? []).map((s) => s.supplier_id).filter(Boolean));
  const save = useAction(() => kind === "plant" ? api.post<Site>("/production/sites/plants", { code: f.code, name: f.name, city: f.city || null })
    : kind === "vendor" ? api.post<Site>("/production/sites/vendors", { supplier_id: f.supplier_id })
      : api.post<Site>("/production/sites/designate", { warehouse_id: f.warehouse_id }),
  { success: (s) => `${s.name} added`, invalidate: [["production-sites"], ["warehouses"]], onSuccess: onClose });
  const title = { plant: "New plant", vendor: "Add job-work vendor", designate: "Use an existing warehouse as a plant" }[kind];
  const desc = {
    plant: "Creates a stock location for the plant. Material transferred here is tracked separately from the main warehouse.",
    vendor: "Creates a location that holds company material while it is at the vendor, so it stays visible and valued in stock.",
    designate: "Marks an existing warehouse as a production plant.",
  }[kind];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={title} description={desc}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      {kind === "plant" && <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Code" htmlFor="pl-c" hint="Upper-case, e.g. PLANT-B" required><Input id="pl-c" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} /></Field>
        <Field label="City" htmlFor="pl-ci"><Input id="pl-ci" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Field>
        <Field label="Name" htmlFor="pl-n" className="sm:col-span-2" required><Input id="pl-n" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      </div>}
      {kind === "vendor" && <Field label="Supplier" htmlFor="vd-s" hint="Add the vendor as a supplier first if they are not listed.">
        <Select id="vd-s" value={f.supplier_id} onChange={(e) => setF({ ...f, supplier_id: e.target.value })}>
          <option value="">Select…</option>{suppliers.data?.items.filter((s) => s.is_active && !takenSup.has(s.id)).map((s) => <option key={s.id} value={s.id}>{s.code} — {s.name}</option>)}
        </Select></Field>}
      {kind === "designate" && <Field label="Warehouse" htmlFor="ds-w"><Select id="ds-w" value={f.warehouse_id} onChange={(e) => setF({ ...f, warehouse_id: e.target.value })}>
        <option value="">Select…</option>{warehouses.data?.filter((w) => !taken.has(w.id)).map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}
      </Select></Field>}
    </Dialog>
  );
}

export function SitesPage() {
  const sites = useSites();
  const [adding, setAdding] = useState<"plant" | "vendor" | "designate" | null>(null);
  const table = (kind: "plant" | "vendor") => (
    <DataTable rows={sites.data?.filter((s) => s.kind === kind)} loading={sites.isLoading} error={sites.error} onRetry={sites.refetch} rowKey={(s) => s.warehouse_id}
      empty={<EmptyState title={kind === "plant" ? "No plants" : "No job-work vendors"} />}
      columns={[
        { key: "c", header: "Location code", cell: (s) => <Link to={`/inventory/warehouses/${s.warehouse_id}`} className="font-mono text-xs text-accent hover:underline">{s.code}</Link> },
        { key: "n", header: "Name", cell: (s) => <span className="font-medium">{short(s.name)}</span> },
        ...(kind === "vendor" ? [{ key: "s", header: "Supplier", cell: (s: Site) => s.supplier_name ?? "—" }] : []),
        { key: "ci", header: "City", hideBelow: "md", cell: (s) => s.city ?? "—" },
        { key: "a", header: "", cell: (s) => (s.is_active ? null : <Badge>Inactive</Badge>) },
      ]} />
  );
  return (
    <div className="space-y-4">
      <PageHeader title="Plants & vendors" description="Production plants and job-work vendors are stock locations, so you always know how much company material sits where." />
      <Card>
        <CardHeader title="Plants" actions={<Can permission="production.manage"><Button size="sm" variant="ghost" onClick={() => setAdding("designate")}>Use existing warehouse</Button><Button size="sm" onClick={() => setAdding("plant")}><Plus /> New plant</Button></Can>} />
        {table("plant")}
      </Card>
      <Card>
        <CardHeader title="Job-work vendors" actions={<Can permission="production.manage"><Button size="sm" onClick={() => setAdding("vendor")}><Plus /> Add vendor</Button></Can>} />
        {table("vendor")}
      </Card>
      {adding && <AddSiteDialog kind={adding} onClose={() => setAdding(null)} />}
    </div>
  );
}
