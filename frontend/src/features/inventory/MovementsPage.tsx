import { useQuery } from "@tanstack/react-query";
import { ArrowDownToLine, ArrowUpFromLine, Download, FlagTriangleRight, PackageOpen } from "lucide-react";
import { useEffect, useState } from "react";
import { LineItems, linesValid, newLine, type LineBase } from "@/components/LineItems";
import { LocationSelect, ProductPicker, WarehouseSelect } from "@/components/pickers";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/overlay";
import { PageHeader, SearchInput, StatusBadge } from "@/components/ui/page";
import { Button, buttonVariants, Card, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { Can } from "@/hooks/auth";
import { useDebounced } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { dateTime, label, money, qty } from "@/lib/format";
import { cn, newIdempotencyKey } from "@/lib/utils";
import type { Movement, Page, Product, Transaction } from "@/types/api";

const TYPES = ["opening", "receipt", "stock_in", "issue", "transfer_out", "transfer_in_transit", "transfer_receive", "adjustment", "reconciliation", "status_change"];

type Mode = "stock_in" | "issue" | "opening";
interface PostLine extends LineBase { location: string; unitCost: string; batch: string; expiry: string; serials: string; status: string }
const makeLine = () => newLine<PostLine>({ location: "", unitCost: "", batch: "", expiry: "", serials: "", status: "unrestricted" });
const serialList = (s: string) => s.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);

function PostStockDialog({ mode, onClose }: { mode: Mode; onClose: () => void }) {
  const [warehouse, setWarehouse] = useState("");
  const [reason, setReason] = useState(mode === "opening" ? "Opening balance" : "");
  const [reference, setReference] = useState("");
  const [lines, setLines] = useState<PostLine[]>([makeLine()]);
  const [idemKey] = useState(newIdempotencyKey); // stable across retries of this submission
  const titles: Record<Mode, string> = { stock_in: "Stock-in (non-PO receipt)", issue: "Issue stock", opening: "Post opening balances" };
  const post = useAction(async () => {
    const path = mode === "issue" ? "/inventory/issues" : mode === "opening" ? "/inventory/opening-balances" : "/inventory/receipts";
    const body = mode === "issue" ? {
      reason, reference_number: reference || null,
      lines: lines.map((l) => ({ product_id: l.product!.id, quantity: l.quantity, warehouse_id: l.location ? null : warehouse, location_id: l.location || null,
        serial_numbers: l.product!.track_serials ? serialList(l.serials) : null })),
    } : {
      reason, ...(mode === "stock_in" ? { reference_number: reference || null } : {}),
      lines: lines.map((l) => ({
        product_id: l.product!.id, quantity: l.quantity, location_id: l.location, unit_cost: l.unitCost || null,
        ...(mode === "stock_in" ? { stock_status: l.status } : {}),
        batch: l.product!.track_batches ? { batch_number: l.batch, expiry_date: l.expiry || null } : null,
        serial_numbers: l.product!.track_serials ? serialList(l.serials) : null,
      })),
    };
    return api.post<Transaction>(path, body, idemKey);
  }, { success: (t) => `Posted ${t.transaction_number}`, invalidate: [["movements"], ["balances"], ["products"], ["overview"], ["product"]], onSuccess: onClose });

  const needsLocation = mode !== "issue";
  const valid = linesValid(lines) && !!warehouse && reason.trim().length >= 3
    && (!needsLocation || lines.every((l) => l.location))
    && (mode !== "opening" || lines.every((l) => l.unitCost !== ""))
    && lines.every((l) => !l.product?.track_batches || mode === "issue" || l.batch)
    && lines.every((l) => !l.product?.track_serials || serialList(l.serials).length === Number(l.quantity));

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="lg" title={titles[mode]}
      description={mode === "issue" ? "Leave the location empty to auto-allocate (earliest expiry first, then default location)." : "Unit cost feeds the weighted-average valuation."}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!valid} loading={post.isPending} onClick={() => post.mutate(undefined)}>Post to ledger</Button></>}>
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Warehouse" htmlFor="pw" required><WarehouseSelect id="pw" value={warehouse} onChange={(v) => { setWarehouse(v); setLines((ls) => ls.map((l) => ({ ...l, location: "" }))); }} /></Field>
          {mode !== "opening" && <Field label="Reference" htmlFor="pref" hint={mode === "issue" ? "Work order / department reference" : "Document reference"}><Input id="pref" value={reference} onChange={(e) => setReference(e.target.value)} /></Field>}
          <Field label="Reason" htmlFor="preason" required className="sm:col-span-2"><Textarea id="preason" value={reason} onChange={(e) => setReason(e.target.value)} className="min-h-[52px]" /></Field>
        </div>
        <LineItems lines={lines} onChange={setLines} makeLine={makeLine} extra={(l, update) => (
          <div className="grid gap-2 sm:grid-cols-4">
            <Field label={needsLocation ? "Location *" : "Location (optional)"} htmlFor={`loc-${l.key}`}>
              <LocationSelect id={`loc-${l.key}`} warehouseId={warehouse} value={l.location} onChange={(v) => update({ location: v })} placeholder={needsLocation ? "Select location" : "Auto-allocate"} />
            </Field>
            {mode !== "issue" && <Field label={mode === "opening" ? "Unit cost *" : "Unit cost"} htmlFor={`uc-${l.key}`} hint={mode === "stock_in" ? "Blank = current average" : undefined}>
              <Input id={`uc-${l.key}`} inputMode="decimal" value={l.unitCost} onChange={(e) => update({ unitCost: e.target.value })} className="num" />
            </Field>}
            {mode === "stock_in" && <Field label="Stock status" htmlFor={`st-${l.key}`}>
              <Select id={`st-${l.key}`} value={l.status} onChange={(e) => update({ status: e.target.value })}><option value="unrestricted">Unrestricted</option><option value="returned">Returned</option><option value="damaged">Damaged</option></Select>
            </Field>}
            {l.product?.track_batches && mode !== "issue" && <>
              <Field label="Batch *" htmlFor={`b-${l.key}`}><Input id={`b-${l.key}`} value={l.batch} onChange={(e) => update({ batch: e.target.value })} /></Field>
              {l.product.track_expiry && <Field label="Expiry" htmlFor={`e-${l.key}`}><Input id={`e-${l.key}`} type="date" value={l.expiry} onChange={(e) => update({ expiry: e.target.value })} /></Field>}
            </>}
            {l.product?.track_serials && <Field label={`Serial numbers (${serialList(l.serials).length}/${l.quantity || 0})`} htmlFor={`s-${l.key}`} className="sm:col-span-4">
              <Textarea id={`s-${l.key}`} value={l.serials} onChange={(e) => update({ serials: e.target.value })} placeholder="Comma or newline separated" className="min-h-[52px] font-mono text-xs" />
            </Field>}
          </div>
        )} />
      </div>
    </Dialog>
  );
}

