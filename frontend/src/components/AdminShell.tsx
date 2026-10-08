import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { titleCase } from "../lib/format";
import { Calendar, Clock, ExternalLink, FileText, Grid, List, MapPin, Mark, Menu, People, Person, SignOut, Sliders, Tag, Warning } from "./icons";
import { NotificationBell, ThemeToggle, iconButton, useConfig } from "./Layout";
import { cx } from "./ui";

type AdminLink = { to: string; label: string; permission: string; icon: (p: { className?: string }) => React.ReactNode; end?: boolean };

const ADMIN_NAV: { label: string; links: AdminLink[] }[] = [
  {
    label: "Operations",
    links: [
      { to: "/admin", label: "Overview", permission: "reports:view", icon: Grid, end: true },
      { to: "/admin/calendar", label: "Calendar", permission: "bookings:view_all", icon: Calendar },
      { to: "/admin/bookings", label: "Bookings", permission: "bookings:view_all", icon: List },
      { to: "/admin/conflicts", label: "Conflicts", permission: "conflicts:manage", icon: Warning },
    ],
  },
  {
    label: "Catalogue",
    links: [
      { to: "/admin/resources", label: "Resources", permission: "resources:manage", icon: Person },
      { to: "/admin/services", label: "Services", permission: "services:manage", icon: Tag },
      { to: "/admin/schedules", label: "Schedules", permission: "schedules:manage", icon: Clock },
      { to: "/admin/locations", label: "Locations", permission: "locations:manage", icon: MapPin },
    ],
  },
  {
    label: "Administration",
    links: [
      { to: "/admin/users", label: "Users", permission: "users:view", icon: People },
      { to: "/admin/settings", label: "Settings", permission: "settings:manage", icon: Sliders },
      { to: "/admin/audit", label: "Audit log", permission: "audit:view", icon: FileText },
    ],
  },
];

const sideRow = "flex items-center gap-3 rounded-md px-2.5 py-2 text-sm";

function Sidebar({ onSignOut }: { onSignOut: () => void }) {
  const { user, can } = useAuth();
  const config = useConfig();
  const groups = ADMIN_NAV.map((g) => ({ ...g, links: g.links.filter((l) => can(l.permission)) })).filter((g) => g.links.length);
  const initials = (user?.name ?? "")
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <div className="flex h-full flex-col bg-side text-side-fg">
      <div className="flex h-14 shrink-0 items-center gap-2.5 px-4">
        <Mark className="h-6 w-6 shrink-0" />
        <span className="min-w-0 leading-tight">
          <span className="block truncate text-sm font-semibold tracking-tight">{config.data?.business_name ?? "Bookings"}</span>
          <span className="block text-[11px] text-side-muted">Admin console</span>
        </span>
      </div>
      <nav aria-label="Admin" className="flex-1 space-y-5 overflow-y-auto px-3 py-3">
        {groups.map((g) => (
          <div key={g.label}>
            <p className="px-2.5 pb-1.5 text-[11px] font-medium uppercase tracking-wider text-side-muted">{g.label}</p>
            <div className="space-y-0.5">
              {g.links.map(({ to, label, icon: Icon, end }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    cx(sideRow, isActive ? "bg-side-active font-medium text-side-fg" : "text-side-muted hover:bg-side-hover hover:text-side-fg")
                  }
                >
                  {({ isActive }) => (
                    <>
                      <Icon className={cx("h-[18px] w-[18px] shrink-0", isActive && "text-side-accent")} />
                      {label}
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>
      <div className="shrink-0 space-y-0.5 border-t border-side-line p-3">
        <Link to="/" className={cx(sideRow, "text-side-muted hover:bg-side-hover hover:text-side-fg")}>
          <ExternalLink className="h-[18px] w-[18px] shrink-0" />
          Customer site
        </Link>
        <Link to="/admin/profile" className={cx(sideRow, "hover:bg-side-hover")}>
          <span aria-hidden className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-side-active text-[11px] font-semibold">
            {initials}
          </span>
          <span className="min-w-0">
            <span className="block truncate font-medium text-side-fg">{user?.name}</span>
            <span className="block truncate text-xs text-side-muted">{titleCase(user?.role ?? "")}</span>
          </span>
        </Link>
        <button onClick={onSignOut} className={cx(sideRow, "w-full text-side-muted hover:bg-side-hover hover:text-side-fg")}>
          <SignOut className="h-[18px] w-[18px] shrink-0" />
          Sign out
        </button>
      </div>
    </div>
  );
}

/** Name of the console section being shown, for the top bar. */
function sectionLabel(pathname: string): string {
  if (pathname === "/admin/profile") return "Profile";
  const links = ADMIN_NAV.flatMap((g) => g.links);
  return links.find((l) => (l.end ? pathname === l.to : pathname.startsWith(l.to)))?.label ?? "";
}

/** The admin console: its own app shell, separate from the customer site. */
export function AdminShell() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => setMenuOpen(false), [pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const signOut = async () => {
    await logout();
    navigate("/admin/login");
  };

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[240px_1fr]">
      <aside className="hidden lg:sticky lg:top-0 lg:block lg:h-dvh">
        <Sidebar onSignOut={signOut} />
      </aside>
      {menuOpen && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Admin menu">
          <div className="absolute inset-0 bg-black/50" onClick={() => setMenuOpen(false)} />
          <div className="absolute inset-y-0 left-0 w-72 max-w-[85vw] shadow-xl">
            <Sidebar onSignOut={signOut} />
          </div>
        </div>
      )}
      <div className="min-w-0">
        <header className="sticky top-0 z-30 border-b border-line bg-canvas/90 backdrop-blur supports-[backdrop-filter]:bg-canvas/80">
          <div className="flex h-14 items-center gap-2 px-4 sm:px-6 lg:px-8">
            <button className={cx(iconButton, "-ml-2 lg:hidden")} onClick={() => setMenuOpen(true)} aria-label="Open menu" aria-expanded={menuOpen}>
              <Menu className="h-5 w-5" />
            </button>
            <p className="min-w-0 truncate text-sm">
              <span className="text-muted">Admin console</span>
              <span className="px-1.5 text-faint">/</span>
              <span className="font-medium text-fg">{sectionLabel(pathname)}</span>
            </p>
            <div className="ml-auto flex items-center gap-1">
              <ThemeToggle />
              <NotificationBell bookingPath={(id) => `/admin/bookings?open=${id}`} />
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 pb-16 pt-6 sm:px-6 sm:pt-8 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
