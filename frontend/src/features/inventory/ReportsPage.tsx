import { useQuery } from "@tanstack/react-query";
import { Download, FileBarChart } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ProductPicker, WarehouseSelect } from "@/components/pickers";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState, ErrorState, PageHeader } from "@/components/ui/page";
import { buttonVariants, Card, CardHeader, Field, Input, Select, Skeleton } from "@/components/ui/primitives";
import { useCategories, useSuppliers } from "@/hooks/data";
import { api } from "@/lib/api";
import { dateTime, qty } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Product, ReportMeta, ReportResult } from "@/types/api";

function cellValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v)) return qty(v, 4);
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v)) return dateTime(v);
  return String(v);
}

export function ReportsPage() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("report") ?? "current_stock";
  const [filters, setFilters] = useState({ date_from: "", date_to: "", warehouse_id: "", supplier_id: "", category_id: "" });
  const [product, setProduct] = useState<Product | null>(null);
  const [page, setPage] = useState(1);
  const catalogue = useQuery({ queryKey: ["reports"], queryFn: () => api.get<ReportMeta[]>("/reports"), staleTime: Infinity });
  const suppliers = useSuppliers();
  const cats = useCategories();
  const meta = catalogue.data?.find((r) => r.id === selected);
  const query = { ...filters, product_id: product?.id ?? "" };
  useEffect(() => setPage(1), [selected, JSON.stringify(query)]);
  const needsProduct = selected === "product_history" && !product;
  const result = useQuery({
    queryKey: ["report", selected, query, page], enabled: !!meta && !needsProduct, placeholderData: (p) => p,
    queryFn: () => api.get<ReportResult>(`/reports/${selected}`, { ...query, page, page_size: 100 }),
  });
  const has = (f: string) => meta?.filters.includes(f);
  return (
    <div>
      <PageHeader title="Reports" description="Every report reads live ledger and document data. Filter, review and export to CSV." />
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <Card className="h-fit">
          <CardHeader title="Report catalogue" />
          {catalogue.isLoading ? <Skeleton className="m-3 h-40" /> : (
            <ul className="p-1.5">
              {catalogue.data?.map((r) => (
                <li key={r.id}>
                  <button type="button" onClick={() => setParams({ report: r.id })}
                    className={cn("w-full rounded-lg px-3 py-2 text-left text-[13px] hover:bg-surface-2", r.id === selected && "bg-surface-2 font-medium")}>{r.title}</button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card className="min-w-0">
          <CardHeader title={meta?.title ?? "Report"} description={meta?.description}
            actions={meta && !needsProduct && <a className={buttonVariants({ size: "sm" })} href={api.downloadUrl(`/reports/${selected}`, { ...query, format: "csv" })}><Download /> Export CSV</a>} />
          {meta && (
            <div className="grid gap-3 border-b border-line p-4 sm:grid-cols-2 xl:grid-cols-4">
              {has("date_from") && <Field label="From" htmlFor="rf"><Input id="rf" type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} /></Field>}
              {has("date_to") && <Field label="To" htmlFor="rt"><Input id="rt" type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} /></Field>}
              {has("warehouse_id") && <Field label="Warehouse" htmlFor="rw"><WarehouseSelect id="rw" value={filters.warehouse_id} onChange={(v) => setFilters({ ...filters, warehouse_id: v })} allowAll /></Field>}
              {has("supplier_id") && <Field label="Supplier" htmlFor="rs"><Select id="rs" value={filters.supplier_id} onChange={(e) => setFilters({ ...filters, supplier_id: e.target.value })}><option value="">All suppliers</option>{suppliers.data?.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select></Field>}
              {has("category_id") && <Field label="Category" htmlFor="rc"><Select id="rc" value={filters.category_id} onChange={(e) => setFilters({ ...filters, category_id: e.target.value })}><option value="">All categories</option>{cats.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>}
              {has("product_id") && <Field label={selected === "product_history" ? "Product *" : "Product"} htmlFor="rp" className="sm:col-span-2"><ProductPicker id="rp" value={product} onChange={setProduct} /></Field>}
            </div>
          )}
          {needsProduct ? <EmptyState title="Choose a product" description="Product history needs a product." icon={<FileBarChart />} /> :
            result.error ? <ErrorState error={result.error} onRetry={result.refetch} /> : (
              <>
                <DataTable dense rows={result.data?.rows} loading={result.isLoading} rowKey={(r) => JSON.stringify(r)}
                  columns={(result.data?.columns ?? meta?.columns ?? []).map((c) => ({ key: c.key, header: c.label,
                    align: ["physical", "available", "reserved", "damaged", "in_transit", "value", "average_cost", "quantity", "unit_cost", "balance_after",
                      "accepted", "rejected", "delta", "system_qty", "counted_qty", "shortfall", "excess", "quantity_received", "receipts"].includes(c.key) ? "right" as const : "left" as const,
                    cell: (r: Record<string, unknown>) => <span className={cn(typeof r[c.key] === "string" && /^-?\d/.test(String(r[c.key])) && "num")}>{cellValue(r[c.key])}</span> }))}
                  empty={<EmptyState title="No rows for these filters" />} page={page} pageSize={100} total={result.data?.total} onPage={setPage} />
                {result.data && <p className="border-t border-line px-4 py-2 text-[11px] text-muted">Generated {dateTime(result.data.generated_at)} from live records.</p>}
              </>
            )}
        </Card>
      </div>
    </div>
  );
}
