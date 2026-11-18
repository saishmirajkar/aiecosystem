import { useQuery } from "@tanstack/react-query";
import { Pencil, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useParams } from "react-router-dom";
import { ProductImage } from "@/components/Attachments";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/overlay";
import { DetailGrid, ErrorState, PageHeader, SearchInput, StatusBadge } from "@/components/ui/page";
import { Badge, Button, Card, CardHeader, Checkbox, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useCategories, useDebounced, useSuppliers, useUnits } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { api, ApiError, type ApiErrorBody } from "@/lib/api";
import { dateTime, label, money, qty } from "@/lib/format";
import { cn } from "@/lib/utils";
import { PRODUCT_TYPES, type Balance, type Movement, type Page, type Product, type ProductSupplier } from "@/types/api";

export function ProductsPage() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [category, setCategory] = useState("");
  const [active, setActive] = useState("true");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState("name");
  const [creating, setCreating] = useState(false);
  const dq = useDebounced(q);
  const cats = useCategories();
  useEffect(() => setPage(1), [dq, type, category, active]);
  const list = useQuery({
    queryKey: ["products", { dq, type, category, active, page, sort }],
    queryFn: () => api.get<Page<Product>>("/products", { q: dq, product_type: type, category_id: category, is_active: active === "all" ? undefined : active, page, page_size: 25, sort }),
    placeholderData: (prev) => prev,
  });
  const cols: Column<Product>[] = [
    { key: "sku", header: "SKU", sortKey: "sku", cell: (p) => <span className="font-mono text-xs">{p.sku}</span> },
    { key: "name", header: "Product", sortKey: "name", cell: (p) => <div><div className="font-medium">{p.name}</div><div className="text-xs text-muted">{p.category_name ?? "Uncategorised"}</div></div> },
    { key: "type", header: "Type", hideBelow: "md", cell: (p) => <span className="text-xs text-fg-2">{label(p.product_type)}</span> },
    { key: "available", header: "Available", align: "right", cell: (p) => <span className="num">{qty(p.stock?.available)} <span className="text-xs text-muted">{p.uom_code}</span></span> },
    { key: "reorder", header: "Reorder at", align: "right", hideBelow: "lg", cell: (p) => <span className="num text-fg-2">{qty(p.reorder_level)}</span> },
    { key: "value", header: "Value", align: "right", hideBelow: "lg", cell: (p) => <span className="num">{money(p.stock?.value)}</span> },
    { key: "status", header: "Status", cell: (p) => p.is_active ? <StatusBadge status={p.stock?.status ?? "ok"} /> : <Badge>Inactive</Badge> },
  ];
  return (
    <div>
      <PageHeader title="Products" description="Raw materials, components, spare parts, consumables and finished goods."
        actions={<Can permission="products.create"><Button variant="primary" onClick={() => setCreating(true)}><Plus /> New product</Button></Can>} />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <SearchInput value={q} onChange={setQ} placeholder="Search name, SKU, code, barcode…" className="min-w-56 flex-1" />
          <Select value={type} onChange={(e) => setType(e.target.value)} className="w-44" aria-label="Product type">
            <option value="">All types</option>{PRODUCT_TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}
          </Select>
          <Select value={category} onChange={(e) => setCategory(e.target.value)} className="w-44" aria-label="Category">
            <option value="">All categories</option>{cats.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
          <Select value={active} onChange={(e) => setActive(e.target.value)} className="w-32" aria-label="Status">
            <option value="true">Active</option><option value="false">Inactive</option><option value="all">All</option>
          </Select>
        </div>
        <DataTable columns={cols} rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(p) => p.id}
          onRowClick={(p) => navigate(`/inventory/products/${p.id}`)} sort={sort} onSort={setSort}
          page={page} pageSize={25} total={list.data?.total} onPage={setPage} />
      </Card>
      {creating && <ProductFormDialog open onOpenChange={setCreating} onSaved={(p) => navigate(`/inventory/products/${p.id}`)} />}
    </div>
  );
}

interface ProductForm {
  sku: string; name: string; internal_code: string; barcode: string; description: string; category_id: string; product_type: string;
  uom_id: string; manufacturer: string; purchase_price: string; standard_cost: string; selling_price: string; min_stock_level: string;
  max_stock_level: string; reorder_level: string; reorder_quantity: string; track_batches: boolean; track_serials: boolean;
  track_expiry: boolean; is_active: boolean;
}

