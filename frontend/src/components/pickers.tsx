import { Check, ChevronsUpDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useDebounced, useLocations, useProductSearch, useWarehouses } from "@/hooks/data";
import { qty } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Product } from "@/types/api";
import { Select, Spinner } from "./ui/primitives";

/** Searchable product combobox (keyboard accessible). */
export function ProductPicker({ value, onChange, placeholder = "Search product by name or SKU…", id, invalid }: {
  value: Product | null; onChange: (p: Product | null) => void; placeholder?: string; id?: string; invalid?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const debounced = useDebounced(q, 250);
  const { data, isFetching } = useProductSearch(debounced);
  const ref = useRef<HTMLDivElement>(null);
  const items = data?.items ?? [];

  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  useEffect(() => setActive(0), [debounced]);

  const pick = (p: Product) => { onChange(p); setOpen(false); setQ(""); };
  return (
    <div ref={ref} className="relative">
      <div className={cn("flex h-9 items-center rounded-lg border border-line bg-surface px-3 text-sm hover:border-line-strong focus-within:border-accent", invalid && "border-bad")}>
        <input id={id} role="combobox" aria-expanded={open} aria-autocomplete="list" className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted"
          value={open ? q : value ? `${value.sku} — ${value.name}` : ""} placeholder={placeholder}
          onFocus={() => setOpen(true)} onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            else if (e.key === "Enter" && open && items[active]) { e.preventDefault(); pick(items[active]!); }
            else if (e.key === "Escape") setOpen(false);
          }} />
        {isFetching ? <Spinner /> : <ChevronsUpDown className="size-4 text-muted" />}
      </div>
      {open && (
        <ul role="listbox" className="absolute z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-line bg-surface p-1 shadow-xl">
          {items.length === 0 && <li className="px-2.5 py-2 text-sm text-muted">{isFetching ? "Searching…" : "No products found"}</li>}
          {items.map((p, i) => (
            <li key={p.id} role="option" aria-selected={value?.id === p.id}
              className={cn("flex cursor-pointer items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-sm", i === active && "bg-surface-2")}
              onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); pick(p); }}>
              <span className="min-w-0"><span className="font-mono text-xs text-muted">{p.sku}</span> <span className="truncate">{p.name}</span></span>
              <span className="flex shrink-0 items-center gap-2 text-xs text-muted">
                <span className="num">{qty(p.stock?.available)} {p.uom_code}</span>
                {value?.id === p.id && <Check className="size-3.5 text-accent" />}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function WarehouseSelect({ value, onChange, id, placeholder = "Select warehouse", exclude, allowAll }: {
  value: string; onChange: (v: string) => void; id?: string; placeholder?: string; exclude?: string; allowAll?: boolean;
}) {
  const { data } = useWarehouses();
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{allowAll ? "All warehouses" : placeholder}</option>
      {data?.filter((w) => w.id !== exclude).map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}
    </Select>
  );
}

export function LocationSelect({ warehouseId, value, onChange, id, includeTransit = false, placeholder = "Default location" }: {
  warehouseId: string; value: string; onChange: (v: string) => void; id?: string; includeTransit?: boolean; placeholder?: string;
}) {
  const { data } = useLocations(warehouseId || undefined);
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} disabled={!warehouseId}>
      <option value="">{placeholder}</option>
      {data?.filter((l) => includeTransit || l.location_type !== "transit").map((l) => (
        <option key={l.id} value={l.id}>{l.path}{l.is_default ? " (default)" : ""}</option>
      ))}
    </Select>
  );
}
