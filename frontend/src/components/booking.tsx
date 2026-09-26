import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Alternative, Availability, Booking, Slot } from "../api/types";
import { addDays, dayAndMonth, formatDateTime, formatDayTimes, formatMoney, formatRange, formatTime, longDate, titleCase } from "../lib/format";
import { ChevronLeft, ChevronRight } from "./icons";
import { Alert, Input, Loading, StatusBadge, cx } from "./ui";

export interface PickedSlot {
  start: string;
  end: string;
  resourceId: string | null; // null = let the system pick any free resource
  timezone: string;
  slot?: Slot;
}

const stepButton =
  "inline-flex h-9 w-9 items-center justify-center rounded-md border border-line-strong bg-surface text-muted hover:bg-subtle hover:text-fg disabled:cursor-not-allowed disabled:opacity-40";

export function DateNav({ date, onChange, min }: { date: string; onChange: (d: string) => void; min?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <div className="flex items-center gap-1.5">
        <button type="button" className={stepButton} disabled={!!min && date <= min} onClick={() => onChange(addDays(date, -1))} aria-label="Previous day">
          <ChevronLeft />
        </button>
        <Input type="date" value={date} min={min} onChange={(e) => e.target.value && onChange(e.target.value)} className="w-auto" aria-label="Date" />
        <button type="button" className={stepButton} onClick={() => onChange(addDays(date, 1))} aria-label="Next day">
          <ChevronRight />
        </button>
      </div>
      <span className="text-sm text-muted">{longDate(date)}</span>
    </div>
  );
}

function SlotButton({ label, sub, available, selected, title, onClick }: {
  label: string;
  sub?: string;
  available: boolean;
  selected: boolean;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={!available}
      onClick={onClick}
      className={cx(
        "tabular rounded-md border px-2 py-2 text-sm font-medium transition-colors",
        selected && "border-accent bg-accent text-accent-fg",
        !selected && available && "border-line-strong bg-surface text-fg hover:border-accent",
        !available && "cursor-not-allowed border-transparent bg-subtle text-faint line-through",
      )}
    >
      {label}
      {sub && <span className={cx("block text-[11px] font-normal no-underline", selected ? "opacity-80" : "text-muted")}>{sub}</span>}
    </button>
  );
}

const slotGrid = "grid grid-cols-3 gap-2 min-[400px]:grid-cols-4 sm:grid-cols-5 lg:grid-cols-6";

function NoTimes() {
  return <p className="rounded-md bg-subtle px-4 py-6 text-center text-sm text-muted">No times on this date. Try another day.</p>;
}

/** Shows slots for one resource, or -- with resourceId null -- for every
 * resource that offers the service (the "time first" flow). */
export function SlotPicker({
  serviceId,
  resourceId,
  date,
  quantity = 1,
  duration,
  selected,
  onSelect,
  showUnavailable = true,
  allowWaitlist = false,
  moving,
}: {
  serviceId: string;
  resourceId: string | null;
  date: string;
  quantity?: number;
  /** A customer-chosen length in minutes; omitted for the service's own length. */
  duration?: number;
  selected: PickedSlot | null;
  onSelect: (slot: PickedSlot) => void;
  showUnavailable?: boolean;
  /** Full group sessions can be chosen, to join their waitlist. */
  allowWaitlist?: boolean;
  /** The booking being rescheduled: its own time is not a clash, and its current slot is marked. */
  moving?: Booking;
}) {
  const availability = useQuery({
    queryKey: ["availability", serviceId, resourceId, date, quantity, duration, moving?.id],
    queryFn: () =>
      api.get<Availability>("/api/availability", {
        service_id: serviceId,
        resource_id: resourceId,
        date,
        quantity,
        duration_minutes: duration,
        exclude_booking_id: moving?.id,
      }),
  });

  if (availability.isLoading) return <Loading label="Checking availability…" />;
  if (availability.error) return <Alert>{(availability.error as Error).message}</Alert>;
  const data = availability.data!;

  if (resourceId) {
    const resource = data.resources[0];
    const slots = (resource?.slots ?? []).filter((s) => showUnavailable || s.available);
    if (!slots.length) return <NoTimes />;
    const waitlistable = (s: Slot) => allowWaitlist && s.status === "CAPACITY_REACHED";
    return (
      <div>
        <div className={slotGrid}>
          {slots.map((s) => {
            const current =
              moving?.resource.id === resource.resource_id && Date.parse(s.start) === Date.parse(moving.start_datetime);
            const sub = current
              ? "Current"
              : s.available
                ? s.capacity != null
                  ? `${s.remaining} left`
                  : undefined
                : s.status === "CAPACITY_REACHED"
                  ? waitlistable(s)
                    ? "Full · waitlist"
                    : "Full"
                  : titleCase(s.status);
            return (
              <SlotButton
                key={s.start}
                label={s.start_time}
                sub={sub}
                available={!current && (s.available || waitlistable(s))}
                title={current ? "The booking's current time" : waitlistable(s) ? "This session is full: choose it to join the waitlist" : s.message}
                selected={selected?.start === s.start && selected.resourceId === resource.resource_id}
                onClick={() => onSelect({ start: s.start, end: s.end, resourceId: resource.resource_id, timezone: resource.timezone, slot: s })}
              />
            );
          })}
        </div>
        <p className="mt-3 text-xs text-muted">
          Times shown in {resource.timezone}.{slots.some(waitlistable) && " Choose a full session to join its waitlist."}
        </p>
      </div>
    );
  }

  const slots = data.slots.filter((s) => showUnavailable || s.available);
  if (!slots.length) return <NoTimes />;
  const tz = data.resources[0]?.timezone ?? "UTC";
  return (
    <div>
      <div className={slotGrid}>
        {slots.map((s) => (
          <SlotButton
            key={s.start}
            label={s.start_time}
            sub={s.available ? `${s.resource_ids.length} free` : "Taken"}
            available={s.available}
            selected={selected?.start === s.start}
            onClick={() => onSelect({ start: s.start, end: s.end, resourceId: null, timezone: tz })}
          />
        ))}
      </div>
      <p className="mt-3 text-xs text-muted">Times shown in {tz}.</p>
    </div>
  );
}

