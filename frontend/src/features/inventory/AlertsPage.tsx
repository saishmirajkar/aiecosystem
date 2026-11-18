import { useQuery } from "@tanstack/react-query";
import { BellRing } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState, ErrorState, PageHeader, StatusBadge } from "@/components/ui/page";
import { Button, Card, Skeleton } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { label } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Alert } from "@/types/api";

const LINKS: Record<string, (id: string) => string> = {
  product: (id) => `/inventory/products/${id}`, purchase_order: (id) => `/purchasing/orders/${id}`,
  stock_transfer: (id) => `/inventory/transfers/${id}`, stock_adjustment: (id) => `/inventory/adjustments/${id}`,
};
const TYPES = ["critical_stock", "low_stock", "overstock", "expiring_stock", "pending_receipt", "unconfirmed_transfer", "stock_discrepancy", "inactive_stock"];

export function AlertsPage() {
  const [type, setType] = useState<string | null>(null);
  const { data, isLoading, error, refetch, isFetching } = useQuery({ queryKey: ["alerts"], queryFn: () => api.get<{ items: Alert[]; counts: Record<string, number>; total: number }>("/inventory/alerts") });
  const items = (data?.items ?? []).filter((a) => !type || a.type === type);
  return (
    <div>
      <PageHeader title="Inventory alerts" description="Computed live from stock levels, batches, purchase orders, transfers and counts. Configure thresholds in System Settings; notification preferences in your profile."
        actions={<><Button onClick={() => refetch()} loading={isFetching}>Refresh</Button><Link to="/profile" className="text-sm text-muted hover:text-fg">Notification preferences</Link></>} />
      <div className="mb-4 flex flex-wrap gap-2">
        <button type="button" onClick={() => setType(null)} className={cn("rounded-full border px-3 py-1 text-xs", !type ? "border-fg bg-fg text-bg" : "border-line bg-surface text-fg-2 hover:border-line-strong")}>All · {data?.total ?? 0}</button>
        {TYPES.map((t) => (
          <button key={t} type="button" onClick={() => setType(t)} disabled={!data?.counts[t]}
            className={cn("rounded-full border px-3 py-1 text-xs disabled:opacity-40", type === t ? "border-fg bg-fg text-bg" : "border-line bg-surface text-fg-2 hover:border-line-strong")}>
            {label(t)} · {data?.counts[t] ?? 0}
          </button>
        ))}
      </div>
      <Card>
        {error ? <ErrorState error={error} onRetry={refetch} /> : isLoading ? <div className="space-y-2 p-4">{[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-14" />)}</div> :
          items.length === 0 ? <EmptyState title="No active alerts" description="Everything is within configured limits." icon={<BellRing />} /> : (
            <ul className="divide-y divide-line">
              {items.map((a) => {
                const to = LINKS[a.entity_type]?.(a.entity_id);
                return (
                  <li key={a.id} className="flex items-start gap-4 px-5 py-3.5">
                    <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", a.severity === "critical" ? "bg-bad" : a.severity === "warning" ? "bg-warn" : "bg-info")} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-medium">{a.title}</span><StatusBadge status={a.severity === "critical" ? "critical_sev" : a.severity} /><span className="text-xs text-muted">{label(a.type)}</span></div>
                      <p className="mt-0.5 text-sm text-muted">{a.detail}</p>
                    </div>
                    {to && <Link to={to} className="shrink-0 text-xs text-accent hover:underline">Open</Link>}
                  </li>
                );
              })}
            </ul>
          )}
      </Card>
    </div>
  );
}
