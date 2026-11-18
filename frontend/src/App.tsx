import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Spinner } from "@/components/ui/primitives";
import { EmptyState } from "@/components/ui/page";
import { CommandCenterPage } from "@/features/ai-command-center/CommandCenterPage";
import { LoginPage, ResetPasswordPage, SetupPage } from "@/features/auth/AuthPages";
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { AdjustmentDetailPage, AdjustmentsPage } from "@/features/inventory/AdjustmentsPage";
import { AlertsPage } from "@/features/inventory/AlertsPage";
import { MovementsPage } from "@/features/inventory/MovementsPage";
import { ReportsPage } from "@/features/inventory/ReportsPage";
import { StockOverviewPage } from "@/features/inventory/StockOverviewPage";
import { TransferDetailPage, TransfersPage } from "@/features/inventory/TransfersPage";
import { ChallanPrintPage, ChallansPage, SitesPage, StructuresPage, VendorWorkloadPage } from "@/features/production/AdminPages";
import { ProductionOrderPage, ProductionOrdersPage } from "@/features/production/OrdersPages";
import { ProductDetailPage, ProductsPage } from "@/features/products/ProductsPage";
import { ApprovalsPage } from "@/features/purchasing/ApprovalsPage";
import { GoodsReceiptDetailPage, GoodsReceiptsPage } from "@/features/purchasing/GoodsReceiptsPage";
import { PurchaseOrderDetailPage, PurchaseOrdersPage } from "@/features/purchasing/PurchaseOrdersPage";
import { RequisitionDetailPage, RequisitionsPage } from "@/features/purchasing/RequisitionsPage";
import { SuppliersPage } from "@/features/purchasing/SuppliersPage";
import { CustomersPage, DispatchNotesPage } from "@/features/sales/SalesPages";
import { AuditLogPage, CompanyPage, DepartmentsPage, ProfilePage, RolesPage, SystemSettingsPage, UnitsPage, UsersPage } from "@/features/settings/SettingsPages";
import { WarehouseDetailPage, WarehousesPage } from "@/features/warehouses/WarehousesPage";
import { useAuth } from "@/hooks/auth";
import { AppShell } from "@/layouts/AppShell";
import { api } from "@/lib/api";

function FullScreenSpinner() {
  return <div className="grid h-full place-items-center"><Spinner className="size-6" /></div>;
}

function Guard({ perm, children }: { perm?: string; children: ReactNode }) {
  const { can } = useAuth();
  if (perm && !can(perm)) return <EmptyState title="You don’t have access to this page" description={`Required permission: ${perm}. Contact your administrator.`} />;
  return <>{children}</>;
}

