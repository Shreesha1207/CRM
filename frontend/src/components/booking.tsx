import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Alternative, Availability, Booking, Slot } from "../api/types";
import { addDays, formatDateTime, formatMoney, formatRange, formatTime, longDate, titleCase } from "../lib/format";
import { Alert, Button, Card, Input, Loading, StatusBadge, cx } from "./ui";

export interface PickedSlot {
  start: string;
  end: string;
  resourceId: string | null; // null = let the system pick any free resource
  timezone: string;
  slot?: Slot;
}

export function DateNav({ date, onChange, min }: { date: string; onChange: (d: string) => void; min?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="secondary" size="sm" disabled={!!min && date <= min} onClick={() => onChange(addDays(date, -1))} aria-label="Previous day">
        ←
      </Button>
      <Input type="date" value={date} min={min} onChange={(e) => e.target.value && onChange(e.target.value)} className="w-auto" />
      <Button variant="secondary" size="sm" onClick={() => onChange(addDays(date, 1))} aria-label="Next day">
        →
      </Button>
      <span className="text-sm text-slate-500">{longDate(date)}</span>
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
        "rounded-lg px-3 py-2 text-sm font-medium ring-1 transition",
        selected && "bg-brand-600 text-white ring-brand-600",
        !selected && available && "bg-white text-slate-800 ring-slate-300 hover:ring-brand-500",
        !available && "cursor-not-allowed bg-slate-50 text-slate-400 line-through ring-slate-200",
      )}
    >
      {label}
      {sub && <span className={cx("block text-[11px] font-normal", selected ? "text-white/80" : "text-slate-500")}>{sub}</span>}
    </button>
  );
}

/** Shows slots for one resource, or -- with resourceId null -- for every
 * resource that offers the service (the "time first" flow). */
export function SlotPicker({
  serviceId,
  resourceId,
  date,
  quantity = 1,
  selected,
  onSelect,
  showUnavailable = true,
}: {
  serviceId: string;
  resourceId: string | null;
  date: string;
  quantity?: number;
  selected: PickedSlot | null;
  onSelect: (slot: PickedSlot) => void;
  showUnavailable?: boolean;
}) {
  const availability = useQuery({
    queryKey: ["availability", serviceId, resourceId, date, quantity],
    queryFn: () =>
      api.get<Availability>("/api/availability", { service_id: serviceId, resource_id: resourceId, date, quantity }),
  });

  if (availability.isLoading) return <Loading label="Checking availability…" />;
  if (availability.error) return <Alert>{(availability.error as Error).message}</Alert>;
  const data = availability.data!;

  if (resourceId) {
    const resource = data.resources[0];
    const slots = (resource?.slots ?? []).filter((s) => showUnavailable || s.available);
    if (!slots.length) return <p className="py-6 text-sm text-slate-500">No times on this date. Try another day.</p>;
    return (
      <div>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-6">
          {slots.map((s) => (
            <SlotButton
              key={s.start}
              label={s.start_time}
              sub={s.capacity != null ? (s.available ? `${s.remaining} left` : titleCase(s.status)) : s.available ? undefined : titleCase(s.status)}
              available={s.available || s.status === "CAPACITY_REACHED"}
              title={s.message}
              selected={selected?.start === s.start && selected.resourceId === resource.resource_id}
              onClick={() => onSelect({ start: s.start, end: s.end, resourceId: resource.resource_id, timezone: resource.timezone, slot: s })}
            />
          ))}
        </div>
        <p className="mt-3 text-xs text-slate-500">Times shown in {resource.timezone}.</p>
      </div>
    );
  }

  const slots = data.slots.filter((s) => showUnavailable || s.available);
  if (!slots.length) return <p className="py-6 text-sm text-slate-500">No times on this date. Try another day.</p>;
  const tz = data.resources[0]?.timezone ?? "UTC";
  return (
    <div>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-6">
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
      <p className="mt-3 text-xs text-slate-500">Times shown in {tz}.</p>
    </div>
  );
}

/** Which resources are free at a chosen time (time-first flow). */
export function useFreeResources(serviceId: string, date: string, start: string | null, quantity = 1) {
  const availability = useQuery({
    queryKey: ["availability", serviceId, null, date, quantity],
    queryFn: () => api.get<Availability>("/api/availability", { service_id: serviceId, date, quantity }),
    enabled: !!start,
  });
  return (availability.data?.resources ?? []).filter((r) => r.slots.some((s) => s.start === start && s.available));
}

export function Alternatives({
  serviceId,
  resourceId,
  start,
  quantity = 1,
  onPick,
}: {
  serviceId: string;
  resourceId: string;
  start: string;
  quantity?: number;
  onPick: (alt: Alternative) => void;
}) {
  const alternatives = useQuery({
    queryKey: ["alternatives", serviceId, resourceId, start, quantity],
    queryFn: () => api.get<Alternative[]>("/api/availability/alternatives", { service_id: serviceId, resource_id: resourceId, start, quantity }),
  });
  if (alternatives.isLoading) return <Loading label="Looking for alternatives…" />;
  if (!alternatives.data?.length) return <p className="text-sm text-slate-500">No nearby alternatives found.</p>;
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium text-slate-700">Suggested alternatives</p>
      <div className="flex flex-wrap gap-2">
        {alternatives.data.map((a) => (
          <button
            key={`${a.resource_id}-${a.start}`}
            onClick={() => onPick(a)}
            className="rounded-lg bg-white px-3 py-2 text-left text-sm ring-1 ring-slate-300 hover:ring-brand-500"
          >
            <span className="block font-medium">{a.resource_name}</span>
            <span className="text-xs text-slate-500">{formatDateTime(a.start, a.timezone)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function BookingSummary({ booking, compact }: { booking: Booking; compact?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-slate-900">{booking.service.name}</span>
        <StatusBadge status={booking.status} />
        {booking.waitlist_position != null && <span className="text-xs text-violet-700">#{booking.waitlist_position} on waitlist</span>}
      </div>
      <div className="mt-1 text-sm text-slate-600">
        {booking.resource.name}
        {booking.additional_resources.length > 0 && ` + ${booking.additional_resources.map((r) => r.name).join(", ")}`}
        {booking.location && ` · ${booking.location.name}`}
      </div>
      <div className="mt-1 text-sm text-slate-800">{formatRange(booking.start_datetime, booking.end_datetime, booking.timezone)}</div>
      {!compact && booking.quantity > 1 && <div className="mt-1 text-xs text-slate-500">Quantity: {booking.quantity}</div>}
    </div>
  );
}

export function BookingListItem({ booking, to }: { booking: Booking; to: string }) {
  return (
    <Card className="flex flex-wrap items-center justify-between gap-4">
      <BookingSummary booking={booking} compact />
      <div className="flex items-center gap-3">
        {booking.price && <span className="text-sm text-slate-500">{formatMoney(booking.price)}</span>}
        <Link to={to} className="rounded-lg px-3 py-1.5 text-sm font-medium text-brand-700 ring-1 ring-brand-200 hover:bg-brand-50">
          View
        </Link>
      </div>
    </Card>
  );
}

export function timeLabel(iso: string, tz: string) {
  return formatTime(iso, tz);
}
