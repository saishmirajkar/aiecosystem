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
import { ProductDetailPage, ProductsPage } from "@/features/products/ProductsPage";
import { ApprovalsPage } from "@/features/purchasing/ApprovalsPage";
import { GoodsReceiptDetailPage, GoodsReceiptsPage } from "@/features/purchasing/GoodsReceiptsPage";
import { PurchaseOrderDetailPage, PurchaseOrdersPage } from "@/features/purchasing/PurchaseOrdersPage";
import { RequisitionDetailPage, RequisitionsPage } from "@/features/purchasing/RequisitionsPage";
import { SuppliersPage } from "@/features/purchasing/SuppliersPage";
import { AuditLogPage, CompanyPage, DepartmentsPage, ProfilePage, RolesPage, SystemSettingsPage, UnitsPage, UsersPage } from "@/features/settings/SettingsPages";
import { WarehouseDetailPage, WarehousesPage } from "@/features/warehouses/WarehousesPage";
import { AttendancePage, OvertimeRecordsPage } from "@/features/workforce/AttendancePages";
import { EmployeeShell, MarketplacePage, MeHomePage, MeOpportunityPage, MeRecordPage, MyLeavePage, MyShiftsPage } from "@/features/workforce/EmployeePages";
import { EmployeeDetailPage, EmployeesPage, PayrollPage, WorkforceReportsPage, WorkforceSetupPage } from "@/features/workforce/HrPages";
import { LeaveApprovalsPage, OpportunityDetailPage, OvertimeBoardPage, SchedulePage, WorkforceDashboardPage } from "@/features/workforce/ManagerPages";
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
  const { user, loading, can } = useAuth();
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
  const demo = !!setup.data?.demo_data;
  const meRoutes = (wrap: (el: ReactNode) => ReactNode) => <>
    <Route path="me" element={wrap(<MeHomePage />)} />
    <Route path="me/shifts" element={wrap(<MyShiftsPage />)} />
    <Route path="me/overtime" element={wrap(<MarketplacePage />)} />
    <Route path="me/overtime/:id" element={wrap(<MeOpportunityPage />)} />
    <Route path="me/leave" element={wrap(<MyLeavePage />)} />
    <Route path="me/record" element={wrap(<MeRecordPage />)} />
  </>;
  // People whose only access is self-service get the mobile-first employee app.
  if (can("workforce.self") && !can("workforce.view") && !can("inventory.view")) {
    return (
      <Routes>
        <Route element={<EmployeeShell demo={demo} />}>
          {meRoutes((el) => el)}
          <Route path="me/account" element={<ProfilePage />} />
          <Route path="*" element={<Navigate to="/me" replace />} />
        </Route>
      </Routes>
    );
  }
  const narrow = (el: ReactNode) => <Guard perm="workforce.self"><div className="mx-auto max-w-2xl">{el}</div></Guard>;
  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route element={<AppShell demo={demo} />}>
        <Route index element={!can("inventory.view") && can("workforce.view") ? <Navigate to="/workforce" replace /> : <Guard perm="inventory.view"><DashboardPage /></Guard>} />
        {meRoutes(narrow)}
        <Route path="me/account" element={<Navigate to="/profile" replace />} />
        <Route path="workforce" element={<Guard perm="workforce.view"><WorkforceDashboardPage /></Guard>} />
        <Route path="workforce/schedule" element={<Guard perm="workforce.view"><SchedulePage /></Guard>} />
        <Route path="workforce/leave" element={<Guard perm="workforce.view"><LeaveApprovalsPage /></Guard>} />
        <Route path="workforce/overtime" element={<Guard perm="workforce.view"><OvertimeBoardPage /></Guard>} />
        <Route path="workforce/overtime/:id" element={<Guard perm="workforce.view"><OpportunityDetailPage /></Guard>} />
        <Route path="workforce/attendance" element={<Guard perm="workforce.view"><AttendancePage /></Guard>} />
        <Route path="workforce/overtime-records" element={<Guard perm="workforce.view"><OvertimeRecordsPage /></Guard>} />
        <Route path="workforce/employees" element={<Guard perm="workforce.view"><EmployeesPage /></Guard>} />
        <Route path="workforce/employees/:id" element={<Guard perm="workforce.view"><EmployeeDetailPage /></Guard>} />
        <Route path="workforce/payroll" element={<Guard perm="workforce.payroll"><PayrollPage /></Guard>} />
        <Route path="workforce/reports" element={<Guard perm="workforce.view"><WorkforceReportsPage /></Guard>} />
        <Route path="workforce/setup" element={<Guard perm="workforce.view"><WorkforceSetupPage /></Guard>} />
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
