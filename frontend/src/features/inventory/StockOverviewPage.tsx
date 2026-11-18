import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { WarehouseSelect } from "@/components/pickers";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader, SearchInput, StatusBadge } from "@/components/ui/page";
import { buttonVariants, Card, Select } from "@/components/ui/primitives";
import { useCategories, useDebounced } from "@/hooks/data";
import { api } from "@/lib/api";
import { date, money, qty, relative } from "@/lib/format";
import type { Balance, Page } from "@/types/api";

export function StockOverviewPage() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [wh, setWh] = useState("");
  const [status, setStatus] = useState("");
  const [cat, setCat] = useState("");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState("product");
  const dq = useDebounced(q);
  const cats = useCategories();
  useEffect(() => setPage(1), [dq, wh, status, cat]);
  const list = useQuery({
    queryKey: ["balances", { dq, wh, status, cat, page, sort }],
    queryFn: () => api.get<Page<Balance>>("/inventory/balances", { q: dq, warehouse_id: wh, stock_status: status, category_id: cat, page, page_size: 50, sort }),
    placeholderData: (prev) => prev,
  });
  return (
    <div>
      <PageHeader title="Stock overview" description="Physical, reserved and available stock at every warehouse, location and batch."
        actions={<a className={buttonVariants()} href={api.downloadUrl("/reports/warehouse_inventory", { format: "csv", warehouse_id: wh })}><Download /> Export CSV</a>} />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <SearchInput value={q} onChange={setQ} placeholder="Search product or SKU…" className="min-w-56 flex-1" />
          <div className="w-56"><WarehouseSelect value={wh} onChange={setWh} allowAll /></div>
          <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-40" aria-label="Stock status">
            <option value="">All statuses</option><option value="unrestricted">Unrestricted</option><option value="damaged">Damaged</option>
            <option value="returned">Returned</option><option value="in_transit">In transit</option>
          </Select>
          <Select value={cat} onChange={(e) => setCat(e.target.value)} className="w-44" aria-label="Category">
            <option value="">All categories</option>{cats.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
        </div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(b) => b.id}
          onRowClick={(b) => navigate(`/inventory/products/${b.product_id}`)} sort={sort} onSort={setSort}
          page={page} pageSize={50} total={list.data?.total} onPage={setPage}
          columns={[
            { key: "product", header: "Product", sortKey: "product", cell: (b) => <div><div className="font-medium">{b.product_name}</div><div className="font-mono text-xs text-muted">{b.product_sku}</div></div> },
            { key: "loc", header: "Warehouse / location", cell: (b) => <div><div>{b.warehouse_code}</div><div className="font-mono text-xs text-muted">{b.location_path}</div></div> },
            { key: "batch", header: "Batch", hideBelow: "md", cell: (b) => b.batch_number ? <div><div>{b.batch_number}</div>{b.expiry_date && <div className="text-xs text-muted">exp {date(b.expiry_date)}</div>}</div> : "—" },
            { key: "status", header: "Status", cell: (b) => <StatusBadge status={b.stock_status} /> },
            { key: "qty", header: "On hand", align: "right", sortKey: "qty", cell: (b) => <span className="num">{qty(b.qty_on_hand)} <span className="text-xs text-muted">{b.uom_code}</span></span> },
            { key: "res", header: "Reserved", align: "right", hideBelow: "lg", cell: (b) => <span className="num text-fg-2">{qty(b.qty_reserved)}</span> },
            { key: "avail", header: "Available", align: "right", cell: (b) => <span className="num font-medium">{qty(b.qty_available)}</span> },
            { key: "value", header: "Value", align: "right", hideBelow: "lg", cell: (b) => <span className="num">{money(b.value)}</span> },
            { key: "upd", header: "Last movement", sortKey: "updated", hideBelow: "lg", cell: (b) => <span className="text-xs text-muted">{relative(b.last_movement_at)}</span> },
          ]} />
      </Card>
    </div>
  );
}