export function ProductFormDialog({ open, onOpenChange, product, onSaved }: {
  open: boolean; onOpenChange: (o: boolean) => void; product?: Product; onSaved?: (p: Product) => void;
}) {
  const units = useUnits();
  const cats = useCategories();
  const editing = !!product;
  const { register, handleSubmit, setError, formState: { errors }, watch } = useForm<ProductForm>({
    defaultValues: product ? {
      ...product, internal_code: product.internal_code ?? "", barcode: product.barcode ?? "", description: product.description ?? "",
      category_id: product.category_id ?? "", manufacturer: product.manufacturer ?? "", selling_price: product.selling_price ?? "",
      max_stock_level: product.max_stock_level ?? "",
    } : { product_type: "raw_material", purchase_price: "0", standard_cost: "0", min_stock_level: "0", reorder_level: "0", reorder_quantity: "0", is_active: true },
  });
  const save = useAction(async (f: ProductForm) => {
    const body: Record<string, unknown> = {
      name: f.name, internal_code: f.internal_code || null, barcode: f.barcode || null, description: f.description || null,
      category_id: f.category_id || null, product_type: f.product_type, manufacturer: f.manufacturer || null,
      purchase_price: f.purchase_price || "0", standard_cost: f.standard_cost || "0", selling_price: f.selling_price || null,
      min_stock_level: f.min_stock_level || "0", max_stock_level: f.max_stock_level || null, reorder_level: f.reorder_level || "0",
      reorder_quantity: f.reorder_quantity || "0", track_expiry: f.track_expiry,
    };
    if (editing) return api.patch<Product>(`/products/${product.id}`, { ...body, is_active: f.is_active });
    return api.post<Product>("/products", { ...body, sku: f.sku, uom_id: f.uom_id, track_batches: f.track_batches, track_serials: f.track_serials });
  }, {
    success: editing ? "Product updated" : "Product created", invalidate: [["products"], ["product"]],
    onSuccess: (p) => { onOpenChange(false); onSaved?.(p); },
    onError: (e) => {
      if (e instanceof ApiError) {
        const fe = e.fieldErrors();
        const field = (e.details as { field?: string } | null)?.field;
        if (field) setError(field as keyof ProductForm, { message: e.message });
        Object.entries(fe).forEach(([k, v]) => setError(k as keyof ProductForm, { message: v }));
        if (!field && !Object.keys(fe).length) setError("root", { message: e.message });
      } else setError("root", { message: (e as ApiErrorBody).message ?? "Save failed" });
    },
  });
  const trackBatches = watch("track_batches");
  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="lg" title={editing ? `Edit ${product.sku}` : "New product"}
      description="Costs are purchase/standard costs used for valuation; selling price is never used to value inventory."
      footer={<><Button onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" loading={save.isPending} onClick={handleSubmit((f) => save.mutate(f))}>{editing ? "Save changes" : "Create product"}</Button></>}>
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => e.preventDefault()}>
        <Field label="SKU" htmlFor="sku" error={errors.sku?.message} required>
          <Input id="sku" disabled={editing} {...register("sku", { required: "SKU is required" })} aria-invalid={!!errors.sku} />
        </Field>
        <Field label="Name" htmlFor="name" error={errors.name?.message} required><Input id="name" {...register("name", { required: "Name is required" })} aria-invalid={!!errors.name} /></Field>
        <Field label="Product type" htmlFor="ptype"><Select id="ptype" {...register("product_type")}>{PRODUCT_TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}</Select></Field>
        <Field label="Category" htmlFor="cat"><Select id="cat" {...register("category_id")}><option value="">Uncategorised</option>{cats.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
        <Field label="Unit of measure" htmlFor="uom" error={errors.uom_id?.message} required hint={editing ? "Unit cannot change after creation" : undefined}>
          <Select id="uom" disabled={editing} {...register("uom_id", { required: !editing && "Unit is required" })}>
            <option value="">Select…</option>{units.data?.map((u) => <option key={u.id} value={u.id}>{u.code} — {u.name}</option>)}
          </Select>
        </Field>
        <Field label="Manufacturer" htmlFor="mfr"><Input id="mfr" {...register("manufacturer")} /></Field>
        <Field label="Internal code" htmlFor="ic" error={errors.internal_code?.message}><Input id="ic" {...register("internal_code")} /></Field>
        <Field label="Barcode" htmlFor="bc" error={errors.barcode?.message}><Input id="bc" {...register("barcode")} /></Field>
        <Field label="Purchase price" htmlFor="pp" error={errors.purchase_price?.message}><Input id="pp" inputMode="decimal" {...register("purchase_price")} /></Field>
        <Field label="Standard cost" htmlFor="sc" error={errors.standard_cost?.message}><Input id="sc" inputMode="decimal" {...register("standard_cost")} /></Field>
        <Field label="Selling price (optional)" htmlFor="sp"><Input id="sp" inputMode="decimal" {...register("selling_price")} /></Field>
        <div />
        <Field label="Minimum level" htmlFor="min"><Input id="min" inputMode="decimal" {...register("min_stock_level")} /></Field>
        <Field label="Maximum level" htmlFor="max" error={errors.max_stock_level?.message}><Input id="max" inputMode="decimal" {...register("max_stock_level")} /></Field>
        <Field label="Reorder level" htmlFor="rl"><Input id="rl" inputMode="decimal" {...register("reorder_level")} /></Field>
        <Field label="Reorder quantity" htmlFor="rq"><Input id="rq" inputMode="decimal" {...register("reorder_quantity")} /></Field>
        <Field label="Description" htmlFor="desc" className="sm:col-span-2"><Textarea id="desc" {...register("description")} /></Field>
        <div className="flex flex-wrap gap-x-6 gap-y-2 sm:col-span-2">
          <Checkbox label="Batch tracking" disabled={editing} {...register("track_batches")} />
          <Checkbox label="Expiry tracking" disabled={!trackBatches && !editing} {...register("track_expiry")} />
          <Checkbox label="Serial tracking" disabled={editing} {...register("track_serials")} />
          {editing && <Checkbox label="Active" {...register("is_active")} />}
        </div>
        {errors.root && <p className="text-sm text-bad sm:col-span-2" role="alert">{errors.root.message}</p>}
      </form>
    </Dialog>
  );
}