function StatusChangeDialog({ onClose }: { onClose: () => void }) {
  const [warehouse, setWarehouse] = useState("");
  const [location, setLocation] = useState("");
  const [product, setProduct] = useState<Product | null>(null);
  const [quantity, setQuantity] = useState("");
  const [from, setFrom] = useState("unrestricted");
  const [to, setTo] = useState("damaged");
  const [reason, setReason] = useState("");
  const [idemKey] = useState(newIdempotencyKey);
  const post = useAction(() => api.post<Transaction>("/inventory/status-changes", { product_id: product!.id, location_id: location, quantity, from_status: from, to_status: to, reason }, idemKey),
    { success: "Stock status updated", invalidate: [["movements"], ["balances"], ["products"], ["overview"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Change stock status" description="Move quantity between unrestricted, damaged and returned at one location. Valuation is unchanged."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={post.isPending} disabled={!product || !location || !(Number(quantity) > 0) || from === to || reason.length < 3} onClick={() => post.mutate(undefined)}>Post</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Warehouse" htmlFor="scw"><WarehouseSelect id="scw" value={warehouse} onChange={(v) => { setWarehouse(v); setLocation(""); }} /></Field>
        <Field label="Location" htmlFor="scl"><LocationSelect id="scl" warehouseId={warehouse} value={location} onChange={setLocation} placeholder="Select location" /></Field>
        <Field label="Product" htmlFor="scp" className="sm:col-span-2"><ProductPicker id="scp" value={product} onChange={setProduct} /></Field>
        <Field label="Quantity" htmlFor="scq"><Input id="scq" inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} /></Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label="From" htmlFor="scf"><Select id="scf" value={from} onChange={(e) => setFrom(e.target.value)}><option value="unrestricted">Unrestricted</option><option value="damaged">Damaged</option><option value="returned">Returned</option></Select></Field>
          <Field label="To" htmlFor="sct"><Select id="sct" value={to} onChange={(e) => setTo(e.target.value)}><option value="damaged">Damaged</option><option value="unrestricted">Unrestricted</option><option value="returned">Returned</option></Select></Field>
        </div>
        <Field label="Reason" htmlFor="scr" className="sm:col-span-2"><Textarea id="scr" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      </div>
    </Dialog>
  );
}

export function MovementsPage() {
  const [q, setQ] = useState("");
  const [wh, setWh] = useState("");
  const [type, setType] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [ref, setRef] = useState("");
  const [page, setPage] = useState(1);
  const [dialog, setDialog] = useState<Mode | "status" | null>(null);
  const dq = useDebounced(q);
  const dref = useDebounced(ref);
  useEffect(() => setPage(1), [dq, wh, type, from, to, dref]);
  const filters = { q: dq, warehouse_id: wh, movement_type: type, date_from: from, date_to: to, reference: dref };
  const list = useQuery({ queryKey: ["movements", filters, page], queryFn: () => api.get<Page<Movement>>("/inventory/movements", { ...filters, page, page_size: 50 }), placeholderData: (p) => p });
  return (
    <div>
      <PageHeader title="Stock movements" description="The permanent, append-only ledger of every inventory change."
        actions={<>
          <a className={buttonVariants()} href={api.downloadUrl("/reports/stock_movements", { format: "csv", warehouse_id: wh, date_from: from, date_to: to })}><Download /> CSV</a>
          <Can permission="inventory.adjust"><Button onClick={() => setDialog("status")}><FlagTriangleRight /> Status change</Button></Can>
          <Can permission="inventory.opening_balance"><Button onClick={() => setDialog("opening")}><PackageOpen /> Opening balance</Button></Can>
          <Can permission="inventory.receive"><Button onClick={() => setDialog("stock_in")}><ArrowDownToLine /> Stock-in</Button></Can>
          <Can permission="inventory.issue"><Button variant="primary" onClick={() => setDialog("issue")}><ArrowUpFromLine /> Issue stock</Button></Can>
        </>} />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <SearchInput value={q} onChange={setQ} placeholder="Product or SKU…" className="min-w-48 flex-1" />
          <SearchInput value={ref} onChange={setRef} placeholder="Reference / txn no." className="w-44" />
          <div className="w-52"><WarehouseSelect value={wh} onChange={setWh} allowAll /></div>
          <Select value={type} onChange={(e) => setType(e.target.value)} className="w-44" aria-label="Movement type">
            <option value="">All types</option>{TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}
          </Select>
          <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-40" aria-label="From date" />
          <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-40" aria-label="To date" />
        </div>
        <DataTable rows={list.data?.items} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(m) => m.id}
          page={page} pageSize={50} total={list.data?.total} onPage={setPage}
          columns={[
            { key: "date", header: "Date", cell: (m) => <div><div className="text-xs">{dateTime(m.created_at)}</div><div className="font-mono text-[11px] text-muted">{m.transaction_number}</div></div> },
            { key: "type", header: "Type", cell: (m) => <span className="text-xs">{label(m.movement_type)}</span> },
            { key: "product", header: "Product", cell: (m) => <div><div>{m.product_name}</div><div className="font-mono text-xs text-muted">{m.product_sku}{m.batch_number ? ` · ${m.batch_number}` : ""}</div></div> },
            { key: "loc", header: "Location", hideBelow: "md", cell: (m) => <div className="text-xs"><div>{m.warehouse_code}/{m.location_path}</div>{m.counterpart_location_path && <div className="text-muted">↔ {m.counterpart_location_path}</div>}</div> },
            { key: "status", header: "Status", hideBelow: "lg", cell: (m) => <StatusBadge status={m.stock_status} /> },
            { key: "qty", header: "Qty", align: "right", cell: (m) => <span className={cn("num", Number(m.quantity) < 0 ? "text-bad" : "text-ok")}>{Number(m.quantity) > 0 ? "+" : ""}{qty(m.quantity)} <span className="text-xs text-muted">{m.uom_code}</span></span> },
            { key: "value", header: "Value", align: "right", hideBelow: "lg", cell: (m) => <span className="num">{Number(m.value) === 0 ? "—" : money(m.value)}</span> },
            { key: "bal", header: "Balance", align: "right", cell: (m) => <span className="num">{qty(m.balance_after)}</span> },
            { key: "ref", header: "Reference / reason", hideBelow: "lg", cell: (m) => <div className="max-w-[220px] text-xs"><div>{m.reference_number ?? "—"}</div><div className="truncate text-muted" title={m.reason ?? ""}>{m.reason}</div></div> },
            { key: "by", header: "By", hideBelow: "lg", cell: (m) => <span className="text-xs">{m.performed_by_name}</span> },
          ]} />
      </Card>
      {dialog && dialog !== "status" && <PostStockDialog mode={dialog} onClose={() => setDialog(null)} />}
      {dialog === "status" && <StatusChangeDialog onClose={() => setDialog(null)} />}
    </div>
  );
}
