import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TdHTMLAttributes, TextareaHTMLAttributes } from "react";
import { Children, cloneElement, isValidElement, useEffect, useId } from "react";
import { Link } from "react-router-dom";
import type { BookingStatus } from "../api/types";
import { titleCase } from "../lib/format";
import { ArrowLeft, Close, MapPin } from "./icons";

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

/** Inline text links. */
export const linkClass = "font-medium text-accent-text underline-offset-2 hover:underline";

/** Opens a location in Google Maps: a new tab, or the Maps app on phones. */
export function MapLink({ url, children = "Open in Google Maps", className }: { url: string | null | undefined; children?: ReactNode; className?: string }) {
  if (!url || !/^https?:\/\//i.test(url)) return null;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className={cx("inline-flex items-center gap-1.5 text-sm", linkClass, className)}>
      <MapPin className="h-4 w-4 shrink-0" />
      {children}
    </a>
  );
}

export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
      <ArrowLeft className="h-4 w-4" />
      {children}
    </Link>
  );
}

type Variant = "primary" | "secondary" | "danger" | "ghost";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:bg-accent-hover disabled:opacity-50",
  secondary: "border border-line-strong bg-surface text-fg hover:bg-subtle disabled:text-faint",
  danger: "bg-danger text-white hover:opacity-90 disabled:opacity-50 dark:text-canvas",
  ghost: "text-muted hover:bg-subtle hover:text-fg disabled:text-faint",
};

/** The button look, for links that act as buttons. */
export function buttonClass(variant: Variant = "primary", size: Size = "md", extra?: string): string {
  return cx(
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium transition-colors disabled:cursor-not-allowed",
    size === "sm" ? "h-8 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
    VARIANTS[variant],
    extra,
  );
}

export function Button({
  variant = "primary",
  size = "md",
  loading,
  className,
  children,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size; loading?: boolean }) {
  return (
    <button type="button" {...props} disabled={disabled || loading} className={buttonClass(variant, size, className)}>
      {loading && <Spinner className="h-4 w-4" />}
      {children}
    </button>
  );
}

export function Spinner({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={cx("animate-spin text-current", className)} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z" />
    </svg>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-10 text-sm text-muted" role="status">
      <Spinner className="h-4 w-4" /> {label}
    </div>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("rounded-lg border border-line bg-surface p-4 sm:p-5", className)}>{children}</div>;
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-fg sm:text-2xl">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: ReactNode }) {
  const autoId = useId();
  // Link the label to a single control via htmlFor/id so its accessible name is just the label.
  const control = isValidElement<{ id?: string; "aria-describedby"?: string }>(children) ? children : null;
  const id = control?.props.id ?? autoId;
  const hintId = `${id}-hint`;
  const note = error ?? hint;
  return (
    <div>
      <label htmlFor={control ? id : undefined} className="mb-1.5 block text-sm font-medium text-fg">
        {label}
      </label>
      {control ? cloneElement(control, { id, "aria-describedby": note ? hintId : undefined }) : children}
      {note && (
        <span id={hintId} className={cx("mt-1.5 block text-xs", error ? "text-danger-text" : "text-muted")}>
          {note}
        </span>
      )}
    </div>
  );
}

const inputClass =
  "block h-9 rounded-md border border-line-strong bg-surface px-3 text-sm text-fg placeholder:text-faint focus:border-accent focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-0 disabled:bg-subtle disabled:text-muted";

/** Full width unless the caller sets its own width class. */
function controlClass(extra?: string): string {
  return cx(inputClass, !/(^|\s)w-/.test(extra ?? "") && "w-full", extra);
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={controlClass(props.className)} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea rows={3} {...props} className={cx(controlClass(props.className), "h-auto py-2")} />;
}

export function Select({ children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...props} className={cx(controlClass(props.className), "pr-8")}>
      {children}
    </select>
  );
}

export function Checkbox({ label, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return (
    <label className="inline-flex items-center gap-2 text-sm text-fg">
      <input type="checkbox" {...props} className="h-4 w-4 rounded border-line-strong" />
      {label}
    </label>
  );
}

// Status is shown as a coloured dot next to plain text: readable at a glance
// without a wall of coloured pills.
const STATUS_DOTS: Record<string, string> = {
  CONFIRMED: "bg-emerald-500",
  ACTIVE: "bg-emerald-500",
  PENDING: "bg-amber-500",
  WAITLISTED: "bg-sky-500",
  CONFLICTED: "bg-red-500",
  SUSPENDED: "bg-red-500",
  NO_SHOW: "bg-orange-500",
  COMPLETED: "bg-stone-400",
  CANCELLED: "bg-stone-400",
  RESCHEDULED: "bg-stone-400",
  INACTIVE: "bg-stone-400",
};

export function StatusBadge({ status }: { status: BookingStatus | string }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium text-fg">
      <span aria-hidden className={cx("h-1.5 w-1.5 rounded-full", STATUS_DOTS[status] ?? "bg-stone-400")} />
      {titleCase(status)}
    </span>
  );
}

