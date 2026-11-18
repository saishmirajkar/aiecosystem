import { ArrowDown, ArrowUp } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { EmptyState, ErrorState, Pagination } from "./page";
import { Skeleton } from "./primitives";

export interface Column<T> {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  align?: "left" | "right" | "center";
  sortKey?: string;
  className?: string;
  hideBelow?: "sm" | "md" | "lg";
}

const hide = { sm: "hidden sm:table-cell", md: "hidden md:table-cell", lg: "hidden lg:table-cell" };

export function DataTable<T>({ columns, rows, loading, error, onRetry, rowKey, onRowClick, empty, sort, onSort, page, pageSize, total, onPage, dense }: {
  columns: Column<T>[]; rows: T[] | undefined; loading?: boolean; error?: unknown; onRetry?: () => void; rowKey: (row: T) => string;
  onRowClick?: (row: T) => void; empty?: ReactNode; sort?: string; onSort?: (sort: string) => void; page?: number; pageSize?: number;
  total?: number; onPage?: (p: number) => void; dense?: boolean;
}) {
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  const cellPad = dense ? "px-3 py-2" : "px-4 py-2.5";
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line bg-surface-2/60">
              {columns.map((c) => {
                const active = sort?.replace("-", "") === c.sortKey;
                const desc = sort?.startsWith("-");
                return (
                  <th key={c.key} scope="col"
                    className={cn("whitespace-nowrap text-[11px] font-medium uppercase tracking-wider text-muted", cellPad,
                      c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "text-left", c.hideBelow && hide[c.hideBelow], c.className)}
                    aria-sort={active ? (desc ? "descending" : "ascending") : undefined}>
                    {c.sortKey && onSort ? (
                      <button type="button" className="inline-flex items-center gap-1 uppercase hover:text-fg"
                        onClick={() => onSort(active && !desc ? `-${c.sortKey}` : c.sortKey!)}>
                        {c.header}
                        {active && (desc ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
                      </button>
                    ) : c.header}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {loading && !rows?.length
              ? Array.from({ length: 6 }).map((_, i) => (
                <tr key={i} className="border-b border-line last:border-0">
                  {columns.map((c) => <td key={c.key} className={cn(cellPad, c.hideBelow && hide[c.hideBelow])}><Skeleton className="h-4 w-full max-w-[160px]" /></td>)}
                </tr>
              ))
              : rows?.map((row) => (
                <tr key={rowKey(row)} onClick={onRowClick ? () => onRowClick(row) : undefined}
                  onKeyDown={onRowClick ? (e) => { if (e.key === "Enter") onRowClick(row); } : undefined}
                  tabIndex={onRowClick ? 0 : undefined}
                  className={cn("border-b border-line last:border-0", onRowClick && "cursor-pointer hover:bg-surface-2/70 focus:bg-surface-2/70 focus:outline-none")}>
                  {columns.map((c) => (
                    <td key={c.key} className={cn(cellPad, "align-middle", c.align === "right" && "text-right", c.align === "center" && "text-center", c.hideBelow && hide[c.hideBelow], c.className)}>
                      {c.cell(row)}
                    </td>
                  ))}
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      {!loading && rows && rows.length === 0 && (empty ?? <EmptyState title="Nothing here yet" />)}
      {page !== undefined && pageSize !== undefined && total !== undefined && onPage && (
        <Pagination page={page} pageSize={pageSize} total={total} onPage={onPage} />
      )}
    </div>
  );
}
