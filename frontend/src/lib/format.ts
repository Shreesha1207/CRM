/** Date helpers. All display conversions go through Intl with an explicit
 * timeZone -- never the browser's local zone -- so a booking at a location in
 * Asia/Kolkata reads the same for everyone. */

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const WEEKDAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function formatDateTime(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function formatDate(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(iso));
}

export function formatTime(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat(undefined, { timeZone, hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
}

/** An end that falls on a later day (a booking past midnight) gets its own date. */
export function formatRange(startIso: string, endIso: string, timeZone: string): string {
  const end = sameDay(startIso, endIso, timeZone)
    ? formatTime(endIso, timeZone)
    : `${formatDate(endIso, timeZone)} · ${formatTime(endIso, timeZone)}`;
  return `${formatDate(startIso, timeZone)} · ${formatTime(startIso, timeZone)} – ${end}`;
}

/** "Sat · 10:00 – 11:00", for rows that show the date separately. An end on a
 * later day still gets its date. */
export function formatDayTimes(startIso: string, endIso: string, timeZone: string): string {
  const weekday = new Intl.DateTimeFormat(undefined, { timeZone, weekday: "short" }).format(new Date(startIso));
  const end = sameDay(startIso, endIso, timeZone)
    ? formatTime(endIso, timeZone)
    : `${formatDate(endIso, timeZone)} · ${formatTime(endIso, timeZone)}`;
  return `${weekday} · ${formatTime(startIso, timeZone)} – ${end}`;
}

/** Day of month and short month name, for date tiles. */
export function dayAndMonth(iso: string, timeZone: string): { day: string; month: string } {
  const parts = new Intl.DateTimeFormat(undefined, { timeZone, day: "numeric", month: "short" }).formatToParts(new Date(iso));
  return {
    day: parts.find((p) => p.type === "day")?.value ?? "",
    month: parts.find((p) => p.type === "month")?.value ?? "",
  };
}

export function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value));
}

/** An hourly rate: "40.00 / h". */
export function formatRate(value: string | null | undefined): string {
  return value === null || value === undefined ? "—" : `${formatMoney(value)} / h`;
}

function toCents(value: string): number {
  const [whole, fraction = ""] = value.split(".");
  return Number(whole) * 100 + Number(`${fraction}00`.slice(0, 2));
}

/** What a booking costs: the hourly rate times the booked hours (times the
 * places for a group session), rounded half up to the cent -- the same sum
 * the server makes. Returned as a decimal string for formatMoney. */
export function bookingPrice(rate: string | null | undefined, minutes: number, places = 1): string | null {
  if (rate === null || rate === undefined) return null;
  return (Math.round((toCents(rate) * minutes * places) / 60) / 100).toFixed(2);
}

/** The lengths a customer may book: multiples of `step` up to `max`. */
export function lengthOptions(step: number, max: number | null | undefined): number[] {
  const count = Math.max(1, Math.floor((max ?? step) / step));
  return Array.from({ length: count }, (_, i) => step * (i + 1));
}

/** "30 min – 2 h", or one length when it is fixed. */
export function formatLengthRange(step: number, max: number | null | undefined): string {
  const options = lengthOptions(step, max);
  const longest = options[options.length - 1];
  return longest > step ? `${formatDuration(step)} – ${formatDuration(longest)}` : formatDuration(step);
}

export function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

export interface ZonedParts {
  date: string; // YYYY-MM-DD in the zone
  hour: number;
  minute: number;
}

const partsCache = new Map<string, Intl.DateTimeFormat>();

export function zonedParts(iso: string | Date, timeZone: string): ZonedParts {
  let fmt = partsCache.get(timeZone);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    partsCache.set(timeZone, fmt);
  }
  const parts = Object.fromEntries(fmt.formatToParts(typeof iso === "string" ? new Date(iso) : iso).map((p) => [p.type, p.value]));
  return { date: `${parts.year}-${parts.month}-${parts.day}`, hour: Number(parts.hour), minute: Number(parts.minute) };
}

export function sameDay(aIso: string, bIso: string, timeZone: string): boolean {
  return zonedParts(aIso, timeZone).date === zonedParts(bIso, timeZone).date;
}

/** Today's calendar date in a zone, as YYYY-MM-DD. */
export function todayIn(timeZone: string): string {
  return zonedParts(new Date(), timeZone).date;
}

// Plain calendar-date arithmetic on YYYY-MM-DD strings (no timezone involved).
export function addDays(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export function weekdayIndex(date: string): number {
  return (new Date(`${date}T00:00:00Z`).getUTCDay() + 6) % 7; // Monday = 0
}

export function startOfWeek(date: string): string {
  return addDays(date, -weekdayIndex(date));
}

export function startOfMonth(date: string): string {
  return `${date.slice(0, 8)}01`;
}

export function daysInMonth(date: string): number {
  const [y, m] = date.split("-").map(Number);
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

export function longDate(date: string): string {
  return new Intl.DateTimeFormat(undefined, { timeZone: "UTC", weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(
    new Date(`${date}T00:00:00Z`),
  );
}

export function monthLabel(date: string): string {
  return new Intl.DateTimeFormat(undefined, { timeZone: "UTC", month: "long", year: "numeric" }).format(new Date(`${date}T00:00:00Z`));
}

export function shortDay(date: string): string {
  return new Intl.DateTimeFormat(undefined, { timeZone: "UTC", weekday: "short", day: "numeric", month: "short" }).format(
    new Date(`${date}T00:00:00Z`),
  );
}

export function titleCase(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** "HH:MM:SS" -> "HH:MM" */
export function hhmm(time: string): string {
  return time.slice(0, 5);
}
