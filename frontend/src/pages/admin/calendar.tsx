import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, errorMessage } from "../../api/client";
import type { AvailabilityException, Booking, CalendarData, Resource } from "../../api/types";
import { BOOKING_STATUSES } from "../../api/types";
import { useConfig } from "../../components/Layout";
import { Alert, Button, Loading, PageHeader, Select, cx } from "../../components/ui";
import {
  WEEKDAYS_SHORT,
  addDays,
  daysInMonth,
  formatTime,
  longDate,
  monthLabel,
  shortDay,
  startOfMonth,
  startOfWeek,
  titleCase,
  todayIn,
  weekdayIndex,
  zonedParts,
} from "../../lib/format";
import { AdminBookingModal, useCatalog } from "./bookings";

type View = "day" | "week" | "month";
const HOUR_PX = 48;
const PALETTE = [
  "bg-sky-100 border-sky-400 text-sky-900",
  "bg-emerald-100 border-emerald-400 text-emerald-900",
  "bg-violet-100 border-violet-400 text-violet-900",
  "bg-amber-100 border-amber-400 text-amber-900",
  "bg-rose-100 border-rose-400 text-rose-900",
  "bg-teal-100 border-teal-400 text-teal-900",
  "bg-indigo-100 border-indigo-400 text-indigo-900",
];

function statusStyle(b: Booking, colorIndex: number): string {
  if (b.status === "CONFLICTED") return "bg-red-100 border-red-500 text-red-900";
  if (b.status === "PENDING") return "bg-amber-50 border-amber-400 border-dashed text-amber-900";
  if (b.status === "CANCELLED" || b.status === "NO_SHOW") return "bg-slate-100 border-slate-300 text-slate-500 line-through";
  return PALETTE[colorIndex % PALETTE.length];
}

interface Placed<T> {
  item: T;
  top: number;
  height: number;
  lane: number;
  lanes: number;
}

/** Position items on one day's timeline, side by side where they overlap. */
function layout<T>(items: { item: T; start: number; end: number }[], fromHour: number): Placed<T>[] {
  const sorted = [...items].sort((a, b) => a.start - b.start || b.end - a.end);
  const placed: (Placed<T> & { start: number; end: number })[] = [];
  let cluster: typeof placed = [];
  let clusterEnd = -1;
  const flush = () => {
    const lanes = Math.max(1, ...cluster.map((p) => p.lane + 1));
    cluster.forEach((p) => (p.lanes = lanes));
    cluster = [];
  };
  for (const it of sorted) {
    if (it.start >= clusterEnd) flush();
    const used = new Set(cluster.filter((p) => p.end > it.start).map((p) => p.lane));
    let lane = 0;
    while (used.has(lane)) lane++;
    const p = { item: it.item, start: it.start, end: it.end, lane, lanes: 1, top: ((it.start - fromHour * 60) / 60) * HOUR_PX, height: Math.max(18, ((it.end - it.start) / 60) * HOUR_PX) };
    placed.push(p);
    cluster.push(p);
    clusterEnd = Math.max(clusterEnd, it.end);
  }
  flush();
  return placed;
}

/** Minutes since local midnight of `day`, clipped to that day. */
function minutesOn(day: string, startIso: string, endIso: string, tz: string): { start: number; end: number } | null {
  const s = zonedParts(startIso, tz);
  const e = zonedParts(endIso, tz);
  if (s.date > day || e.date < day || (e.date === day && e.hour * 60 + e.minute === 0 && s.date < day)) return null;
  const start = s.date < day ? 0 : s.hour * 60 + s.minute;
  const end = e.date > day ? 24 * 60 : e.hour * 60 + e.minute;
  return end > start ? { start, end } : null;
}

function blockApplies(block: AvailabilityException, resource: Resource): boolean {
  if (block.resource_id) return block.resource_id === resource.id;
  if (block.location_id) return block.location_id === resource.location_id;
  return true;
}