export function App() {
  const { user, loading } = useAuth();
  const location = useLocation();
  const setup = useQuery({ queryKey: ["setup-status"], queryFn: () => api.get<{ initialized: boolean; demo_data: boolean }>("/setup/status"), staleTime: 60_000 });

  if (loading || setup.isLoading) return <FullScreenSpinner />;
  if (setup.data && !setup.data.initialized) return <SetupPage onDone={() => setup.refetch()} />;
  if (!user) {
    return (
      <Routes>
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="*" element={location.pathname === "/login" ? <LoginPage /> : <Navigate to="/login" replace />} />
      </Routes>
    );
  }
  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route path="production/challans/:id/print" element={<Guard perm="production.view"><ChallanPrintPage /></Guard>} />
      <Route element={<AppShell demo={!!setup.data?.demo_data} />}>
        <Route index element={<Guard perm="inventory.view"><DashboardPage /></Guard>} />
        <Route path="ai" element={<Guard perm="ai.access"><CommandCenterPage /></Guard>} />
        <Route path="ai/:conversationId" element={<Guard perm="ai.access"><CommandCenterPage /></Guard>} />
        <Route path="inventory/products" element={<Guard perm="inventory.view"><ProductsPage /></Guard>} />
        <Route path="inventory/products/:id" element={<Guard perm="inventory.view"><ProductDetailPage /></Guard>} />
        <Route path="inventory/stock" element={<Guard perm="inventory.view"><StockOverviewPage /></Guard>} />
        <Route path="inventory/warehouses" element={<Guard perm="inventory.view"><WarehousesPage /></Guard>} />
        <Route path="inventory/warehouses/:id" element={<Guard perm="inventory.view"><WarehouseDetailPage /></Guard>} />
        <Route path="inventory/movements" element={<Guard perm="inventory.view"><MovementsPage /></Guard>} />
        <Route path="inventory/transfers" element={<Guard perm="inventory.view"><TransfersPage /></Guard>} />
        <Route path="inventory/transfers/:id" element={<Guard perm="inventory.view"><TransferDetailPage /></Guard>} />
        <Route path="inventory/adjustments" element={<Guard perm="inventory.view"><AdjustmentsPage /></Guard>} />
        <Route path="inventory/adjustments/:id" element={<Guard perm="inventory.view"><AdjustmentDetailPage /></Guard>} />
        <Route path="inventory/receipts" element={<Guard perm="purchasing.view"><GoodsReceiptsPage /></Guard>} />
        <Route path="inventory/receipts/:id" element={<Guard perm="purchasing.view"><GoodsReceiptDetailPage /></Guard>} />
        <Route path="inventory/alerts" element={<Guard perm="inventory.view"><AlertsPage /></Guard>} />
        <Route path="inventory/reports" element={<Guard perm="reports.view"><ReportsPage /></Guard>} />
        <Route path="purchasing/suppliers" element={<Guard perm="purchasing.view"><SuppliersPage /></Guard>} />
        <Route path="purchasing/requisitions" element={<Guard perm="purchasing.view"><RequisitionsPage /></Guard>} />
        <Route path="purchasing/requisitions/:id" element={<Guard perm="purchasing.view"><RequisitionDetailPage /></Guard>} />
        <Route path="purchasing/orders" element={<Guard perm="purchasing.view"><PurchaseOrdersPage /></Guard>} />
        <Route path="purchasing/orders/:id" element={<Guard perm="purchasing.view"><PurchaseOrderDetailPage /></Guard>} />
        {/* client/company-x */}
        <Route path="production/orders" element={<Guard perm="production.view"><ProductionOrdersPage /></Guard>} />
        <Route path="production/orders/:id" element={<Guard perm="production.view"><ProductionOrderPage /></Guard>} />
        <Route path="production/vendors" element={<Guard perm="production.view"><VendorWorkloadPage /></Guard>} />
        <Route path="production/challans" element={<Guard perm="production.view"><ChallansPage /></Guard>} />
        <Route path="production/structures" element={<Guard perm="production.view"><StructuresPage /></Guard>} />
        <Route path="production/sites" element={<Guard perm="production.view"><SitesPage /></Guard>} />
        <Route path="sales/customers" element={<Guard perm="production.view"><CustomersPage /></Guard>} />
        <Route path="sales/dispatch" element={<Guard perm="production.view"><DispatchNotesPage /></Guard>} />
        <Route path="approvals" element={<Guard perm="approvals.view"><ApprovalsPage /></Guard>} />
        <Route path="settings/company" element={<Guard perm="company.view"><CompanyPage /></Guard>} />
        <Route path="settings/users" element={<Guard perm="users.view"><UsersPage /></Guard>} />
        <Route path="settings/roles" element={<Guard perm="roles.view"><RolesPage /></Guard>} />
        <Route path="settings/departments" element={<Guard perm="departments.view"><DepartmentsPage /></Guard>} />
        <Route path="settings/units" element={<Guard perm="inventory.view"><UnitsPage /></Guard>} />
        <Route path="settings/system" element={<Guard perm="settings.view"><SystemSettingsPage /></Guard>} />
        <Route path="settings/audit" element={<Guard perm="audit.view"><AuditLogPage /></Guard>} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="*" element={<EmptyState title="Page not found" description="The page you’re looking for doesn’t exist." />} />
      </Route>
    </Routes>
  );
}
