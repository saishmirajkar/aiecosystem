import { useQuery } from "@tanstack/react-query";
import { MapPin, Pencil, Plus } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/overlay";
import { DetailGrid, EmptyState, ErrorState, PageHeader, StatusBadge } from "@/components/ui/page";
import { Badge, Button, Card, CardHeader, Checkbox, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Can, useAuth } from "@/hooks/auth";
import { useLocations, useUsers } from "@/hooks/data";
import { useAction } from "@/hooks/mutation";
import { api } from "@/lib/api";
import { label, money, qty } from "@/lib/format";
import type { Balance, Location, Page, Warehouse } from "@/types/api";

function WarehouseDialog({ warehouse, onClose }: { warehouse?: Warehouse; onClose: () => void }) {
  const { can } = useAuth();
  const users = useUsers(can("users.view"));
  const [f, setF] = useState({ code: warehouse?.code ?? "", name: warehouse?.name ?? "", address: warehouse?.address ?? "", city: warehouse?.city ?? "",
    manager_user_id: warehouse?.manager_user_id ?? "", is_active: warehouse?.is_active ?? true });
  const save = useAction(() => warehouse
    ? api.patch<Warehouse>(`/warehouses/${warehouse.id}`, { name: f.name, address: f.address || null, city: f.city || null, manager_user_id: f.manager_user_id || null, is_active: f.is_active })
    : api.post<Warehouse>("/warehouses", { code: f.code.toUpperCase(), name: f.name, address: f.address || null, city: f.city || null, manager_user_id: f.manager_user_id || null }),
  { success: warehouse ? "Warehouse updated" : "Warehouse created (MAIN and TRANSIT locations added)", invalidate: [["warehouses"], ["warehouse"]], onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={warehouse ? `Edit ${warehouse.code}` : "New warehouse"}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.code || !f.name} onClick={() => save.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Code" htmlFor="wc" required hint="Uppercase letters, digits, - or _"><Input id="wc" value={f.code} disabled={!!warehouse} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
        <Field label="Name" htmlFor="wn" required><Input id="wn" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="City" htmlFor="wcity"><Input id="wcity" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Field>
        <Field label="Manager" htmlFor="wm"><Select id="wm" value={f.manager_user_id} onChange={(e) => setF({ ...f, manager_user_id: e.target.value })}>
          <option value="">Unassigned</option>{users.data?.items.map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
        </Select></Field>
        <Field label="Address" htmlFor="wa" className="sm:col-span-2"><Textarea id="wa" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
        {warehouse && <Checkbox label="Active" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />}
      </div>
    </Dialog>
  );
}

export function WarehousesPage() {
  const navigate = useNavigate();
  const [dialog, setDialog] = useState(false);
  const [inactive, setInactive] = useState(false);
  const list = useQuery({ queryKey: ["warehouses", inactive], queryFn: () => api.get<Warehouse[]>("/warehouses", { include_inactive: inactive }) });
  return (
    <div>
      <PageHeader title="Warehouses" description="Stores, their storage locations and the stock they hold."
        actions={<><Checkbox label="Show inactive" checked={inactive} onChange={(e) => setInactive(e.target.checked)} /><Can permission="warehouses.manage"><Button variant="primary" onClick={() => setDialog(true)}><Plus /> New warehouse</Button></Can></>} />
      <Card>
        <DataTable rows={list.data} loading={list.isLoading} error={list.error} onRetry={list.refetch} rowKey={(w) => w.id} onRowClick={(w) => navigate(`/inventory/warehouses/${w.id}`)}
          empty={<EmptyState title="No warehouses yet" description="Create your first warehouse to start receiving stock." />}
          columns={[
            { key: "code", header: "Code", cell: (w) => <span className="font-mono text-xs">{w.code}</span> },
            { key: "name", header: "Name", cell: (w) => <div><div className="font-medium">{w.name}</div><div className="text-xs text-muted">{w.city ?? ""}</div></div> },
            { key: "mgr", header: "Manager", hideBelow: "md", cell: (w) => w.manager_name ?? "—" },
            { key: "loc", header: "Locations", align: "right", hideBelow: "md", cell: (w) => <span className="num">{w.location_count}</span> },
            { key: "sku", header: "SKUs held", align: "right", cell: (w) => <span className="num">{w.sku_count}</span> },
            { key: "val", header: "Stock value", align: "right", cell: (w) => <span className="num">{money(w.stock_value)}</span> },
            { key: "st", header: "Status", cell: (w) => <StatusBadge status={w.is_active ? "active" : "cancelled"} /> },
          ]} />
      </Card>
      {dialog && <WarehouseDialog onClose={() => setDialog(false)} />}
    </div>
  );
}

function LocationDialog({ warehouseId, locations, onClose }: { warehouseId: string; locations: Location[]; onClose: () => void }) {
  const [f, setF] = useState({ code: "", name: "", parent_id: "", location_type: "bin" });
  const save = useAction(() => api.post<Location>(`/warehouses/${warehouseId}/locations`, { ...f, name: f.name || null, parent_id: f.parent_id || null }),
    { success: (l) => `Location ${l.path} created`, invalidate: [["locations", warehouseId], ["warehouses"]], onSuccess: onClose });
  const parents = locations.filter((l) => l.location_type !== "transit");
  const parentPath = parents.find((p) => p.id === f.parent_id)?.path;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="New storage location" description="Build hierarchies such as Section → Rack → Bin."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.code} onClick={() => save.mutate(undefined)}>Create</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Parent" htmlFor="lp"><Select id="lp" value={f.parent_id} onChange={(e) => setF({ ...f, parent_id: e.target.value })}>
          <option value="">(Top level)</option>{parents.map((p) => <option key={p.id} value={p.id}>{p.path}</option>)}
        </Select></Field>
        <Field label="Type" htmlFor="lt"><Select id="lt" value={f.location_type} onChange={(e) => setF({ ...f, location_type: e.target.value })}>
          {["zone", "section", "rack", "shelf", "bin", "storage", "receiving"].map((t) => <option key={t} value={t}>{label(t)}</option>)}
        </Select></Field>
        <Field label="Code" htmlFor="lc" required hint={f.code ? `Path: ${parentPath ? `${parentPath}/` : ""}${f.code.toUpperCase()}` : undefined}><Input id="lc" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
        <Field label="Name" htmlFor="ln"><Input id="ln" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      </div>
    </Dialog>
  );
}

