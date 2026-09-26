import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { NotificationItem, PublicConfig } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { formatDateTime } from "../lib/format";
import { Bell, Close, Mark, Menu, Moon, Sun } from "./icons";
import { buttonClass, cx } from "./ui";

export function useConfig() {
  return useQuery({ queryKey: ["config"], queryFn: () => api.get<PublicConfig>("/api/config"), staleTime: 5 * 60_000 });
}

const iconButton = "inline-flex h-9 w-9 items-center justify-center rounded-md text-muted hover:bg-subtle hover:text-fg";

type NavVariant = "bar" | "menu" | "side";

const NAV_STYLES: Record<NavVariant, { base: string; active: string; idle: string }> = {
  // Header links: an underline on the header's bottom edge marks the page.
  bar: {
    base: "relative flex h-14 items-center text-sm",
    active: "text-fg after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-accent",
    idle: "text-muted hover:text-fg",
  },
  // Rows in the phone menu.
  menu: { base: "block rounded-md px-3 py-2.5 text-[15px]", active: "bg-subtle font-medium text-fg", idle: "text-muted hover:text-fg" },
  // Admin sections: tabs on narrow screens, a side list from lg up.
  side: {
    base: "-mb-px whitespace-nowrap border-b-2 py-2 text-sm lg:mb-0 lg:rounded-md lg:border-b-0 lg:px-3 lg:py-1.5",
    active: "border-accent font-medium text-fg lg:bg-subtle",
    idle: "border-transparent text-muted hover:text-fg",
  },
};

