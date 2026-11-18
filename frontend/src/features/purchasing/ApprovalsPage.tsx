import { useQuery } from "@tanstack/react-query";
import { ClipboardCheck } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ConfirmDialog } from "@/components/ui/overlay";
import { EmptyState, ErrorState, PageHeader, StatusBadge } from "@/components/ui/page";
import { Button, Card, Select, Skeleton } from "@/components/ui/primitives";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { dateTime, label } from "@/lib/format";
import type { Approval, Page } from "@/types/api";

export function ApprovalsPage() {
  const [status, setStatus] = useState("pending");
  const [rejecting, setRejecting] = useState<Approval | null>(null);
  const list = useQuery({ queryKey: ["approvals", status], queryFn: () => api.get<Page<Approval> & { pending_count: number }>("/approvals", { status, page_size: 50 }) });
  const decide = useAction(({ a, kind, note }: { a: Approval; kind: "approve" | "reject"; note?: string }) => api.post<Approval>(`/approvals/${a.id}/${kind}`, { note: note ?? null }),
    { success: (r) => r.status === "executed" ? `Executed — ${r.result?.number ?? "record"} created` : r.status === "failed" ? `Failed: ${r.error}` : "Rejected",
      invalidate: [["approvals"], ["requisitions"], ["transfers"], ["adjustments"]], onSuccess: () => setRejecting(null) });
  return (
    <div>
      <PageHeader title="Action approvals" description="Actions proposed by the AI Command Center. Each approval is bound to its exact parameters and executes through the normal business workflow."
        actions={<Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-40" aria-label="Status">
          <option value="pending">Pending</option><option value="executed">Executed</option><option value="rejected">Rejected</option><option value="failed">Failed</option><option value="expired">Expired</option><option value="">All</option>
        </Select>} />
      {list.error ? <ErrorState error={list.error} onRetry={list.refetch} /> : list.isLoading ? <Skeleton className="h-40" /> :
        !list.data?.items.length ? <Card><EmptyState title="No approvals" description="When the assistant prepares a transfer, adjustment or requisition, it appears here." icon={<ClipboardCheck />} /></Card> : (
          <div className="space-y-3">
            {list.data.items.map((a) => (
              <Card key={a.id} className="p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2"><StatusBadge status={a.status} /><span className="text-xs text-muted">{label(a.action_type.split(".")[1] ?? a.action_type)}</span></div>
                    <p className="mt-1.5 text-sm font-medium">{a.summary}</p>
                    <p className="mt-1 text-xs text-muted">Requested by {a.requested_by_name ?? "—"} via {a.requested_via === "ai" ? "AI Command Center" : "UI"} · {dateTime(a.created_at)}
                      {a.status === "pending" && <> · expires {dateTime(a.expires_at)}</>}
                      {a.decided_at && <> · decided by {a.decided_by_name} {dateTime(a.decided_at)}</>}</p>
                    {a.result?.link && <Link to={a.result.link} className="mt-1 inline-block text-xs text-accent hover:underline">Open {a.result.number}</Link>}
                    {a.error && <p className="mt-1 text-xs text-bad">{a.error}</p>}
                    {a.conversation_id && <Link to={`/ai/${a.conversation_id}`} className="ml-3 mt-1 inline-block text-xs text-muted hover:text-fg">View conversation</Link>}
                  </div>
                  {a.can_decide && (
                    <div className="flex gap-2">
                      <Button variant="primary" size="sm" loading={decide.isPending} onClick={() => decide.mutate({ a, kind: "approve" })}>Approve & execute</Button>
                      <Button size="sm" onClick={() => setRejecting(a)}>Reject</Button>
                    </div>
                  )}
                </div>
                {a.preview?.rows && a.preview.rows.length > 0 && (
                  <div className="mt-3 overflow-x-auto rounded-lg border border-line">
                    <table className="w-full text-xs">
                      <thead className="bg-surface-2"><tr>{Object.keys(a.preview.rows[0]!).map((k) => <th key={k} className="px-3 py-1.5 text-left font-medium uppercase tracking-wider text-muted">{label(k)}</th>)}</tr></thead>
                      <tbody>{a.preview.rows.map((r, i) => <tr key={i} className="border-t border-line">{Object.values(r).map((v, j) => <td key={j} className="px-3 py-1.5">{String(v ?? "—")}</td>)}</tr>)}</tbody>
                    </table>
                  </div>
                )}
              </Card>
            ))}
          </div>
        )}
      <ConfirmDialog open={!!rejecting} onOpenChange={(o) => !o && setRejecting(null)} title="Reject proposed action" requireReason reasonLabel="Note" tone="danger" confirmLabel="Reject"
        loading={decide.isPending} onConfirm={(note) => rejecting && decide.mutate({ a: rejecting, kind: "reject", note })} />
    </div>
  );
}