export function WarehouseDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { can } = useAuth();
  const [edit, setEdit] = useState(false);
  const [newLoc, setNewLoc] = useState(false);
  const wh = useQuery({ queryKey: ["warehouse", id], queryFn: () => api.get<Warehouse>(`/warehouses/${id}`) });
  const locs = useLocations(id);
  const stock = useQuery({ queryKey: ["balances", { wh: id }], queryFn: () => api.get<Page<Balance>>("/inventory/balances", { warehouse_id: id, page_size: 200 }) });
  const toggle = useAction(({ loc, patch }: { loc: Location; patch: Record<string, boolean> }) => api.patch(`/locations/${loc.id}`, patch),
    { success: "Location updated", invalidate: [["locations", id]] });
  if (wh.error) return <ErrorState error={wh.error} onRetry={wh.refetch} />;
  if (!wh.data) return <Skeleton className="h-64" />;
  const w = wh.data;
  const depth = (p: string) => p.split("/").length - 1;
  return (
    <div className="space-y-4">
      <PageHeader breadcrumb={[{ label: "Warehouses", to: "/inventory/warehouses" }]} title={<span className="flex items-center gap-3">{w.name} <StatusBadge status={w.is_active ? "active" : "cancelled"} /></span>}
        description={<span className="font-mono">{w.code}</span>} actions={<Can permission="warehouses.manage"><Button onClick={() => setEdit(true)}><Pencil /> Edit</Button></Can>} />
      <Card><div className="p-5"><DetailGrid items={[
        { label: "Manager", value: w.manager_name }, { label: "City", value: w.city }, { label: "Address", value: w.address },
        { label: "Stock value", value: money(w.stock_value) }, { label: "SKUs held", value: w.sku_count }, { label: "Locations", value: w.location_count },
      ]} /></div></Card>
      <div className="grid gap-4 xl:grid-cols-[380px_1fr]">
        <Card className="h-fit">
          <CardHeader title="Storage locations" actions={<Can permission="warehouses.manage"><Button size="sm" onClick={() => setNewLoc(true)}><Plus /> Add</Button></Can>} />
          {locs.isLoading ? <Skeleton className="m-4 h-32" /> : (
            <ul className="divide-y divide-line">
              {locs.data?.map((l) => (
                <li key={l.id} className="flex items-center justify-between gap-2 px-4 py-2 text-sm" style={{ paddingLeft: 16 + depth(l.path) * 18 }}>
                  <span className="flex min-w-0 items-center gap-2"><MapPin className="size-3.5 shrink-0 text-muted" /><span className="truncate font-mono text-xs">{l.code}</span>
                    <span className="text-xs text-muted">{label(l.location_type)}</span>
                    {l.is_default && <Badge tone="accent">Default</Badge>}{l.is_system && <Badge>System</Badge>}</span>
                  {can("warehouses.manage") && !l.is_system && !l.is_default && (
                    <Button size="sm" variant="ghost" onClick={() => toggle.mutate({ loc: l, patch: { is_default: true } })}>Set default</Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card>
          <CardHeader title="Stock in this warehouse" />
          <DataTable dense rows={stock.data?.items} loading={stock.isLoading} rowKey={(b) => b.id} onRowClick={(b) => navigate(`/inventory/products/${b.product_id}`)}
            empty={<EmptyState title="No stock held here" />}
            columns={[
              { key: "p", header: "Product", cell: (b) => <div><div>{b.product_name}</div><div className="font-mono text-xs text-muted">{b.product_sku}</div></div> },
              { key: "l", header: "Location", cell: (b) => <span className="font-mono text-xs">{b.location_path}</span> },
              { key: "s", header: "Status", cell: (b) => <StatusBadge status={b.stock_status} /> },
              { key: "q", header: "On hand", align: "right", cell: (b) => <span className="num">{qty(b.qty_on_hand)} <span className="text-xs text-muted">{b.uom_code}</span></span> },
              { key: "a", header: "Available", align: "right", cell: (b) => <span className="num">{qty(b.qty_available)}</span> },
              { key: "v", header: "Value", align: "right", hideBelow: "md", cell: (b) => <span className="num">{money(b.value)}</span> },
            ]} />
        </Card>
      </div>
      {edit && <WarehouseDialog warehouse={w} onClose={() => setEdit(false)} />}
      {newLoc && <LocationDialog warehouseId={w.id} locations={locs.data ?? []} onClose={() => setNewLoc(false)} />}
    </div>
  );
}
