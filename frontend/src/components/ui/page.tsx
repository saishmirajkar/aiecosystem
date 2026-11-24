import { AlertTriangle, ChevronLeft, ChevronRight, Inbox, Search } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { errorMessage } from "@/lib/api";
import { label as fmtLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge, Button, Input, type Tone } from "./primitives";

export function PageHeader({ title, description, actions, breadcrumb }: {
  title: ReactNode; description?: ReactNode; actions?: ReactNode; breadcrumb?: { label: string; to: string }[];
}) {
  return (
    <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {breadcrumb && (
          <nav className="mb-1.5 flex items-center gap-1.5 text-xs text-muted" aria-label="Breadcrumb">
            {breadcrumb.map((b, i) => (
              <span key={b.to} className="flex items-center gap-1.5">
                {i > 0 && <span>/</span>}
                <Link to={b.to} className="hover:text-fg">{b.label}</Link>
              </span>
            ))}
          </nav>
        )}
        <h1 className="text-[22px] font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({ title, description, action, icon }: { title: string; description?: string; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <div className="mb-3 rounded-full border border-line bg-surface-2 p-3 text-muted [&_svg]:size-5">{icon ?? <Inbox />}</div>
      <p className="text-sm font-medium">{title}</p>
      {description && <p className="mt-1 max-w-sm text-sm text-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center" role="alert">
      <div className="mb-3 rounded-full bg-bad-soft p-3 text-bad"><AlertTriangle className="size-5" /></div>
      <p className="text-sm font-medium">Couldn’t load this data</p>
      <p className="mt-1 max-w-md text-sm text-muted">{errorMessage(error)}</p>
      {onRetry && <Button className="mt-4" size="sm" onClick={onRetry}>Try again</Button>}
    </div>
  );
}

export function Stat({ label, value, hint, tone, icon, to }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "bad" | "warn" | "ok"; icon?: ReactNode; to?: string }) {
  const body = (
    <div className={cn("group flex h-full flex-col justify-between rounded-xl border border-line bg-surface p-4 shadow-[var(--shadow)] transition-colors", to && "hover:border-line-strong")}>
      <div className="flex items-center justify-between text-muted">
        <span className="text-xs font-medium uppercase tracking-wider">{label}</span>
        <span className="[&_svg]:size-4">{icon}</span>
      </div>
      <div className={cn("num mt-3 text-[26px] font-medium leading-none tracking-tight", tone === "bad" && "text-bad", tone === "warn" && "text-warn", tone === "ok" && "text-ok")}>{value}</div>
      {hint && <div className="mt-2 text-xs text-muted">{hint}</div>}
    </div>
  );
  return to ? <Link to={to} className="block h-full">{body}</Link> : body;
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total === 0) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-2.5 text-xs text-muted">
      <span className="num">{from}–{to} of {total}</span>
      <div className="flex items-center gap-1">
        <Button size="icon" variant="ghost" className="h-7 w-7" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page"><ChevronLeft /></Button>
        <span className="num px-2">{page} / {pages}</span>
        <Button size="icon" variant="ghost" className="h-7 w-7" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page"><ChevronRight /></Button>
      </div>
    </div>
  );
}

export function SearchInput({ value, onChange, placeholder = "Search…", className }: { value: string; onChange: (v: string) => void; placeholder?: string; className?: string }) {
  return (
    <div className={cn("relative", className)}>
      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted" />
      <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className="pl-8" aria-label={placeholder} />
    </div>
  );
}

const STATUS_TONES: Record<string, Tone> = {
  draft: "neutral", pending_approval: "warn", pending: "warn", approved: "info", sent: "info", in_transit: "info",
  partially_received: "accent", fully_received: "ok", completed: "ok", posted: "ok", confirmed: "ok", executed: "ok", converted: "ok",
  closed: "neutral", cancelled: "neutral", rejected: "bad", failed: "bad", expired: "neutral", active: "ok", released: "neutral",
  ok: "ok", low: "warn", critical: "bad", out: "bad", overstock: "info", unrestricted: "ok", damaged: "bad", returned: "warn",
  critical_sev: "bad", warning: "warn", info: "info", in_stock: "ok", issued: "neutral",
  // workforce
  published: "info", open: "accent", partially_filled: "accent", filled: "ok", assigned: "ok", on_leave: "warn", won: "ok", lost: "neutral",
  present: "ok", absent: "bad", late: "warn", half_day: "warn", holiday: "info", weekly_off: "neutral", calculated: "warn", exported: "neutral",
  working: "accent", not_checked_in: "bad", scheduled: "neutral", resolved: "ok", suspended: "warn", inactive: "neutral", terminated: "neutral",
};
export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const text = status === "out" ? "Out of stock" : fmtLabel(status);
  return <Badge tone={STATUS_TONES[status] ?? "neutral"} className={className}>{text}</Badge>;
}

export function DetailGrid({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-4">
      {items.map((it) => (
        <div key={it.label} className="min-w-0">
          <dt className="text-xs text-muted">{it.label}</dt>
          <dd className="mt-0.5 truncate text-sm">{it.value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}