function Timeline({
  days,
  columns,
  data,
  tz,
  fromHour,
  toHour,
  colorOf,
  onOpen,
}: {
  days: string[];
  columns: { key: string; label: string; day: string; resource?: Resource }[];
  data: CalendarData;
  tz: string;
  fromHour: number;
  toHour: number;
  colorOf: (b: Booking) => number;
  onOpen: (b: Booking) => void;
}) {
  const hours = Array.from({ length: toHour - fromHour }, (_, i) => fromHour + i);
  const nowParts = zonedParts(new Date(), tz);
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
      <div className="grid min-w-[640px]" style={{ gridTemplateColumns: `56px repeat(${columns.length}, minmax(120px, 1fr))` }}>
        <div className="sticky top-0 z-10 border-b border-slate-200 bg-white" />
        {columns.map((c) => (
          <div key={c.key} className={cx("sticky top-0 z-10 border-b border-l border-slate-200 bg-white px-2 py-2 text-center text-xs font-semibold", c.day === nowParts.date && !c.resource && "text-brand-700")}>
            {c.label}
          </div>
        ))}
        <div className="relative" style={{ height: hours.length * HOUR_PX }}>
          {hours.map((h, i) => (
            <div key={h} className={cx("absolute right-2 text-[11px] text-slate-400", i > 0 && "-translate-y-2")} style={{ top: i * HOUR_PX }}>
              {String(h).padStart(2, "0")}:00
            </div>
          ))}
        </div>
        {columns.map((c) => {
          const bookings = data.bookings.filter((b) => (c.resource ? b.resource.id === c.resource.id : true));
          const placedBookings = layout(
            bookings.flatMap((b) => {
              const m = minutesOn(c.day, b.start_datetime, b.end_datetime, tz);
              return m ? [{ item: b, ...m }] : [];
            }),
            fromHour,
          );
          const blocks = data.blocks
            .filter((bl) => bl.type !== "SPECIAL_HOURS" && (!c.resource || blockApplies(bl, c.resource)))
            .flatMap((bl) => {
              const m = minutesOn(c.day, bl.start_datetime, bl.end_datetime, tz);
              return m ? [{ bl, ...m }] : [];
            });
          return (
            <div key={c.key} className="relative border-l border-slate-200" style={{ height: hours.length * HOUR_PX }}>
              {hours.map((_, i) => (
                <div key={i} className="absolute inset-x-0 border-t border-slate-100" style={{ top: i * HOUR_PX }} />
              ))}
              {blocks.map(({ bl, start, end }) => (
                <div
                  key={bl.id}
                  title={`${titleCase(bl.type)}${bl.reason ? `: ${bl.reason}` : ""}`}
                  className="absolute inset-x-0 px-1 text-[10px] font-medium uppercase text-slate-500"
                  style={{
                    top: ((start - fromHour * 60) / 60) * HOUR_PX,
                    height: ((end - start) / 60) * HOUR_PX,
                    background: "repeating-linear-gradient(135deg, #f1f5f9 0 6px, #e2e8f0 6px 12px)",
                  }}
                >
                  {titleCase(bl.type)}
                </div>
              ))}
              {c.day === nowParts.date && nowParts.hour >= fromHour && nowParts.hour < toHour && (
                <div className="absolute inset-x-0 z-10 border-t-2 border-red-400" style={{ top: ((nowParts.hour * 60 + nowParts.minute - fromHour * 60) / 60) * HOUR_PX }} />
              )}
              {placedBookings.map(({ item: b, top, height, lane, lanes }) => (
                <button
                  key={b.id}
                  onClick={() => onOpen(b)}
                  className={cx("absolute overflow-hidden rounded-md border-l-4 px-1.5 py-0.5 text-left text-[11px] leading-tight shadow-sm hover:z-20 hover:shadow", statusStyle(b, colorOf(b)))}
                  style={{ top, height, left: `calc(${(lane / lanes) * 100}% + 2px)`, width: `calc(${100 / lanes}% - 4px)` }}
                >
                  <span className="font-semibold">{formatTime(b.start_datetime, tz)}</span> {b.service.name}
                  <span className="block truncate">{c.resource ? b.user.name : `${b.resource.name} · ${b.user.name}`}</span>
                  {b.service.booking_type === "CAPACITY" && <span className="block">×{b.quantity}</span>}
                </button>
              ))}
            </div>
          );
        })}
      </div>
      {days.length === 0 && <p className="p-4 text-sm text-slate-500">Nothing to show.</p>}
    </div>
  );
}

