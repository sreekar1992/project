import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { AuthProvider, useAuth } from "./lib/auth";
import { DashboardPage } from "./pages/DashboardPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PatientDetailPage } from "./pages/PatientDetailPage";
import { PatientsPage } from "./pages/PatientsPage";
import { SuperAdminPage } from "./pages/SuperAdminPage";
import { LoadingState } from "./components/LoadingState";

function isSuperAdmin(roles: string[] | undefined): boolean {
  return roles?.includes("SUPER_ADMIN") ?? false;
}

function ProtectedRoute() {
  const { authenticated, restoring } = useAuth();
  const location = useLocation();
  if (restoring) return <LoadingState label="Restoring your authorized workspace…" />;
  if (!authenticated) return <Navigate to="/login" replace state={{ from: location }} />;
  return <AppShell />;
}

function ClinicalDashboardRoute() {
  const { user } = useAuth();
  if (isSuperAdmin(user?.roles)) return <Navigate to="/super-admin" replace />;
  return <DashboardPage />;
}

function ClinicalWorkspaceRoute({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  if (isSuperAdmin(user?.roles)) return <Navigate to="/super-admin" replace />;
  return children;
}

function RoutedApp() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/" element={<ClinicalDashboardRoute />} />
        <Route path="/super-admin" element={<SuperAdminPage />} />
        <Route path="/patients" element={<ClinicalWorkspaceRoute><PatientsPage /></ClinicalWorkspaceRoute>} />
        <Route path="/patients/:patientId" element={<ClinicalWorkspaceRoute><PatientDetailPage /></ClinicalWorkspaceRoute>} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <RoutedApp />
      </BrowserRouter>
    </AuthProvider>
  );
}

export { useAuth } from "./lib/auth";
