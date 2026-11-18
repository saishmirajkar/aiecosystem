import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Boxes, Coins, ClipboardList, PackageCheck, PackageSearch, Sparkles, Truck, Warehouse } from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Link, useNavigate } from "react-router-dom";
import { Button, Card, CardHeader, Skeleton } from "@/components/ui/primitives";
import { EmptyState, ErrorState, PageHeader, Stat, StatusBadge } from "@/components/ui/page";
import { Can, useAuth } from "@/hooks/auth";
import { api } from "@/lib/api";
import { dateTime, label, money, qty, relative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Overview } from "@/types/api";

const CHART_COLORS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--chart-6)"];

function ChartTooltip({ active, payload, label: l, formatter }: { active?: boolean; payload?: { name: string; value: number; color: string }[]; label?: string; formatter: (v: number) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg">
      {l && <div className="mb-1 text-muted">{l}</div>}
      {payload.map((p) => <div key={p.name} className="flex items-center gap-2"><span className="size-2 rounded-full" style={{ background: p.color }} />{p.name}: <span className="num">{formatter(p.value)}</span></div>)}
    </div>
  );
}

export function DashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["overview"], queryFn: () => api.get<Overview>("/inventory/overview"), refetchInterval: 60_000 });
  const hour = new Date().getHours();
  const greet = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";

  if (error) return <ErrorState error={error} onRetry={refetch} />;
  const m = data?.metrics;
  const trend = data?.trend.map((t) => ({ date: t.date.slice(5), Inbound: Number(t.inbound_value), Outbound: Number(t.outbound_value) })) ?? [];
  const dist = data?.distribution.map((d) => ({ name: label(d.product_type), value: Number(d.value) })).sort((a, b) => b.value - a.value) ?? [];

  return (
    <div>
      <PageHeader
        title={`${greet}, ${user?.full_name.split(" ")[0]}`}
        description={data ? `Operational overview for ${user?.company?.name ?? "your company"} · updated ${relative(data.as_of)}` : "Loading operational overview…"}
        actions={<Can permission="ai.access"><Button variant="primary" onClick={() => navigate("/ai")}><Sparkles /> Open Command Center</Button></Can>}
      />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {isLoading || !m ? Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-[118px] rounded-xl" />) : <>
          <Stat label="Stock value" value={money(m.total_stock_value, { compact: true })} hint="Weighted average cost" icon={<Coins />} to="/inventory/reports?report=inventory_valuation" />
          <Stat label="Active products" value={qty(m.active_products)} icon={<PackageSearch />} to="/inventory/products" />
          <Stat label="Warehouses" value={qty(m.warehouse_count)} icon={<Warehouse />} to="/inventory/warehouses" />
          <Stat label="Low stock" value={qty(m.low_stock_items)} tone={m.low_stock_items ? "warn" : "ok"} hint="At/below reorder level" icon={<AlertTriangle />} to="/inventory/alerts" />
          <Stat label="Awaiting delivery" value={qty(m.pos_awaiting_delivery)} hint={`${m.pending_goods_receipts} draft GRN${m.pending_goods_receipts === 1 ? "" : "s"} to confirm`} icon={<PackageCheck />} to="/inventory/receipts" />
          <Stat label="POs to approve" value={qty(m.pending_purchase_orders)} hint="Draft or pending approval" icon={<ClipboardList />} to="/purchasing/orders" />
        </>}
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Inventory movement trend" description="Daily inbound vs outbound value, last 30 days (excludes internal transfers)" />
          <div className="h-[260px] px-2 py-3">
            {isLoading ? <Skeleton className="h-full w-full" /> : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={trend} margin={{ left: 8, right: 12, top: 8 }}>
                  <defs>
                    <linearGradient id="gIn" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.35} /><stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} /></linearGradient>
                    <linearGradient id="gOut" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--chart-2)" stopOpacity={0.25} /><stop offset="100%" stopColor="var(--chart-2)" stopOpacity={0} /></linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--line)" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 11, fill: "var(--muted)" }} tickLine={false} axisLine={false} interval={4} />
                  <YAxis tick={{ fontSize: 11, fill: "var(--muted)" }} tickLine={false} axisLine={false} width={64} tickFormatter={(v: number) => money(v, { compact: true })} />
                  <Tooltip content={<ChartTooltip formatter={(v) => money(v)} />} />
                  <Area type="monotone" dataKey="Inbound" stroke="var(--chart-1)" strokeWidth={1.75} fill="url(#gIn)" animationDuration={500} />
                  <Area type="monotone" dataKey="Outbound" stroke="var(--chart-2)" strokeWidth={1.75} fill="url(#gOut)" animationDuration={500} />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title="Inventory distribution" description="On-shelf value by product type" />
          <div className="h-[260px] px-2 py-3">
            {isLoading ? <Skeleton className="h-full w-full" /> : dist.length === 0 ? <EmptyState title="No stock yet" /> : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={dist} layout="vertical" margin={{ left: 8, right: 16 }}>
                  <XAxis type="number" hide />
                  <YAxis type="category" dataKey="name" width={112} tick={{ fontSize: 11, fill: "var(--muted)" }} tickLine={false} axisLine={false} />
                  <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip formatter={(v) => money(v)} />} />
                  <Bar dataKey="value" name="Value" radius={[0, 4, 4, 0]} barSize={16} animationDuration={500}>
                    {dist.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Recent stock movements" actions={<Link to="/inventory/movements" className="inline-flex items-center gap-1 text-xs text-muted hover:text-fg">View ledger <ArrowRight className="size-3" /></Link>} />
          {isLoading ? <div className="space-y-2 p-4">{[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-8" />)}</div> :
            !data?.recent_movements.length ? <EmptyState title="No movements yet" description="Post opening balances or confirm a goods receipt to start the ledger." icon={<Boxes />} /> : (
              <ul className="divide-y divide-line">
                {data.recent_movements.map((mv) => (
                  <li key={mv.id} className="flex items-center gap-3 px-5 py-2.5 text-sm">
                    <span className={cn("num w-20 shrink-0 text-right", Number(mv.quantity) < 0 ? "text-bad" : "text-ok")}>{Number(mv.quantity) > 0 ? "+" : ""}{qty(mv.quantity)}</span>
                    <span className="w-10 shrink-0 text-xs text-muted">{mv.uom_code}</span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate">{mv.product_name}</div>
                      <div className="truncate text-xs text-muted">{label(mv.movement_type)} · {mv.warehouse_code}/{mv.location_path} · {mv.reference_number ?? mv.transaction_number}</div>
                    </div>
                    <span className="hidden shrink-0 text-xs text-muted sm:block">{dateTime(mv.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
        </Card>
        <Card>
          <CardHeader title="Critical alerts" actions={<Link to="/inventory/alerts" className="inline-flex items-center gap-1 text-xs text-muted hover:text-fg">All alerts <ArrowRight className="size-3" /></Link>} />
          {isLoading ? <div className="space-y-2 p-4">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-12" />)}</div> :
            !data?.critical_alerts.length ? <EmptyState title="No alerts" description="Stock levels, deliveries and transfers are within limits." icon={<Truck />} /> : (
              <ul className="divide-y divide-line">
                {data.critical_alerts.map((a) => (
                  <li key={a.id} className="px-5 py-3">
                    <div className="flex items-start justify-between gap-2">
                      <span className="text-[13px] font-medium leading-snug">{a.title}</span>
                      <StatusBadge status={a.severity === "critical" ? "critical_sev" : a.severity} />
                    </div>
                    <p className="mt-0.5 text-xs text-muted">{a.detail}</p>
                  </li>
                ))}
              </ul>
            )}
        </Card>
      </div>
      {data && data.value_by_warehouse.length > 0 && (
        <Card className="mt-4">
          <CardHeader title="Stock value by warehouse" />
          <div className="grid gap-px bg-line sm:grid-cols-2 lg:grid-cols-4">
            {data.value_by_warehouse.map((w) => (
              <Link key={w.warehouse_id} to={`/inventory/warehouses/${w.warehouse_id}`} className="bg-surface px-5 py-4 hover:bg-surface-2">
                <div className="text-xs text-muted">{w.warehouse_code} · {w.warehouse_name}</div>
                <div className="num mt-1 text-lg">{money(w.value)}</div>
              </Link>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
