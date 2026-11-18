import { Plus, Trash2 } from "lucide-react";
import type { ReactNode } from "react";
import type { Product } from "@/types/api";
import { ProductPicker } from "./pickers";
import { Button, Input, Label } from "./ui/primitives";

export interface LineBase { key: string; product: Product | null; quantity: string }

export function newLine<T extends LineBase>(extra?: Omit<T, keyof LineBase>): T {
  return { key: Math.random().toString(36).slice(2), product: null, quantity: "", ...(extra ?? {}) } as T;
}

/** Editable list of product lines. `extra` renders additional per-line fields. */
export function LineItems<T extends LineBase>({ lines, onChange, extra, quantityLabel = "Quantity", minLines = 1, makeLine }: {
  lines: T[]; onChange: (lines: T[]) => void; extra?: (line: T, update: (patch: Partial<T>) => void) => ReactNode;
  quantityLabel?: string; minLines?: number; makeLine: () => T;
}) {
  const update = (key: string, patch: Partial<T>) => onChange(lines.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  return (
    <div className="space-y-2">
      {lines.map((l, i) => (
        <div key={l.key} className="rounded-lg border border-line bg-surface-2/40 p-3">
          <div className="grid gap-2 sm:grid-cols-[1fr_140px_auto] sm:items-end">
            <div className="space-y-1.5">
              <Label htmlFor={`p-${l.key}`}>Product {i + 1}</Label>
              <ProductPicker id={`p-${l.key}`} value={l.product} onChange={(p) => update(l.key, { product: p } as Partial<T>)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`q-${l.key}`}>{quantityLabel}{l.product && <span className="text-muted"> ({l.product.uom_code})</span>}</Label>
              <Input id={`q-${l.key}`} inputMode="decimal" value={l.quantity} onChange={(e) => update(l.key, { quantity: e.target.value } as Partial<T>)} className="num" />
            </div>
            <Button variant="ghost" size="icon" disabled={lines.length <= minLines} onClick={() => onChange(lines.filter((x) => x.key !== l.key))} aria-label={`Remove line ${i + 1}`}><Trash2 /></Button>
          </div>
          {extra && <div className="mt-2">{extra(l, (patch) => update(l.key, patch))}</div>}
        </div>
      ))}
      <Button size="sm" variant="ghost" onClick={() => onChange([...lines, makeLine()])}><Plus /> Add line</Button>
    </div>
  );
}

export function linesValid(lines: LineBase[]): boolean {
  return lines.length > 0 && lines.every((l) => l.product && Number(l.quantity) > 0);
}
