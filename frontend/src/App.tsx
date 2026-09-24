import { Link, Route, Routes } from "react-router-dom";
import { RequireAuth } from "./auth/AuthContext";
import { AdminLayout, Layout } from "./components/Layout";
import { BookingDetailPage, BookingsPage, DashboardPage, ProfilePage } from "./pages/account";
import { AdminBookingsPage } from "./pages/admin/bookings";
import { AdminCalendarPage } from "./pages/admin/calendar";
import { AdminLocationsPage, AdminResourcesPage, AdminServicesPage } from "./pages/admin/catalog";
import { AdminConflictsPage, AdminOverviewPage } from "./pages/admin/overview";
import { AdminSchedulesPage } from "./pages/admin/schedules";
import { AdminAuditPage, AdminSettingsPage, AdminUsersPage } from "./pages/admin/system";
import { BookPage } from "./pages/book";
import {
  ForgotPasswordPage,
  HomePage,
  LoginPage,
  RegisterPage,
  ResetPasswordPage,
  ResourceDetailPage,
  ResourcesPage,
  ServiceDetailPage,
  ServicesPage,
} from "./pages/public";

function NotFound() {
  return (
    <div className="py-20 text-center">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <Link to="/" className="mt-4 inline-block text-brand-700 hover:underline">
        Go home
      </Link>
    </div>
  );
}

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        {/* Public */}
        <Route index element={<HomePage />} />
        <Route path="login" element={<LoginPage />} />
        <Route path="register" element={<RegisterPage />} />
        <Route path="forgot-password" element={<ForgotPasswordPage />} />
        <Route path="reset-password" element={<ResetPasswordPage />} />
        <Route path="services" element={<ServicesPage />} />
        <Route path="services/:id" element={<ServiceDetailPage />} />
        <Route path="resources" element={<ResourcesPage />} />
        <Route path="resources/:id" element={<ResourceDetailPage />} />

        {/* Signed-in users */}
        <Route path="dashboard" element={<RequireAuth><DashboardPage /></RequireAuth>} />
        <Route path="book" element={<RequireAuth><BookPage /></RequireAuth>} />
        <Route path="bookings" element={<RequireAuth><BookingsPage /></RequireAuth>} />
        <Route path="bookings/:id" element={<RequireAuth><BookingDetailPage /></RequireAuth>} />
        <Route path="profile" element={<RequireAuth><ProfilePage /></RequireAuth>} />

        {/* Staff / admin. The API enforces every permission independently. */}
        <Route path="admin" element={<RequireAuth staff><AdminLayout /></RequireAuth>}>
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
        </Route>
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