function NavItem({ to, children, end, variant = "bar" }: { to: string; children: React.ReactNode; end?: boolean; variant?: NavVariant }) {
  const style = NAV_STYLES[variant];
  return (
    <NavLink to={to} end={end} className={({ isActive }) => cx(style.base, isActive ? style.active : style.idle)}>
      {children}
    </NavLink>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme ?? "light");
  const next = theme === "dark" ? "light" : "dark";
  const toggle = () => {
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("theme", next);
    } catch {
      // Storage can be blocked (private mode); the choice then lasts until reload.
    }
    setTheme(next);
  };
  return (
    <button onClick={toggle} className={iconButton} aria-label={`Use ${next} theme`} title={`Use ${next} theme`}>
      {theme === "dark" ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
    </button>
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
  const config = useConfig();
  const tz = config.data?.default_timezone ?? "UTC";

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const unread = count.data?.count ?? 0;
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className={cx(iconButton, "relative")}
        aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}
        aria-expanded={open}
        aria-controls="notifications-panel"
      >
        <Bell className="h-[18px] w-[18px]" />
        {unread > 0 && (
          <span className="tabular absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-semibold text-accent-fg">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div
          id="notifications-panel"
          className="fixed inset-x-3 top-16 z-40 overflow-hidden rounded-lg border border-line bg-surface shadow-lg sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-2 sm:w-96"
        >
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
            <span className="text-sm font-semibold text-fg">Notifications</span>
            {unread > 0 && (
              <button className="text-xs font-medium text-accent-text hover:underline" onClick={() => readAll.mutate()}>
                Mark all read
              </button>
            )}
          </div>
          <div className="max-h-[70dvh] overflow-y-auto sm:max-h-96">
            {list.data?.length === 0 && <p className="px-4 py-8 text-center text-sm text-muted">Nothing yet.</p>}
            {list.data?.map((n) => (
              <Link
                key={n.id}
                to={n.booking_id ? `/bookings/${n.booking_id}` : "#"}
                onClick={() => setOpen(false)}
                className="flex gap-3 border-b border-line px-4 py-3 last:border-b-0 hover:bg-subtle"
              >
                <span aria-hidden className={cx("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", n.read_at ? "bg-transparent" : "bg-accent")} />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-fg">{n.title}</span>
                  <span className="mt-0.5 block whitespace-pre-line text-xs text-muted">{n.body}</span>
                  <span className="mt-1 block text-[11px] text-faint">{formatDateTime(n.created_at, tz)}</span>
                </span>
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

  const links = [
    { to: "/services", label: "Services" },
    { to: "/resources", label: "Resources" },
    ...(user
      ? [
          { to: "/dashboard", label: "Dashboard" },
          { to: "/book", label: "Book" },
          { to: "/bookings", label: "My bookings" },
        ]
      : []),
    ...(isStaff ? [{ to: "/admin", label: "Admin" }] : []),
  ];
  const signOut = async () => {
    setMenuOpen(false);
    await logout();
    navigate("/");
  };

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-30 border-b border-line bg-canvas/90 backdrop-blur supports-[backdrop-filter]:bg-canvas/80">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4 sm:px-6">
          <Link to="/" className="flex shrink-0 items-center gap-2 font-semibold tracking-tight text-fg" onClick={() => setMenuOpen(false)}>
            <Mark />
            {config.data?.business_name ?? "Bookings"}
          </Link>
          <nav className="hidden items-center gap-6 md:flex" aria-label="Main">
            {links.map((l) => (
              <NavItem key={l.to} to={l.to}>
                {l.label}
              </NavItem>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-1">
            <ThemeToggle />
            {/* Visibility sits on wrappers: `hidden` on the button itself would
                lose to the button's own `inline-flex`. */}
            {user ? (
              <>
                <NotificationBell />
                <div className="ml-2 hidden items-center gap-1 md:flex">
                  <Link to="/profile" className="max-w-40 truncate text-sm text-muted hover:text-fg">
                    {user.name}
                  </Link>
                  <button className={buttonClass("ghost")} onClick={signOut}>
                    Sign out
                  </button>
                </div>
              </>
            ) : (
              <>
                <Link to="/login" className={buttonClass("ghost", "md", "ml-1")}>
                  Sign in
                </Link>
                <div className="ml-1 hidden sm:block">
                  <Link to="/register" className={buttonClass()}>
                    Create account
                  </Link>
                </div>
              </>
            )}
            <button
              className={cx(iconButton, "md:hidden")}
              onClick={() => setMenuOpen((o) => !o)}
              aria-label={menuOpen ? "Close menu" : "Menu"}
              aria-expanded={menuOpen}
            >
              {menuOpen ? <Close className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>
          </div>
        </div>
        {menuOpen && (
          <div className="border-t border-line bg-surface md:hidden">
            <nav className="mx-auto max-w-6xl space-y-0.5 px-3 py-2" aria-label="Menu" onClick={() => setMenuOpen(false)}>
              {links.map((l) => (
                <NavItem key={l.to} to={l.to} variant="menu">
                  {l.label}
                </NavItem>
              ))}
              {!user && (
                <NavItem to="/register" variant="menu">
                  Create account
                </NavItem>
              )}
            </nav>
            {user && (
              <div className="flex items-center justify-between gap-3 border-t border-line px-6 py-3">
                <Link to="/profile" className="min-w-0" onClick={() => setMenuOpen(false)}>
                  <span className="block truncate text-sm font-medium text-fg">{user.name}</span>
                  <span className="block truncate text-xs text-muted">{user.email}</span>
                </Link>
                <button className={buttonClass("secondary", "sm")} onClick={signOut}>
                  Sign out
                </button>
              </div>
            )}
          </div>
        )}
      </header>
      <main className="mx-auto max-w-6xl px-4 pb-16 pt-6 sm:px-6 sm:pt-10">
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
    <div className="grid gap-6 lg:grid-cols-[176px_1fr] lg:gap-10">
      <aside className="min-w-0">
        <nav
          aria-label="Admin"
          className="-mx-4 flex gap-5 overflow-x-auto border-b border-line px-4 sm:-mx-6 sm:px-6 lg:sticky lg:top-24 lg:mx-0 lg:flex-col lg:gap-0.5 lg:border-b-0 lg:px-0"
        >
          {ADMIN_LINKS.filter((l) => can(l.permission)).map((l) => (
            <NavItem key={l.to} to={l.to} end={l.end} variant="side">
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