function MonthGrid({ month, data, tz, colorOf, onOpen, onDay }: {
  month: string;
  data: CalendarData;
  tz: string;
  colorOf: (b: Booking) => number;
  onOpen: (b: Booking) => void;
  onDay: (day: string) => void;
}) {
  const first = startOfMonth(month);
  const gridStart = startOfWeek(first);
  const cells = Array.from({ length: 42 }, (_, i) => addDays(gridStart, i));
  const today = todayIn(tz);
  const byDay = useMemo(() => {
    const map = new Map<string, Booking[]>();
    for (const b of data.bookings) {
      const key = zonedParts(b.start_datetime, tz).date;
      map.set(key, [...(map.get(key) ?? []), b]);
    }
    return map;
  }, [data, tz]);
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className="grid grid-cols-7 border-b border-slate-200 bg-slate-50 text-center text-xs font-semibold text-slate-500">
        {WEEKDAYS_SHORT.map((d) => (
          <div key={d} className="py-2">
            {d}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {cells.map((day) => {
          const items = (byDay.get(day) ?? []).sort((a, b) => a.start_datetime.localeCompare(b.start_datetime));
          const inMonth = day.slice(0, 7) === first.slice(0, 7);
          return (
            <div key={day} className={cx("min-h-28 border-b border-r border-slate-100 p-1.5", !inMonth && "bg-slate-50/60")}>
              <button onClick={() => onDay(day)} className={cx("mb-1 rounded px-1.5 text-xs font-medium hover:bg-slate-100", day === today ? "bg-brand-600 text-white hover:bg-brand-700" : inMonth ? "text-slate-700" : "text-slate-400")}>
                {Number(day.slice(8))}
              </button>
              <div className="space-y-0.5">
                {items.slice(0, 3).map((b) => (
                  <button key={b.id} onClick={() => onOpen(b)} className={cx("block w-full truncate rounded border-l-2 px-1 text-left text-[11px]", statusStyle(b, colorOf(b)))}>
                    {formatTime(b.start_datetime, tz)} {b.service.name}
                  </button>
                ))}
                {items.length > 3 && (
                  <button onClick={() => onDay(day)} className="px-1 text-[11px] text-slate-500 hover:underline">
                    +{items.length - 3} more
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function AdminCalendarPage() {
  const config = useConfig();
  const tz = config.data?.default_timezone ?? "UTC";
  const { resources, services, locations } = useCatalog();
  const [view, setView] = useState<View>("week");
  const [anchor, setAnchor] = useState(todayIn(tz));
  const [filters, setFilters] = useState({ resource_id: "", service_id: "", location_id: "", status: "" });
  const [selected, setSelected] = useState<Booking | null>(null);

  const range = useMemo(() => {
    if (view === "day") return { start: anchor, end: anchor };
    if (view === "week") return { start: startOfWeek(anchor), end: addDays(startOfWeek(anchor), 6) };
    const first = startOfMonth(anchor);
    const gridStart = startOfWeek(first);
    return { start: gridStart, end: addDays(gridStart, 41) };
  }, [view, anchor]);

  const calendar = useQuery({
    queryKey: ["admin", "calendar", range, filters],
    queryFn: () =>
      api.get<CalendarData>("/api/admin/calendar", {
        ...range,
        resource_id: filters.resource_id,
        service_id: filters.service_id,
        location_id: filters.location_id,
        status: filters.status ? [filters.status] : [],
      }),
  });

  const colorIndex = useMemo(() => new Map(resources.map((r, i) => [r.id, i])), [resources]);
  const colorOf = (b: Booking) => colorIndex.get(b.resource.id) ?? 0;

  const step = (dir: number) => {
    if (view === "day") setAnchor(addDays(anchor, dir));
    else if (view === "week") setAnchor(addDays(anchor, 7 * dir));
    else {
      const first = startOfMonth(anchor);
      setAnchor(dir > 0 ? addDays(first, daysInMonth(first)) : startOfMonth(addDays(first, -1)));
    }
  };

  const title =
    view === "day" ? longDate(anchor) : view === "week" ? `${shortDay(range.start)} – ${shortDay(range.end)}` : monthLabel(anchor);

  const data = calendar.data;
  // Hours shown: 06–22 by default, widened to fit anything outside that range.
  const { fromHour, toHour } = useMemo(() => {
    let from = 6;
    let to = 22;
    for (const b of data?.bookings ?? []) {
      const s = zonedParts(b.start_datetime, tz);
      const e = zonedParts(b.end_datetime, tz);
      from = Math.min(from, s.hour);
      to = Math.max(to, e.date > s.date ? 24 : e.hour + (e.minute ? 1 : 0));
    }
    return { fromHour: from, toHour: Math.min(24, to) };
  }, [data, tz]);

  const shownResources = (data?.resources ?? []).slice(0, 12);

  return (
    <>
      <PageHeader title="Calendar" subtitle={`Times in ${tz}. Hatched areas are blocked.`} />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-lg bg-slate-100 p-1 text-sm">
          {(["day", "week", "month"] as View[]).map((v) => (
            <button key={v} onClick={() => setView(v)} className={cx("rounded-md px-3 py-1 font-medium capitalize", view === v ? "bg-white shadow-sm" : "text-slate-500")}>
              {v}
            </button>
          ))}
        </div>
        <Button variant="secondary" size="sm" onClick={() => step(-1)} aria-label="Previous">
          ←
        </Button>
        <Button variant="secondary" size="sm" onClick={() => setAnchor(todayIn(tz))}>
          Today
        </Button>
        <Button variant="secondary" size="sm" onClick={() => step(1)} aria-label="Next">
          →
        </Button>
        <span className="ml-2 font-semibold">{title}</span>
      </div>
      <div className="mb-4 grid gap-2 sm:grid-cols-4">
        <Select value={filters.resource_id} onChange={(e) => setFilters({ ...filters, resource_id: e.target.value })} aria-label="Resource">
          <option value="">All resources</option>
          {resources.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </Select>
        <Select value={filters.service_id} onChange={(e) => setFilters({ ...filters, service_id: e.target.value })} aria-label="Service">
          <option value="">All services</option>
          {services.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </Select>
        <Select value={filters.location_id} onChange={(e) => setFilters({ ...filters, location_id: e.target.value })} aria-label="Location">
          <option value="">All locations</option>
          {locations.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </Select>
        <Select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} aria-label="Status">
          <option value="">Active bookings</option>
          {BOOKING_STATUSES.map((s) => (
            <option key={s} value={s}>
              {titleCase(s)}
            </option>
          ))}
        </Select>
      </div>

      {calendar.isLoading ? (
        <Loading />
      ) : calendar.error ? (
        <Alert>{errorMessage(calendar.error)}</Alert>
      ) : view === "month" ? (
        <MonthGrid month={anchor} data={data!} tz={tz} colorOf={colorOf} onOpen={setSelected} onDay={(d) => { setAnchor(d); setView("day"); }} />
      ) : view === "day" ? (
        <Timeline
          days={[anchor]}
          columns={shownResources.map((r) => ({ key: r.id, label: r.name, day: anchor, resource: r }))}
          data={data!}
          tz={tz}
          fromHour={fromHour}
          toHour={toHour}
          colorOf={colorOf}
          onOpen={setSelected}
        />
      ) : (
        <Timeline
          days={Array.from({ length: 7 }, (_, i) => addDays(range.start, i))}
          columns={Array.from({ length: 7 }, (_, i) => {
            const day = addDays(range.start, i);
            return { key: day, label: `${WEEKDAYS_SHORT[weekdayIndex(day)]} ${Number(day.slice(8))}`, day };
          })}
          data={{
            ...data!,
            // In week view a block only shades the column when it concerns the filtered resource.
            blocks: filters.resource_id ? data!.blocks.filter((bl) => shownResources.some((r) => blockApplies(bl, r))) : [],
          }}
          tz={tz}
          fromHour={fromHour}
          toHour={toHour}
          colorOf={colorOf}
          onOpen={setSelected}
        />
      )}
      {view === "day" && (data?.resources.length ?? 0) > shownResources.length && (
        <p className="mt-2 text-xs text-slate-500">Showing the first {shownResources.length} resources — filter to see others.</p>
      )}
      {selected && <AdminBookingModal booking={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
