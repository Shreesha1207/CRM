import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, api, errorMessage } from "../api/client";
import type { Booking, RecurringPreview, Service, ServiceDetail } from "../api/types";
import { Alternatives, DateNav, SlotPicker, useFreeResources, type PickedSlot } from "../components/booking";
import { useConfig } from "../components/Layout";
import { Check } from "../components/icons";
import { Alert, Badge, Button, Checkbox, Field, Input, Loading, PageHeader, Select, Textarea, cx } from "../components/ui";
import {
  bookingPrice,
  formatDateTime,
  formatDuration,
  formatLengthRange,
  formatMoney,
  formatRange,
  formatRate,
  lengthOptions,
  titleCase,
  todayIn,
} from "../lib/format";

type Mode = "resource" | "time";

const MODES: { value: Mode; label: string; hint: string }[] = [
  { value: "resource", label: "Pick who or what first", hint: "Choose the person, room or item you want, then see when it's free." },
  { value: "time", label: "Pick a time first", hint: "See all free times, then pick from what's available at that time." },
];

function Step({ n, title, children, done }: { n: number; title: string; children: React.ReactNode; done?: boolean }) {
  return (
    <section className="rounded-lg border border-line bg-surface">
      <header className="flex items-center gap-3 border-b border-line px-4 py-3 sm:px-5">
        <span
          className={cx(
            "tabular flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
            done ? "bg-accent text-accent-fg" : "border border-line-strong text-muted",
          )}
        >
          {done ? <Check className="h-3.5 w-3.5" /> : n}
        </span>
        <h2 className="text-sm font-semibold text-fg">{title}</h2>
      </header>
      <div className="p-4 sm:p-5">{children}</div>
    </section>
  );
}

/** A selectable option (a service, a resource). */
function Choice({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cx(
        "w-full rounded-md border px-3 py-2.5 text-left transition-colors",
        selected ? "border-accent bg-accent-soft" : "border-line-strong bg-surface hover:border-accent",
      )}
    >
      {children}
    </button>
  );
}

/** How long to book, in steps of the service's own length. */
function LengthSlider({ options, value, onChange }: { options: number[]; value: number; onChange: (minutes: number) => void }) {
  const id = useId();
  const shortest = options[0];
  const longest = options[options.length - 1];
  return (
    <div className="w-full sm:max-w-sm">
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-sm font-medium text-fg">
          Length
        </label>
        <span className="tabular text-sm font-semibold text-fg">{formatDuration(value)}</span>
      </div>
      <input
        id={id}
        type="range"
        min={shortest}
        max={longest}
        step={shortest}
        value={value}
        list={`${id}-marks`}
        aria-valuetext={formatDuration(value)}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-6 w-full"
      />
      <datalist id={`${id}-marks`}>
        {options.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <div className="tabular flex justify-between text-xs text-muted">
        <span>{formatDuration(shortest)}</span>
        <span>{formatDuration(longest)}</span>
      </div>
    </div>
  );
}

function SummaryList({ rows, total, breakdown }: { rows: [string, React.ReactNode][]; total: string | null; breakdown?: string }) {
  return (
    <dl className="space-y-2 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-4">
          <dt className="text-muted">{label}</dt>
          <dd className="text-right font-medium text-fg">{value}</dd>
        </div>
      ))}
      <div className="flex justify-between gap-4 border-t border-line pt-2">
        <dt className="text-muted">Price</dt>
        <dd className="text-right">
          <span className="tabular font-semibold text-fg">{total ?? "—"}</span>
          {total && breakdown && <span className="tabular block text-xs text-muted">{breakdown}</span>}
        </dd>
      </div>
    </dl>
  );
}

