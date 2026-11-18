import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeftRight, Bell, Boxes, Building2, ChartNoAxesCombined, ClipboardCheck, ClipboardList, FileBarChart, Gauge, KeyRound,
  LayoutGrid, LogOut, Menu, Moon, PackageCheck, PackageSearch, Ruler, ScrollText, Settings2, ShieldCheck, Sparkles, Sun,
  TriangleAlert, Truck, UserRound, Users, Warehouse, Waypoints, X, Scale, ShoppingCart, Network,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Badge, Button } from "@/components/ui/primitives";
import { DropdownContent, DropdownItem, DropdownLabel, DropdownMenu, DropdownSeparator, DropdownTrigger } from "@/components/ui/overlay";
import { ChatPanel } from "@/features/ai-command-center/ChatPanel";
import { useAuth } from "@/hooks/auth";
import { useTheme } from "@/hooks/theme";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Notification, Page } from "@/types/api";

interface NavItem { to: string; label: string; icon: ReactNode; perm?: string }
interface NavGroup { label?: string; items: NavItem[] }

const NAV: NavGroup[] = [
  { items: [
    { to: "/", label: "Overview", icon: <Gauge /> },
    { to: "/ai", label: "AI Command Center", icon: <Sparkles />, perm: "ai.access" },
  ] },
  { label: "Inventory", items: [
    { to: "/inventory/products", label: "Products", icon: <PackageSearch />, perm: "inventory.view" },
    { to: "/inventory/stock", label: "Stock Overview", icon: <Boxes />, perm: "inventory.view" },
    { to: "/inventory/warehouses", label: "Warehouses", icon: <Warehouse />, perm: "inventory.view" },
    { to: "/inventory/movements", label: "Stock Movements", icon: <Waypoints />, perm: "inventory.view" },
    { to: "/inventory/transfers", label: "Stock Transfers", icon: <ArrowLeftRight />, perm: "inventory.view" },
    { to: "/inventory/adjustments", label: "Adjustments & Counts", icon: <Scale />, perm: "inventory.view" },
    { to: "/inventory/receipts", label: "Goods Receipts", icon: <PackageCheck />, perm: "purchasing.view" },
    { to: "/inventory/alerts", label: "Inventory Alerts", icon: <TriangleAlert />, perm: "inventory.view" },
    { to: "/inventory/reports", label: "Reports", icon: <FileBarChart />, perm: "reports.view" },
  ] },
  { label: "Purchasing", items: [
    { to: "/purchasing/suppliers", label: "Suppliers", icon: <Truck />, perm: "purchasing.view" },
    { to: "/purchasing/requisitions", label: "Purchase Requisitions", icon: <ClipboardList />, perm: "purchasing.view" },
    { to: "/purchasing/orders", label: "Purchase Orders", icon: <ShoppingCart />, perm: "purchasing.view" },
    { to: "/approvals", label: "Approvals", icon: <ClipboardCheck />, perm: "approvals.view" },
  ] },
  { label: "ERP Settings", items: [
    { to: "/settings/company", label: "Company", icon: <Building2 />, perm: "company.view" },
    { to: "/settings/users", label: "Users", icon: <Users />, perm: "users.view" },
    { to: "/settings/roles", label: "Roles & Permissions", icon: <ShieldCheck />, perm: "roles.view" },
    { to: "/settings/departments", label: "Departments", icon: <Network />, perm: "departments.view" },
    { to: "/settings/units", label: "Units of Measurement", icon: <Ruler />, perm: "inventory.view" },
  ] },
  { label: "System", items: [
    { to: "/settings/system", label: "System Settings", icon: <Settings2 />, perm: "settings.view" },
    { to: "/settings/audit", label: "Audit Log", icon: <ScrollText />, perm: "audit.view" },
  ] },
];