const BADGE_TONES = {
  neutral: "border-line text-muted",
  accent: "border-transparent bg-accent-soft text-accent-text",
  warn: "border-transparent bg-warn-soft text-warn-text",
  danger: "border-transparent bg-danger-soft text-danger-text",
};

/** A small label for a kind or a count ("Group", "Holiday"), not a status. */
export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: keyof typeof BADGE_TONES }) {
  return (
    <span className={cx("inline-flex items-center whitespace-nowrap rounded border px-1.5 py-px text-xs", BADGE_TONES[tone])}>{children}</span>
  );
}

export function Alert({ tone = "error", children }: { tone?: "error" | "info" | "success" | "warning"; children: ReactNode }) {
  const styles = {
    error: "bg-danger-soft text-danger-text",
    info: "bg-info-soft text-info-text",
    success: "bg-ok-soft text-ok-text",
    warning: "bg-warn-soft text-warn-text",
  }[tone];
  return (
    <div role={tone === "error" ? "alert" : "status"} className={cx("rounded-md px-3.5 py-2.5 text-sm", styles)}>
      {children}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line-strong px-6 py-10 text-center">
      <p className="text-sm font-medium text-fg">{title}</p>
      {children && <div className="mt-1.5 text-sm text-muted">{children}</div>}
    </div>
  );
}

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  wide,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  // A bottom sheet on phones, a centred dialog from sm up.
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-start sm:p-4 sm:pt-16" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal
        aria-label={title}
        onMouseDown={(e) => e.stopPropagation()}
        className={cx(
          "flex max-h-[92dvh] w-full flex-col rounded-t-xl border border-line bg-surface shadow-xl sm:max-h-[85vh] sm:rounded-lg",
          wide ? "sm:max-w-3xl" : "sm:max-w-lg",
        )}
      >
        <div className="flex items-center justify-between gap-4 border-b border-line px-4 py-3 sm:px-5">
          <h2 className="text-base font-semibold text-fg">{title}</h2>
          <button onClick={onClose} className="-mr-1 rounded-md p-1.5 text-muted hover:bg-subtle hover:text-fg" aria-label="Close">
            <Close />
          </button>
        </div>
        <div className="overflow-y-auto px-4 py-4 sm:px-5">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-line px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-5">{footer}</div>}
      </div>
    </div>
  );
}

export function Tabs<T extends string>({
  value,
  onChange,
  tabs,
}: {
  value: T;
  onChange: (v: T) => void;
  tabs: { value: T; label: ReactNode }[];
}) {
  return (
    <div className="-mx-4 mb-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <div className="flex min-w-max gap-5 border-b border-line">
        {tabs.map((t) => (
          <button
            key={t.value}
            onClick={() => onChange(t.value)}
            className={cx(
              "-mb-px border-b-2 py-2 text-sm font-medium",
              value === t.value ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** On phones each row becomes a labelled block; the labels come from `head`. */
export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  const labels = head.map((h) => (typeof h === "string" ? h : ""));
  const rows = Children.map(children, (row) => {
    if (!isValidElement<{ children?: ReactNode }>(row) || row.type !== "tr") return row;
    let index = 0;
    const cells = Children.map(row.props.children, (cell) => {
      if (!isValidElement<{ colSpan?: number }>(cell)) return cell;
      // A cell spanning columns (an empty-table message) has no single label.
      const span = cell.props.colSpan ?? 1;
      const label = span > 1 ? "" : (labels[index] ?? "");
      index += span;
      return cloneElement(cell as React.ReactElement<Record<string, unknown>>, { "data-label": label });
    });
    return cloneElement(row, {}, cells);
  });
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface">
      <table className="stack-table min-w-full text-sm">
        <thead>
          <tr className="border-b border-line">
            {head.map((h, i) => (
              <th key={i} className="px-4 py-2.5 text-left text-xs font-medium text-muted">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">{rows}</tbody>
      </table>
    </div>
  );
}

export function Td({ children, className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  // One wrapper, so a stacked cell on a phone is always "label | content".
  return (
    <td {...props} className={cx("px-4 py-2.5 align-top text-fg", className)}>
      <div className="min-w-0">{children}</div>
    </td>
  );
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: "warn" }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <div className="text-xs text-muted">{label}</div>
      <div className={cx("tabular mt-1 text-2xl font-semibold tracking-tight", tone === "warn" ? "text-danger-text" : "text-fg")}>{value}</div>
    </div>
  );
}

export function Pagination({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  return (
    <div className="mt-3 flex items-center justify-between text-sm text-muted">
      <span className="tabular">
        {offset + 1}–{Math.min(offset + limit, total)} of {total}
      </span>
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Previous
        </Button>
        <Button variant="secondary" size="sm" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          Next
        </Button>
      </div>
    </div>
  );
}
