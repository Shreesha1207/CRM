import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, api, errorMessage } from "../api/client";
import type { Booking, RecurringPreview, Service, ServiceDetail } from "../api/types";
import { Alternatives, DateNav, SlotPicker, useFreeResources, type PickedSlot } from "../components/booking";
import { useConfig } from "../components/Layout";
import { Alert, Badge, Button, Card, Checkbox, Field, Input, Loading, PageHeader, Select, Textarea, cx } from "../components/ui";
import { formatDateTime, formatDuration, formatMoney, formatRange, titleCase, todayIn } from "../lib/format";

type Mode = "resource" | "time";

function Step({ n, title, children, done }: { n: number; title: string; children: React.ReactNode; done?: boolean }) {
  return (
    <Card>
      <div className="mb-4 flex items-center gap-3">
        <span
          className={cx(
            "flex h-7 w-7 items-center justify-center rounded-full text-sm font-semibold",
            done ? "bg-emerald-100 text-emerald-700" : "bg-brand-100 text-brand-700",
          )}
        >
          {done ? "✓" : n}
        </span>
        <h2 className="font-semibold">{title}</h2>
      </div>
      {children}
    </Card>
  );
}

export function BookPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const config = useConfig();
  const today = todayIn(config.data?.default_timezone ?? "UTC");

  const serviceId = params.get("service") ?? "";
  const [mode, setModeState] = useState<Mode>("resource");
  const [resourceId, setResourceIdState] = useState<string>(params.get("resource") ?? "");
  const [date, setDateState] = useState<string>(params.get("date") ?? today);
  const [picked, setPicked] = useState<PickedSlot | null>(null);
  const [chosenResource, setChosenResource] = useState<string>(""); // time-first flow: "" = any
  const [quantity, setQuantityState] = useState(1);
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
  const setMode = (m: Mode) => { setModeState(m); resetSlot(); };
  const setResourceId = (id: string) => { setResourceIdState(id); resetSlot(); };
  const setDate = (d: string) => { setDateState(d); resetSlot(); };
  const setQuantity = (q: number) => { setQuantityState(q); resetSlot(); };

  const selectService = (id: string) => {
    setParams(id ? { service: id } : {});
    setResourceId("");
  };

  const isGroup = service.data?.booking_type === "CAPACITY";
  const effectiveResource = mode === "resource" ? resourceId : picked?.resourceId ?? chosenResource;
  const freeResources = useFreeResources(serviceId, date, mode === "time" ? picked?.start ?? null : null, quantity);
  const offering = service.data?.resources.find((r) => r.resource_id === (effectiveResource || freeResources[0]?.resource_id));
  const slotFull = picked?.slot?.status === "CAPACITY_REACHED";

  const bookingBody = () => ({
    service_id: serviceId,
    resource_id: effectiveResource || null,
    start: picked!.start,
    quantity,
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
  const total = useMemo(() => {
    if (!offering?.price) return null;
    return isGroup ? Number(offering.price) * quantity : Number(offering.price);
  }, [offering, quantity, isGroup]);

  return (
    <>
      <PageHeader title="Make a booking" subtitle="Availability is checked live and confirmed again when you book." />
      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-5">
          <Step n={1} title="Choose a service" done={!!serviceId}>
            {services.isLoading ? (
              <Loading />
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {services.data?.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => selectService(s.id)}
                    className={cx(
                      "rounded-lg p-3 text-left ring-1 transition",
                      s.id === serviceId ? "bg-brand-50 ring-2 ring-brand-500" : "bg-white ring-slate-200 hover:ring-brand-300",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{s.name}</span>
                      {s.booking_type === "CAPACITY" && <Badge tone="WAITLISTED">Group</Badge>}
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500">
                      {formatDuration(s.duration_minutes)} · {formatMoney(s.price)}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </Step>

          {serviceId && service.data && (
            <Step n={2} title="Choose how to book" done={mode === "time" || !!resourceId}>
              <div className="mb-4 inline-flex rounded-lg bg-slate-100 p-1 text-sm">
                {(["resource", "time"] as Mode[]).map((m) => (
                  <button
                    key={m}
                    onClick={() => setMode(m)}
                    className={cx("rounded-md px-3 py-1.5 font-medium", mode === m ? "bg-white shadow-sm" : "text-slate-500")}
                  >
                    {m === "resource" ? "Pick who / what first" : "Pick a time first"}
                  </button>
                ))}
              </div>
              {mode === "resource" ? (
                <div className="grid gap-2 sm:grid-cols-2">
                  {service.data.resources.map((r) => (
                    <button
                      key={r.resource_id}
                      onClick={() => setResourceId(r.resource_id)}
                      className={cx(
                        "rounded-lg p-3 text-left ring-1",
                        r.resource_id === resourceId ? "bg-brand-50 ring-2 ring-brand-500" : "bg-white ring-slate-200 hover:ring-brand-300",
                      )}
                    >
                      <div className="font-medium">{r.resource_name}</div>
                      <div className="text-xs text-slate-500">
                        {titleCase(r.resource_type)}
                        {r.location_name && ` · ${r.location_name}`} · {formatMoney(r.price)}
                      </div>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-slate-500">You'll see every free time across {service.data.resources.length} resources, then choose.</p>
              )}
            </Step>
          )}

          {serviceId && (mode === "time" || resourceId) && (
            <Step n={3} title="Pick a date and time" done={!!picked}>
              <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                <DateNav date={date} onChange={setDate} min={today} />
                {isGroup && (
                  <Field label="Places">
                    <Input type="number" min={1} max={service.data?.capacity ?? 100} value={quantity} onChange={(e) => setQuantity(Math.max(1, Number(e.target.value)))} className="w-24" />
                  </Field>
                )}
              </div>
              <SlotPicker
                serviceId={serviceId}
                resourceId={mode === "resource" ? resourceId : null}
                date={date}
                quantity={quantity}
                selected={picked}
                onSelect={(s) => {
                  setPicked(s);
                  setChosenResource("");
                  create.reset();
                }}
              />
              {mode === "time" && picked && (
                <div className="mt-5 border-t border-slate-100 pt-4">
                  <p className="mb-2 text-sm font-medium">Free at {picked.slot?.start_time ?? formatDateTime(picked.start, picked.timezone)}:</p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      onClick={() => setChosenResource("")}
                      className={cx("rounded-lg px-3 py-1.5 text-sm ring-1", !chosenResource ? "bg-brand-600 text-white ring-brand-600" : "ring-slate-300")}
                    >
                      Any available
                    </button>
                    {freeResources.map((r) => (
                      <button
                        key={r.resource_id}
                        onClick={() => setChosenResource(r.resource_id)}
                        className={cx(
                          "rounded-lg px-3 py-1.5 text-sm ring-1",
                          chosenResource === r.resource_id ? "bg-brand-600 text-white ring-brand-600" : "ring-slate-300",
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
                <Field label="Notes (optional)">
                  <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} />
                </Field>
                {config.data?.allow_recurring_bookings && effectiveResource && !slotFull && (
                  <div className="rounded-lg bg-slate-50 p-4">
                    <Checkbox label="Repeat this booking" checked={repeat} onChange={(e) => { setRepeat(e.target.checked); setPreview(null); }} />
                    {repeat && (
                      <div className="mt-3 grid gap-3 sm:grid-cols-3">
                        <Field label="Frequency">
                          <Select value={frequency} onChange={(e) => { setFrequency(e.target.value); setPreview(null); }}>
                            <option value="DAILY">Daily</option>
                            <option value="WEEKLY">Weekly</option>
                            <option value="MONTHLY">Monthly</option>
                          </Select>
                        </Field>
                        <Field label="Every">
                          <Input type="number" min={1} max={52} value={interval} onChange={(e) => { setInterval(Math.max(1, Number(e.target.value))); setPreview(null); }} />
                        </Field>
                        <Field label="Occurrences">
                          <Input type="number" min={2} max={52} value={count} onChange={(e) => { setCount(Math.max(1, Number(e.target.value))); setPreview(null); }} />
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
                  <div className="rounded-lg ring-1 ring-slate-200">
                    <div className="border-b border-slate-100 px-4 py-2 text-sm font-medium">
                      {preview.available_count} available · {preview.conflict_count} unavailable
                    </div>
                    <ul className="max-h-64 divide-y divide-slate-50 overflow-y-auto text-sm">
                      {preview.occurrences.map((o) => (
                        <li key={o.start} className="flex justify-between gap-3 px-4 py-2">
                          <span className={o.available ? "" : "text-slate-400 line-through"}>{formatRange(o.start, o.end, picked.timezone)}</span>
                          <span className={o.available ? "text-emerald-600" : "text-red-600"}>{o.available ? "Available" : o.message}</span>
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
                    <Button
                      onClick={() => create.mutate()}
                      loading={create.isPending}
                      disabled={slotFull && !config.data?.allow_waitlist}
                    >
                      {slotFull ? "Join waitlist" : "Confirm booking"}
                    </Button>
                  )}
                </div>
              </div>
            </Step>
          )}
        </div>

        <aside className="lg:sticky lg:top-20 lg:self-start">
          <Card>
            <h2 className="mb-3 font-semibold">Summary</h2>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-slate-500">Service</dt>
                <dd className="text-right font-medium">{service.data?.name ?? "—"}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-slate-500">With</dt>
                <dd className="text-right font-medium">
                  {offering && effectiveResource ? offering.resource_name : picked ? "Any available" : "—"}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-slate-500">When</dt>
                <dd className="text-right font-medium">{picked ? formatRange(picked.start, picked.end, picked.timezone) : "—"}</dd>
              </div>
              {isGroup && (
                <div className="flex justify-between gap-3">
                  <dt className="text-slate-500">Places</dt>
                  <dd className="font-medium">{quantity}</dd>
                </div>
              )}
              <div className="flex justify-between gap-3 border-t border-slate-100 pt-2">
                <dt className="text-slate-500">Price</dt>
                <dd className="font-semibold">{total != null ? formatMoney(String(total)) : "—"}</dd>
              </div>
            </dl>
            {config.data?.require_admin_confirmation && (
              <p className="mt-4 text-xs text-slate-500">Bookings are confirmed by our team; you'll be notified.</p>
            )}
          </Card>
        </aside>
      </div>
    </>
  );
}