function Brand({ name }: { name?: string }) {
  return (
    <Link to="/" className="flex items-center gap-2.5 px-2">
      <svg viewBox="0 0 32 32" className="size-7 shrink-0" aria-hidden>
        <rect width="32" height="32" rx="7" fill="currentColor" className="text-fg" />
        <path d="M8 22 16 9l8 13" fill="none" stroke="var(--accent)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M11.5 22h9" stroke="var(--bg)" strokeWidth="2.6" strokeLinecap="round" />
      </svg>
      <div className="min-w-0 leading-tight">
        <div className="text-[13px] font-semibold tracking-[0.14em]">ABOVE</div>
        <div className="truncate text-[11px] text-muted">{name ?? "Industrial AI"}</div>
      </div>
    </Link>
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { can, user } = useAuth();
  return (
    <div className="flex h-full flex-col gap-4 px-3 py-4">
      <Brand name={user?.company?.name} />
      <nav className="flex-1 space-y-5 overflow-y-auto pb-4" aria-label="Main navigation">
        {NAV.map((g, gi) => {
          const items = g.items.filter((i) => !i.perm || can(i.perm));
          if (!items.length) return null;
          return (
            <div key={gi}>
              {g.label && <div className="mb-1.5 px-2.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-muted">{g.label}</div>}
              <ul className="space-y-0.5">
                {items.map((i) => (
                  <li key={i.to}>
                    <NavLink to={i.to} end={i.to === "/"} onClick={onNavigate}
                      className={({ isActive }) => cn("flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13.5px] text-fg-2 transition-colors hover:bg-surface-2 hover:text-fg [&_svg]:size-4 [&_svg]:text-muted",
                        isActive && "bg-surface-2 font-medium text-fg [&_svg]:text-accent")}>
                      {i.icon}{i.label}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </nav>
      <div className="rounded-lg border border-line bg-surface-2/60 px-3 py-2.5 text-[11px] leading-relaxed text-muted">
        <span className="font-medium text-fg-2">Your entire business. Within reach.</span><br />Inventory connected · more departments coming
      </div>
    </div>
  );
}

function Notifications() {
  const { data, refetch } = useQuery({ queryKey: ["notifications"], queryFn: () => api.get<Page<Notification>>("/notifications", { page_size: 8 }), refetchInterval: 120_000 });
  const unread = data?.items.filter((n) => !n.read_at).length ?? 0;
  return (
    <DropdownMenu>
      <DropdownTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`} className="relative">
          <Bell />
          {unread > 0 && <span className="absolute right-1.5 top-1.5 size-2 rounded-full bg-accent" />}
        </Button>
      </DropdownTrigger>
      <DropdownContent className="w-80">
        <div className="flex items-center justify-between px-2.5 py-1.5">
          <span className="text-xs font-medium text-muted">Notifications</span>
          {unread > 0 && <button className="text-xs text-accent hover:underline" onClick={async () => { await api.post("/notifications/read-all"); refetch(); }}>Mark all read</button>}
        </div>
        <DropdownSeparator />
        {!data?.items.length ? <p className="px-2.5 py-6 text-center text-sm text-muted">You’re all caught up.</p> :
          data.items.map((n) => (
            <DropdownItem key={n.id} className="flex-col items-start gap-0.5" onSelect={async () => { if (!n.read_at) { await api.post(`/notifications/${n.id}/read`); refetch(); } }}>
              <span className={cn("text-[13px]", !n.read_at && "font-medium text-fg")}>{n.title}</span>
              {n.body && <span className="line-clamp-2 text-xs text-muted">{n.body}</span>}
              <span className="text-[11px] text-muted">{relative(n.created_at)}</span>
            </DropdownItem>
          ))}
      </DropdownContent>
    </DropdownMenu>
  );
}

export function AppShell({ demo }: { demo: boolean }) {
  const { user, logout, can } = useAuth();
  const [theme, toggleTheme] = useTheme();
  const [mobileNav, setMobileNav] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const onCommandCenter = location.pathname.startsWith("/ai");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k" && can("ai.access")) { e.preventDefault(); setAiOpen((o) => !o); }
      if (e.key === "Escape") setAiOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [can]);

  return (
    <div className="flex h-full">
      <aside className="hidden w-[248px] shrink-0 border-r border-line bg-surface lg:block"><Sidebar /></aside>
      {mobileNav && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMobileNav(false)} />
          <aside className="absolute inset-y-0 left-0 w-[264px] border-r border-line bg-surface">
            <Button variant="ghost" size="icon" className="absolute right-2 top-3" onClick={() => setMobileNav(false)} aria-label="Close navigation"><X /></Button>
            <Sidebar onNavigate={() => setMobileNav(false)} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b border-line bg-bg/85 px-3 backdrop-blur sm:px-5">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileNav(true)} aria-label="Open navigation"><Menu /></Button>
          {demo && <Badge tone="accent" className="hidden sm:inline-flex">Demo data</Badge>}
          <div className="flex-1" />
          {can("ai.access") && !onCommandCenter && (
            <Button variant="secondary" size="sm" onClick={() => setAiOpen(true)} className="gap-2">
              <Sparkles className="text-accent" /> <span className="hidden sm:inline">Ask Above</span>
              <span className="hidden text-[10px] text-muted md:inline">⌘K</span>
            </Button>
          )}
          <Notifications />
          <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
            {theme === "dark" ? <Sun /> : <Moon />}
          </Button>
          <DropdownMenu>
            <DropdownTrigger asChild>
              <button className="flex items-center gap-2 rounded-lg px-1.5 py-1 hover:bg-surface-2" aria-label="Account menu">
                <span className="grid size-7 place-items-center rounded-full bg-fg text-[11px] font-semibold text-bg">
                  {user?.full_name.split(" ").map((p) => p[0]).slice(0, 2).join("")}
                </span>
                <span className="hidden text-left leading-tight md:block">
                  <span className="block text-[13px] font-medium">{user?.full_name}</span>
                  <span className="block text-[11px] text-muted">{user?.roles[0]?.name}</span>
                </span>
              </button>
            </DropdownTrigger>
            <DropdownContent>
              <DropdownLabel>{user?.email}</DropdownLabel>
              <DropdownSeparator />
              <DropdownItem onSelect={() => navigate("/profile")}><UserRound /> Profile & notifications</DropdownItem>
              <DropdownItem onSelect={() => navigate("/profile#password")}><KeyRound /> Change password</DropdownItem>
              <DropdownSeparator />
              <DropdownItem onSelect={() => logout()}><LogOut /> Sign out</DropdownItem>
            </DropdownContent>
          </DropdownMenu>
        </header>
        <main className={cn("min-h-0 flex-1 overflow-y-auto", !onCommandCenter && "px-4 py-6 sm:px-6 lg:px-8")}>
          <div className={cn(!onCommandCenter && "mx-auto max-w-[1400px]")}><Outlet /></div>
        </main>
      </div>
      {aiOpen && !onCommandCenter && (
        <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label="AI Command Center">
          <div className="absolute inset-0 bg-black/30" onClick={() => setAiOpen(false)} />
          <div className="absolute inset-y-0 right-0 flex w-full max-w-[560px] flex-col border-l border-line bg-bg shadow-2xl">
            <div className="flex h-14 items-center justify-between border-b border-line px-4">
              <div className="flex items-center gap-2 text-sm font-semibold"><ChartNoAxesCombined className="size-4 text-accent" /> AI Command Center</div>
              <div className="flex items-center gap-1">
                <Button variant="ghost" size="sm" onClick={() => { setAiOpen(false); navigate("/ai"); }}><LayoutGrid /> Full view</Button>
                <Button variant="ghost" size="icon" onClick={() => setAiOpen(false)} aria-label="Close assistant"><X /></Button>
              </div>
            </div>
            <ChatPanel compact />
          </div>
        </div>
      )}
    </div>
  );
}