/** Which resources are free at a chosen time (time-first flow). */
export function useFreeResources(serviceId: string, date: string, start: string | null, quantity = 1, duration?: number) {
  const availability = useQuery({
    queryKey: ["availability", serviceId, null, date, quantity, duration, undefined],
    queryFn: () => api.get<Availability>("/api/availability", { service_id: serviceId, date, quantity, duration_minutes: duration }),
    enabled: !!start,
  });
  return (availability.data?.resources ?? []).filter((r) => r.slots.some((s) => s.start === start && s.available));
}

export function Alternatives({
  serviceId,
  resourceId,
  start,
  quantity = 1,
  duration,
  onPick,
}: {
  serviceId: string;
  resourceId: string;
  start: string;
  quantity?: number;
  duration?: number;
  onPick: (alt: Alternative) => void;
}) {
  const alternatives = useQuery({
    queryKey: ["alternatives", serviceId, resourceId, start, quantity, duration],
    queryFn: () =>
      api.get<Alternative[]>("/api/availability/alternatives", {
        service_id: serviceId,
        resource_id: resourceId,
        start,
        quantity,
        duration_minutes: duration,
      }),
  });
  if (alternatives.isLoading) return <Loading label="Looking for alternatives…" />;
  if (!alternatives.data?.length) return <p className="text-sm text-muted">No nearby alternatives found.</p>;
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium text-fg">Suggested alternatives</p>
      <div className="grid gap-2 sm:grid-cols-2">
        {alternatives.data.map((a) => (
          <button
            key={`${a.resource_id}-${a.start}`}
            onClick={() => onPick(a)}
            className="rounded-md border border-line-strong bg-surface px-3 py-2 text-left text-sm hover:border-accent"
          >
            <span className="block font-medium text-fg">{a.resource_name}</span>
            <span className="text-xs text-muted">{formatDateTime(a.start, a.timezone)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function WaitlistPosition({ booking }: { booking: Booking }) {
  if (booking.waitlist_position == null) return null;
  return <span className="text-xs text-muted">#{booking.waitlist_position} on the waitlist</span>;
}

export function BookingSummary({ booking, compact }: { booking: Booking; compact?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-base font-semibold text-fg">{booking.service.name}</span>
        <StatusBadge status={booking.status} />
        <WaitlistPosition booking={booking} />
      </div>
      <div className="tabular mt-1 text-sm text-fg">{formatRange(booking.start_datetime, booking.end_datetime, booking.timezone)}</div>
      <div className="mt-0.5 text-sm text-muted">
        {booking.resource.name}
        {booking.additional_resources.length > 0 && ` + ${booking.additional_resources.map((r) => r.name).join(", ")}`}
        {booking.location && ` · ${booking.location.name}`}
      </div>
      {!compact && booking.quantity > 1 && <div className="mt-1 text-xs text-muted">Places: {booking.quantity}</div>}
    </div>
  );
}

export function DateTile({ iso, timeZone }: { iso: string; timeZone: string }) {
  const { day, month } = dayAndMonth(iso, timeZone);
  return (
    <div className="flex h-12 w-12 shrink-0 flex-col items-center justify-center rounded-md border border-line bg-canvas leading-none">
      <span className="text-[10px] font-medium uppercase tracking-wide text-muted">{month}</span>
      <span className="tabular mt-1 text-lg font-semibold text-fg">{day}</span>
    </div>
  );
}

/** A list of bookings; each row opens the booking. */
export function BookingRows({ children }: { children: React.ReactNode }) {
  return <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface">{children}</ul>;
}

export function BookingListItem({ booking, to }: { booking: Booking; to: string }) {
  return (
    <li>
      <Link to={to} className="flex items-center gap-3 px-3 py-3 hover:bg-subtle sm:gap-4 sm:px-4">
        <DateTile iso={booking.start_datetime} timeZone={booking.timezone} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
            <span className="font-medium text-fg">{booking.service.name}</span>
            <StatusBadge status={booking.status} />
            <WaitlistPosition booking={booking} />
          </div>
          <div className="tabular mt-0.5 text-sm text-fg">{formatDayTimes(booking.start_datetime, booking.end_datetime, booking.timezone)}</div>
          <div className="truncate text-sm text-muted">
            {booking.resource.name}
            {booking.location && ` · ${booking.location.name}`}
          </div>
        </div>
        {booking.price && <span className="tabular hidden text-sm text-muted sm:block">{formatMoney(booking.price)}</span>}
        <ChevronRight className="h-4 w-4 shrink-0 text-faint" />
      </Link>
    </li>
  );
}

export function timeLabel(iso: string, tz: string) {
  return formatTime(iso, tz);
}
