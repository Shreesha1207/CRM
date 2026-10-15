import { Link, Route, Routes } from "react-router-dom";
import { RequireAuth } from "./auth/AuthContext";
import { AdminShell } from "./components/AdminShell";
import { Layout } from "./components/Layout";
import { linkClass } from "./components/ui";
import { BookingDetailPage, BookingsPage, DashboardPage, ProfilePage } from "./pages/account";
import { AdminBookingsPage } from "./pages/admin/bookings";
import { AdminCalendarPage } from "./pages/admin/calendar";
import { AdminLocationsPage, AdminResourcesPage, AdminServicesPage } from "./pages/admin/catalog";
import { AdminLoginPage } from "./pages/admin/login";
import { AdminConflictsPage, AdminOverviewPage } from "./pages/admin/overview";
import { AdminSchedulesPage } from "./pages/admin/schedules";
import { AdminAuditPage, AdminSettingsPage, AdminUsersPage } from "./pages/admin/system";
import { BookPage } from "./pages/book";
import {
  ForgotPasswordPage,
  HomePage,
  InvitationPage,
  LoginPage,
  RegisterPage,
  ResetPasswordPage,
  ResourceDetailPage,
  ResourcesPage,
  ServiceDetailPage,
  ServicesPage,
} from "./pages/public";

function NotFound({ home = "/" }: { home?: string }) {
  return (
    <div className="py-20 text-center">
      <p className="tabular text-sm font-medium text-muted">404</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">Page not found</h1>
      <Link to={home} className={`mt-4 inline-block text-sm ${linkClass}`}>
        Go home
      </Link>
    </div>
  );
}

export function App() {
  return (
    <Routes>
      {/* Admin console: its own sign-in and shell, separate from the customer site.
          The API enforces every permission independently. */}
      <Route path="admin/login" element={<AdminLoginPage />} />
      <Route path="admin" element={<RequireAuth staff><AdminShell /></RequireAuth>}>
        <Route index element={<AdminOverviewPage />} />
        <Route path="calendar" element={<AdminCalendarPage />} />
        <Route path="bookings" element={<AdminBookingsPage />} />
        <Route path="conflicts" element={<AdminConflictsPage />} />
        <Route path="resources" element={<AdminResourcesPage />} />
        <Route path="services" element={<AdminServicesPage />} />
        <Route path="schedules" element={<AdminSchedulesPage />} />
        <Route path="locations" element={<AdminLocationsPage />} />
        <Route path="users" element={<AdminUsersPage />} />
        <Route path="settings" element={<AdminSettingsPage />} />
        <Route path="audit" element={<AdminAuditPage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="*" element={<NotFound home="/admin" />} />
      </Route>

      <Route element={<Layout />}>
        {/* Public */}
        <Route index element={<HomePage />} />
        <Route path="login" element={<LoginPage />} />
        <Route path="register" element={<RegisterPage />} />
        <Route path="forgot-password" element={<ForgotPasswordPage />} />
        <Route path="reset-password" element={<ResetPasswordPage />} />
        <Route path="invitation" element={<InvitationPage />} />
        <Route path="services" element={<ServicesPage />} />
        <Route path="services/:id" element={<ServiceDetailPage />} />
        <Route path="resources" element={<ResourcesPage />} />
        <Route path="resources/:id" element={<ResourceDetailPage />} />

        {/* Customers' own area */}
        <Route path="dashboard" element={<RequireAuth customer><DashboardPage /></RequireAuth>} />
        <Route path="book" element={<RequireAuth customer><BookPage /></RequireAuth>} />
        <Route path="bookings" element={<RequireAuth customer><BookingsPage /></RequireAuth>} />
        <Route path="bookings/:id" element={<RequireAuth customer><BookingDetailPage /></RequireAuth>} />
        <Route path="profile" element={<RequireAuth customer><ProfilePage /></RequireAuth>} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
