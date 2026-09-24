import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { NotificationItem, PublicConfig } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { cx } from "./ui";

export function useConfig() {
  return useQuery({ queryKey: ["config"], queryFn: () => api.get<PublicConfig>("/api/config"), staleTime: 5 * 60_000 });
}

function NavItem({ to, children, end }: { to: string; children: React.ReactNode; end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cx(
          "rounded-md px-3 py-2 text-sm font-medium",
          isActive ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
        )
      }
    >
      {children}
    </NavLink>
  );
}

function NotificationBell() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const count = useQuery({
    queryKey: ["notifications", "count"],
    queryFn: () => api.get<{ count: number }>("/api/notifications/unread-count"),
    refetchInterval: 60_000,
  });
  const list = useQuery({
    queryKey: ["notifications", "list"],
    queryFn: () => api.get<NotificationItem[]>("/api/notifications", { limit: 15 }),
    enabled: open,
  });
  const readAll = useMutation({
    mutationFn: () => api.post("/api/notifications/read-all"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  useEffect(() => {
    const onClick = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const unread = count.data?.count ?? 0;
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="relative rounded-full p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
        aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}
      >
        <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
        </svg>
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-semibold text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-80 rounded-xl border border-slate-200 bg-white shadow-lg">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
            <span className="text-sm font-semibold">Notifications</span>
            {unread > 0 && (
              <button className="text-xs text-brand-600 hover:underline" onClick={() => readAll.mutate()}>
                Mark all read
              </button>
            )}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {list.data?.length === 0 && <p className="px-4 py-6 text-center text-sm text-slate-500">Nothing yet.</p>}
            {list.data?.map((n) => (
              <Link
                key={n.id}
                to={n.booking_id ? `/bookings/${n.booking_id}` : "#"}
                onClick={() => setOpen(false)}
                className={cx("block border-b border-slate-50 px-4 py-3 hover:bg-slate-50", !n.read_at && "bg-brand-50/50")}
              >
                <div className="text-sm font-medium text-slate-800">{n.title}</div>
                <div className="mt-0.5 whitespace-pre-line text-xs text-slate-500">{n.body}</div>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function Layout() {
  const { user, isStaff, logout } = useAuth();
  const config = useConfig();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  const links = (
    <>
      <NavItem to="/services">Services</NavItem>
      <NavItem to="/resources">Resources</NavItem>
      {user && (
        <>
          <NavItem to="/dashboard">Dashboard</NavItem>
          <NavItem to="/book">Book</NavItem>
          <NavItem to="/bookings">My bookings</NavItem>
        </>
      )}
      {isStaff && <NavItem to="/admin">Admin</NavItem>}
    </>
  );

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4">
          <Link to="/" className="flex items-center gap-2 font-semibold text-slate-900">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-600 text-sm text-white">B</span>
            {config.data?.business_name ?? "Bookings"}
          </Link>
          <nav className="hidden flex-1 items-center gap-1 md:flex">{links}</nav>
          <div className="ml-auto flex items-center gap-2">
            {user ? (
              <>
                <NotificationBell />
                <Link to="/profile" className="hidden text-sm text-slate-600 hover:text-slate-900 sm:block">
                  {user.name}
                </Link>
                <button
                  className="rounded-md px-3 py-2 text-sm text-slate-600 hover:bg-slate-100"
                  onClick={async () => {
                    await logout();
                    navigate("/");
                  }}
                >
                  Sign out
                </button>
              </>
            ) : (
              <>
                <NavItem to="/login">Sign in</NavItem>
                <Link to="/register" className="rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700">
                  Create account
                </Link>
              </>
            )}
            <button className="rounded-md p-2 text-slate-600 md:hidden" onClick={() => setMenuOpen((o) => !o)} aria-label="Menu">
              ☰
            </button>
          </div>
        </div>
        {menuOpen && (
          <nav className="flex flex-col gap-1 border-t border-slate-100 px-4 py-2 md:hidden" onClick={() => setMenuOpen(false)}>
            {links}
          </nav>
        )}
      </header>
      <main className="mx-auto max-w-7xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}

const ADMIN_LINKS: { to: string; label: string; permission: string; end?: boolean }[] = [
  { to: "/admin", label: "Overview", permission: "reports:view", end: true },
  { to: "/admin/calendar", label: "Calendar", permission: "bookings:view_all" },
  { to: "/admin/bookings", label: "Bookings", permission: "bookings:view_all" },
  { to: "/admin/conflicts", label: "Conflicts", permission: "conflicts:manage" },
  { to: "/admin/resources", label: "Resources", permission: "resources:manage" },
  { to: "/admin/services", label: "Services", permission: "services:manage" },
  { to: "/admin/schedules", label: "Schedules", permission: "schedules:manage" },
  { to: "/admin/locations", label: "Locations", permission: "locations:manage" },
  { to: "/admin/users", label: "Users", permission: "users:view" },
  { to: "/admin/settings", label: "Settings", permission: "settings:manage" },
  { to: "/admin/audit", label: "Audit log", permission: "audit:view" },
];

export function AdminLayout() {
  const { can } = useAuth();
  return (
    <div className="grid gap-8 lg:grid-cols-[200px_1fr]">
      <aside>
        <nav className="flex gap-1 overflow-x-auto lg:sticky lg:top-20 lg:flex-col">
          {ADMIN_LINKS.filter((l) => can(l.permission)).map((l) => (
            <NavItem key={l.to} to={l.to} end={l.end}>
              {l.label}
            </NavItem>
          ))}
        </nav>
      </aside>
      <section className="min-w-0">
        <Outlet />
      </section>
    </div>
  );
}