function SuppliersEditor({ product }: { product: Product }) {
  const { can } = useAuth();
  const links = useQuery({ queryKey: ["product", product.id, "suppliers"], queryFn: () => api.get<ProductSupplier[]>(`/products/${product.id}/suppliers`) });
  const all = useSuppliers();
  const [adding, setAdding] = useState("");
  const save = useAction((items: { supplier_id: string; is_preferred: boolean; supplier_sku?: string | null; lead_time_days?: number | null; last_purchase_price?: string | null }[]) =>
    api.put<ProductSupplier[]>(`/products/${product.id}/suppliers`, items), { success: "Suppliers updated", invalidate: [["product", product.id, "suppliers"]] });
  const current = links.data ?? [];
  const toPayload = (rows: ProductSupplier[]) => rows.map((r) => ({ supplier_id: r.supplier_id, is_preferred: r.is_preferred, supplier_sku: r.supplier_sku, lead_time_days: r.lead_time_days, last_purchase_price: r.last_purchase_price }));
  return (
    <Card>
      <CardHeader title="Suppliers" description="Referenced by purchasing; last purchase price updates on each confirmed GRN." />
      {links.isLoading ? <Skeleton className="m-4 h-10" /> : current.length === 0 ? <p className="px-5 py-4 text-sm text-muted">No suppliers linked.</p> : (
        <ul className="divide-y divide-line">
          {current.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-3 px-5 py-2.5 text-sm">
              <div><div className="font-medium">{r.supplier_name} {r.is_preferred && <Badge tone="accent">Preferred</Badge>}</div>
                <div className="text-xs text-muted">{r.supplier_code} · last price {money(r.last_purchase_price)} · lead time {r.lead_time_days ?? "—"} days</div></div>
              {can("products.edit") && (
                <div className="flex gap-1">
                  {!r.is_preferred && <Button size="sm" variant="ghost" onClick={() => save.mutate(toPayload(current.map((x) => ({ ...x, is_preferred: x.id === r.id }))))}>Make preferred</Button>}
                  <Button size="sm" variant="ghost" onClick={() => save.mutate(toPayload(current.filter((x) => x.id !== r.id)))}>Remove</Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {can("products.edit") && (
        <div className="flex gap-2 border-t border-line p-3">
          <Select value={adding} onChange={(e) => setAdding(e.target.value)} aria-label="Add supplier">
            <option value="">Link a supplier…</option>
            {all.data?.items.filter((s) => !current.some((c) => c.supplier_id === s.id)).map((s) => <option key={s.id} value={s.id}>{s.code} — {s.name}</option>)}
          </Select>
          <Button disabled={!adding} loading={save.isPending} onClick={() => { save.mutate([...toPayload(current), { supplier_id: adding, is_preferred: current.length === 0 }]); setAdding(""); }}>Add</Button>
        </div>
      )}
    </Card>
  );
}

export function ProductDetailPage() {
  const { id } = useParams();
  const { can } = useAuth();
  const [editing, setEditing] = useState(false);
  const stock = useQuery({ queryKey: ["product", id, "stock"], queryFn: () => api.get<{ product: Product; balances: Balance[] }>(`/inventory/products/${id}/stock`) });
  const moves = useQuery({ queryKey: ["product", id, "movements"], queryFn: () => api.get<Page<Movement>>("/inventory/movements", { product_id: id, page_size: 15 }) });
  if (stock.error) return <ErrorState error={stock.error} onRetry={stock.refetch} />;
  const p = stock.data?.product;
  if (!p) return <Skeleton className="h-64" />;
  const s = p.stock!;
  return (
    <div className="space-y-4">
      <PageHeader breadcrumb={[{ label: "Products", to: "/inventory/products" }]} title={<span className="flex items-center gap-3">{p.name} <StatusBadge status={p.is_active ? s.status : "cancelled"} /></span>}
        description={<span className="font-mono">{p.sku}{p.internal_code ? ` · ${p.internal_code}` : ""}{p.barcode ? ` · ${p.barcode}` : ""}</span>}
        actions={<Can permission="products.edit"><Button onClick={() => setEditing(true)}><Pencil /> Edit</Button></Can>} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        {[["Physical", s.physical], ["Available", s.available], ["Reserved", s.reserved], ["In transit", s.in_transit], ["Damaged", s.damaged]].map(([l, v]) => (
          <Card key={l} className="p-4"><div className="text-xs text-muted">{l}</div><div className="num mt-1 text-xl">{qty(v)} <span className="text-xs text-muted">{p.uom_code}</span></div></Card>
        ))}
        <Card className="p-4"><div className="text-xs text-muted">Value · avg {money(s.average_cost)}</div><div className="num mt-1 text-xl">{money(s.value)}</div></Card>
      </div>
      <Card>
        <CardHeader title="Master data" />
        <div className="flex flex-col gap-6 p-5 sm:flex-row">
        <ProductImage productId={p.id} attachmentId={p.image_attachment_id} canEdit={can("products.edit")} onChanged={() => stock.refetch()} />
        <div className="min-w-0 flex-1"><DetailGrid items={[
          { label: "Type", value: label(p.product_type) }, { label: "Category", value: p.category_name }, { label: "Unit", value: p.uom_code },
          { label: "Manufacturer", value: p.manufacturer }, { label: "Purchase price", value: money(p.purchase_price) },
          { label: "Standard cost", value: money(p.standard_cost) }, { label: "Selling price", value: money(p.selling_price) },
          { label: "Min / reorder / max", value: <span className="num">{qty(p.min_stock_level)} / {qty(p.reorder_level)} / {qty(p.max_stock_level)}</span> },
          { label: "Reorder quantity", value: <span className="num">{qty(p.reorder_quantity)}</span> },
          { label: "Tracking", value: [p.track_batches && "Batch", p.track_expiry && "Expiry", p.track_serials && "Serial"].filter(Boolean).join(", ") || "None" },
          { label: "Updated", value: dateTime(p.updated_at) },
        ]} />{p.description && <p className="mt-4 text-sm text-fg-2">{p.description}</p>}</div>
        </div>
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Stock by location" description="Exact warehouse → location → batch positions" />
          <DataTable dense rows={stock.data?.balances} rowKey={(b) => b.id} columns={[
            { key: "wh", header: "Warehouse", cell: (b) => b.warehouse_code },
            { key: "loc", header: "Location", cell: (b) => <span className="font-mono text-xs">{b.location_path}</span> },
            { key: "batch", header: "Batch", cell: (b) => b.batch_number ?? "—" },
            { key: "st", header: "Status", cell: (b) => <StatusBadge status={b.stock_status} /> },
            { key: "oh", header: "On hand", align: "right", cell: (b) => <span className="num">{qty(b.qty_on_hand)}</span> },
            { key: "av", header: "Available", align: "right", cell: (b) => <span className="num">{qty(b.qty_available)}</span> },
          ]} />
        </Card>
        <SuppliersEditor product={p} />
      </div>
      <Card>
        <CardHeader title="Recent movements" description="From the immutable stock ledger" />
        <DataTable dense rows={moves.data?.items} loading={moves.isLoading} rowKey={(m) => m.id} columns={[
          { key: "d", header: "Date", cell: (m) => <span className="text-xs">{dateTime(m.created_at)}</span> },
          { key: "t", header: "Type", cell: (m) => label(m.movement_type) },
          { key: "w", header: "Location", cell: (m) => <span className="text-xs">{m.warehouse_code}/{m.location_path}</span> },
          { key: "q", header: "Qty", align: "right", cell: (m) => <span className={cn("num", Number(m.quantity) < 0 ? "text-bad" : "text-ok")}>{qty(m.quantity)}</span> },
          { key: "b", header: "Balance", align: "right", cell: (m) => <span className="num">{qty(m.balance_after)}</span> },
          { key: "r", header: "Reference", hideBelow: "md", cell: (m) => m.reference_number ?? m.transaction_number },
          { key: "u", header: "By", hideBelow: "lg", cell: (m) => m.performed_by_name },
        ]} />
      </Card>
      {editing && <ProductFormDialog open product={p} onOpenChange={setEditing} />}
    </div>
  );
}