export function BookPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const config = useConfig();
  const today = todayIn(config.data?.default_timezone ?? "UTC");

  const serviceId = params.get("service") ?? "";
  // No way of booking is assumed, unless the link already names a resource.
  const [mode, setModeState] = useState<Mode | null>(params.get("resource") ? "resource" : null);
  const [resourceId, setResourceIdState] = useState<string>(params.get("resource") ?? "");
  const [date, setDateState] = useState<string>(params.get("date") ?? today);
  const [picked, setPicked] = useState<PickedSlot | null>(null);
  const [chosenResource, setChosenResource] = useState<string>(""); // time-first flow: "" = any
  const [quantity, setQuantityState] = useState(1);
  const [duration, setDurationState] = useState<number | null>(null); // null = the default length
  const [notes, setNotes] = useState("");
  const [repeat, setRepeat] = useState(false);
  const [frequency, setFrequency] = useState("WEEKLY");
  const [interval, setInterval] = useState(1);
  const [count, setCount] = useState(4);
  const [preview, setPreview] = useState<RecurringPreview | null>(null);

  const services = useQuery({ queryKey: ["services"], queryFn: () => api.get<Service[]>("/api/services") });
  const service = useQuery({
    queryKey: ["service", serviceId],
    queryFn: () => api.get<ServiceDetail>(`/api/services/${serviceId}`),
    enabled: !!serviceId,
  });

  // Any change upstream invalidates the chosen slot.
  const resetSlot = () => {
    setPicked(null);
    setPreview(null);
  };
  const setMode = (m: Mode) => {
    setModeState(m);
    resetSlot();
  };
  const setResourceId = (id: string) => {
    setResourceIdState(id);
    resetSlot();
  };
  const setDate = (d: string) => {
    setDateState(d);
    resetSlot();
  };
  const setQuantity = (q: number) => {
    setQuantityState(q);
    resetSlot();
  };
  const setDuration = (minutes: number) => {
    setDurationState(minutes);
    resetSlot();
  };

  // Choosing a service starts the booking over; "Change service" goes back to the list.
  const selectService = (id: string) => {
    setParams(id ? { service: id } : {});
    setResourceId("");
    setDurationState(null);
    setQuantityState(1);
    setModeState(null);
  };

  const isGroup = service.data?.booking_type === "CAPACITY";
  // Bookable lengths: the chosen resource's terms, or the service's when the
  // time comes first (resources that can't do a length are then left out).
  const chosenOffering = mode === "resource" ? service.data?.resources.find((r) => r.resource_id === resourceId) : undefined;
  const baseLength = chosenOffering?.duration_minutes ?? service.data?.duration_minutes ?? 0;
  const lengths = baseLength ? lengthOptions(baseLength, chosenOffering?.max_duration_minutes ?? service.data?.max_duration_minutes) : [];
  const length = duration && lengths.includes(duration) ? duration : baseLength;
  // Only a length the customer picked is sent; otherwise each resource uses its own.
  const customLength = length !== baseLength ? length : undefined;

  const effectiveResource = mode === "resource" ? resourceId : picked?.resourceId ?? chosenResource;
  const freeResources = useFreeResources(serviceId, date, mode === "time" ? picked?.start ?? null : null, quantity, customLength);
  const offering = service.data?.resources.find((r) => r.resource_id === (effectiveResource || freeResources[0]?.resource_id));
  const slotFull = picked?.slot?.status === "CAPACITY_REACHED";
  const waitlistClosed = slotFull && !config.data?.allow_waitlist;

  const bookingBody = () => ({
    service_id: serviceId,
    resource_id: effectiveResource || null,
    start: picked!.start,
    quantity,
    duration_minutes: customLength,
    notes: notes || null,
    join_waitlist: slotFull,
  });

  const create = useMutation({
    mutationFn: () => api.post<Booking>("/api/bookings", bookingBody()),
    onSuccess: (booking) => {
      queryClient.invalidateQueries();
      navigate(`/bookings/${booking.id}`, { state: { created: true } });
    },
  });

  const recurringBody = (skip: boolean) => ({
    ...bookingBody(),
    frequency,
    interval,
    count,
    skip_conflicts: skip,
  });
  const previewRecurring = useMutation({
    mutationFn: () => api.post<RecurringPreview>("/api/bookings/recurring/preview", recurringBody(true)),
    onSuccess: setPreview,
  });
  const createRecurring = useMutation({
    mutationFn: () => api.post<{ created: Booking[] }>("/api/bookings/recurring", recurringBody(true)),
    onSuccess: () => {
      queryClient.invalidateQueries();
      navigate("/bookings", { state: { created: true } });
    },
  });

  const conflictError = create.error instanceof ApiError && create.error.status === 409 ? create.error : null;
  const bookedMinutes = customLength ?? offering?.duration_minutes ?? length;
  const places = isGroup ? quantity : 1;
  const total = offering ? bookingPrice(offering.price, bookedMinutes, places) : null;

  const summary: [string, React.ReactNode][] = [
    ["Service", service.data?.name ?? "—"],
    ["With", offering && effectiveResource ? offering.resource_name : picked ? "Any available" : "—"],
    ["When", picked ? formatRange(picked.start, picked.end, picked.timezone) : "—"],
    ...(service.data ? [["Length", formatDuration(bookedMinutes)] as [string, React.ReactNode]] : []),
    ...(isGroup ? [["Places", quantity] as [string, React.ReactNode]] : []),
  ];
  const summaryTotal = total != null ? formatMoney(total) : null;
  const breakdown = offering?.price
    ? `${formatRate(offering.price)} × ${formatDuration(bookedMinutes)}${places > 1 ? ` × ${places} places` : ""}`
    : undefined;
  const confirmationNote = config.data?.require_admin_confirmation && (
    <p className="mt-4 text-xs text-muted">Bookings are confirmed by our team; you'll be notified.</p>
  );

  return (
    <>
      <PageHeader title="Make a booking" subtitle="Availability is checked live and confirmed again when you book." />
      <div className="grid gap-6 lg:grid-cols-[1fr_300px] lg:gap-8">
        <div className="min-w-0 space-y-4">
          <Step n={1} title="Choose a service" done={!!serviceId}>
            {serviceId ? (
              // Once chosen, the service is locked: the other services are
              // not selectable until the customer asks to change it.
              service.error ? (
                <div className="space-y-3">
                  <Alert>{errorMessage(service.error)}</Alert>
                  <Button variant="secondary" size="sm" onClick={() => selectService("")}>
                    Choose another service
                  </Button>
                </div>
              ) : !service.data ? (
                <Loading />
              ) : (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-fg">{service.data.name}</span>
                      {isGroup && <Badge>Group</Badge>}
                    </div>
                    <div className="tabular mt-0.5 text-xs text-muted">
                      {formatLengthRange(service.data.duration_minutes, service.data.max_duration_minutes)}
                      {service.data.price && ` · ${formatRate(service.data.price)}`}
                    </div>
                  </div>
                  <Button variant="secondary" size="sm" onClick={() => selectService("")}>
                    Change service
                  </Button>
                </div>
              )
            ) : services.isLoading ? (
              <Loading />
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {services.data?.map((s) => (
                  <Choice key={s.id} selected={false} onClick={() => selectService(s.id)}>
                    <span className="flex items-center justify-between gap-2">
                      <span className="font-medium text-fg">{s.name}</span>
                      {s.booking_type === "CAPACITY" && <Badge>Group</Badge>}
                    </span>
                    <span className="tabular mt-0.5 block text-xs text-muted">
                      {formatLengthRange(s.duration_minutes, s.max_duration_minutes)}
                      {s.price && ` · ${formatRate(s.price)}`}
                    </span>
                  </Choice>
                ))}
              </div>
            )}
          </Step>

          {serviceId && service.data && (
            <Step n={2} title="Choose how to book" done={mode === "time" || (mode === "resource" && !!resourceId)}>
              <fieldset>
                <legend className="sr-only">How would you like to book?</legend>
                <div className="grid gap-2 sm:grid-cols-2">
                  {MODES.map((m) => (
                    <label
                      key={m.value}
                      className={cx(
                        "flex cursor-pointer gap-3 rounded-md border px-3 py-3 transition-colors",
                        mode === m.value ? "border-accent bg-accent-soft" : "border-line-strong bg-surface hover:border-accent",
                      )}
                    >
                      <input
                        type="radio"
                        name="how-to-book"
                        value={m.value}
                        checked={mode === m.value}
                        onChange={() => setMode(m.value)}
                        className="mt-0.5 h-4 w-4 shrink-0"
                      />
                      <span>
                        <span className="block text-sm font-medium text-fg">{m.label}</span>
                        <span className="mt-0.5 block text-xs text-muted">{m.hint}</span>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
              {mode === "resource" && (
                <div className="mt-4 grid gap-2 border-t border-line pt-4 sm:grid-cols-2">
                  {service.data.resources.map((r) => (
                    <Choice key={r.resource_id} selected={r.resource_id === resourceId} onClick={() => setResourceId(r.resource_id)}>
                      <span className="block font-medium text-fg">{r.resource_name}</span>
                      <span className="block text-xs text-muted">
                        {titleCase(r.resource_type)}
                        {r.location_name && ` · ${r.location_name}`}
                        {r.price && ` · ${formatRate(r.price)}`}
                      </span>
                    </Choice>
                  ))}
                </div>
              )}
            </Step>
          )}

          {serviceId && (mode === "time" || (mode === "resource" && resourceId)) && (
            <Step n={3} title="Pick a date and time" done={!!picked}>
              {lengths.length > 1 && (
                <div className="mb-5 border-b border-line pb-5">
                  <LengthSlider options={lengths} value={length} onChange={setDuration} />
                </div>
              )}
              <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
                <DateNav date={date} onChange={setDate} min={today} />
                {isGroup && (
                  <Field label="Places">
                    <Input
                      type="number"
                      min={1}
                      max={service.data?.capacity ?? 100}
                      value={quantity}
                      onChange={(e) => setQuantity(Math.max(1, Number(e.target.value)))}
                      className="w-24"
                    />
                  </Field>
                )}
              </div>
              <SlotPicker
                serviceId={serviceId}
                resourceId={mode === "resource" ? resourceId : null}
                date={date}
                quantity={quantity}
                duration={customLength}
                selected={picked}
                onSelect={(s) => {
                  setPicked(s);
                  setChosenResource("");
                  create.reset();
                }}
              />
              {mode === "time" && picked && (
                <div className="mt-5 border-t border-line pt-4">
                  <p className="mb-2 text-sm font-medium text-fg">Free at {picked.slot?.start_time ?? formatDateTime(picked.start, picked.timezone)}:</p>
                  <div className="flex flex-wrap gap-2">
                    {[{ resource_id: "", resource_name: "Any available" }, ...freeResources].map((r) => (
                      <button
                        key={r.resource_id}
                        onClick={() => setChosenResource(r.resource_id)}
                        aria-pressed={chosenResource === r.resource_id}
                        className={cx(
                          "rounded-md border px-3 py-1.5 text-sm",
                          chosenResource === r.resource_id ? "border-accent bg-accent text-accent-fg" : "border-line-strong text-fg hover:border-accent",
                        )}
                      >
                        {r.resource_name}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </Step>
          )}

          {picked && (
            <Step n={4} title="Confirm">
              <div className="space-y-4">
                {slotFull && (
                  <Alert tone="warning">
                    This session is full.{" "}
                    {config.data?.allow_waitlist ? "You can join the waitlist and we'll confirm you automatically if a place opens." : "Please choose another time."}
                  </Alert>
                )}
                <div className="rounded-md bg-subtle p-4 lg:hidden">
                  <SummaryList rows={summary} total={summaryTotal} breakdown={breakdown} />
                </div>
                <Field label="Notes (optional)">
                  <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} />
                </Field>
                {config.data?.allow_recurring_bookings && effectiveResource && !slotFull && (
                  <div className="rounded-md border border-line p-4">
                    <Checkbox
                      label="Repeat this booking"
                      checked={repeat}
                      onChange={(e) => {
                        setRepeat(e.target.checked);
                        setPreview(null);
                      }}
                    />
                    {repeat && (
                      <div className="mt-3 grid gap-3 sm:grid-cols-3">
                        <Field label="Frequency">
                          <Select
                            value={frequency}
                            onChange={(e) => {
                              setFrequency(e.target.value);
                              setPreview(null);
                            }}
                          >
                            <option value="DAILY">Daily</option>
                            <option value="WEEKLY">Weekly</option>
                            <option value="MONTHLY">Monthly</option>
                          </Select>
                        </Field>
                        <Field label="Every">
                          <Input
                            type="number"
                            min={1}
                            max={52}
                            value={interval}
                            onChange={(e) => {
                              setInterval(Math.max(1, Number(e.target.value)));
                              setPreview(null);
                            }}
                          />
                        </Field>
                        <Field label="Occurrences">
                          <Input
                            type="number"
                            min={2}
                            max={52}
                            value={count}
                            onChange={(e) => {
                              setCount(Math.max(1, Number(e.target.value)));
                              setPreview(null);
                            }}
                          />
                        </Field>
                      </div>
                    )}
                  </div>
                )}

                {create.error && (
                  <div className="space-y-3">
                    <Alert>{errorMessage(create.error)}</Alert>
                    {conflictError && effectiveResource && (
                      <Alternatives
                        serviceId={serviceId}
                        resourceId={effectiveResource}
                        start={picked.start}
                        quantity={quantity}
                        duration={customLength}
                        onPick={(a) => {
                          setModeState("resource");
                          setResourceIdState(a.resource_id);
                          setDateState(a.start.slice(0, 10));
                          setPreview(null);
                          setPicked({ start: a.start, end: a.end, resourceId: a.resource_id, timezone: a.timezone });
                          create.reset();
                        }}
                      />
                    )}
                  </div>
                )}

                {preview && (
                  <div className="overflow-hidden rounded-md border border-line">
                    <div className="border-b border-line bg-subtle px-4 py-2 text-sm font-medium text-fg">
                      {preview.available_count} available · {preview.conflict_count} unavailable
                    </div>
                    <ul className="max-h-64 divide-y divide-line overflow-y-auto text-sm">
                      {preview.occurrences.map((o) => (
                        <li key={o.start} className="flex flex-wrap justify-between gap-x-3 gap-y-0.5 px-4 py-2">
                          <span className={cx("tabular", o.available ? "text-fg" : "text-faint line-through")}>{formatRange(o.start, o.end, picked.timezone)}</span>
                          <span className={o.available ? "text-ok-text" : "text-danger-text"}>{o.available ? "Available" : o.message}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {previewRecurring.error && <Alert>{errorMessage(previewRecurring.error)}</Alert>}
                {createRecurring.error && <Alert>{errorMessage(createRecurring.error)}</Alert>}

                <div className="flex flex-wrap gap-2">
                  {repeat && effectiveResource ? (
                    preview ? (
                      <Button onClick={() => createRecurring.mutate()} loading={createRecurring.isPending} disabled={!preview.available_count}>
                        Book {preview.available_count} available date{preview.available_count === 1 ? "" : "s"}
                      </Button>
                    ) : (
                      <Button onClick={() => previewRecurring.mutate()} loading={previewRecurring.isPending}>
                        Check dates
                      </Button>
                    )
                  ) : (
                    !waitlistClosed && (
                      <Button onClick={() => create.mutate()} loading={create.isPending}>
                        {slotFull ? "Join waitlist" : "Confirm booking"}
                      </Button>
                    )
                  )}
                </div>
                <div className="lg:hidden">{confirmationNote}</div>
              </div>
            </Step>
          )}
        </div>

        <aside className="hidden lg:sticky lg:top-24 lg:block lg:self-start">
          <div className="rounded-lg border border-line bg-surface p-5">
            <h2 className="mb-3 text-sm font-semibold text-fg">Summary</h2>
            <SummaryList rows={summary} total={summaryTotal} breakdown={breakdown} />
            {confirmationNote}
          </div>
        </aside>
      </div>
    </>
  );
}
