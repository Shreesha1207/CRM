import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, errorMessage } from "../../api/client";
import type { AuditLog, Booking, Invitation, Location, Page, Resource, Service, ServiceDetail, User } from "../../api/types";
import { BOOKING_STATUSES } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { BookingSummary, DateNav, SlotPicker, type PickedSlot } from "../../components/booking";
import { useConfig } from "../../components/Layout";
import { Alert, Button, Checkbox, Field, Input, Loading, Modal, PageHeader, Pagination, Select, StatusBadge, Table, Td, Textarea, TimeSelect, cx } from "../../components/ui";
import { CancelModal, RescheduleModal } from "../account";
import { formatDateTime, formatDuration, formatStamp, formatMoney, formatTime, lengthOptions, sameDay, titleCase, todayIn } from "../../lib/format";

export function useCatalog() {
  const resources = useQuery({ queryKey: ["admin", "resources"], queryFn: () => api.get<Resource[]>("/api/admin/resources") });
  const services = useQuery({ queryKey: ["admin", "services"], queryFn: () => api.get<Service[]>("/api/admin/services") });
  const locations = useQuery({ queryKey: ["admin", "locations"], queryFn: () => api.get<Location[]>("/api/admin/locations") });
  return { resources: resources.data ?? [], services: services.data ?? [], locations: locations.data ?? [] };
}

function History({ bookingId }: { bookingId: string }) {
  const history = useQuery({ queryKey: ["booking-history", bookingId], queryFn: () => api.get<AuditLog[]>(`/api/admin/bookings/${bookingId}/history`) });
  if (history.isLoading) return <Loading />;
  return (
    <ol className="space-y-2 border-l border-line pl-4 text-sm">
      {history.data?.map((h) => (
        <li key={h.id}>
          <span className="font-medium">{titleCase(h.action)}</span>
          <span className="text-muted"> · {h.actor_name ?? "system"} · {formatStamp(h.created_at)}</span>
          {h.new_value?.reason ? <div className="text-xs text-muted">{String(h.new_value.reason)}</div> : null}
        </li>
      ))}
    </ol>
  );
}

function ReassignModal({ booking, onClose }: { booking: Booking; onClose: () => void }) {
  const queryClient = useQueryClient();
  const service = useQuery({ queryKey: ["service", booking.service.id], queryFn: () => api.get<ServiceDetail>(`/api/services/${booking.service.id}`) });
  const [resourceId, setResourceId] = useState("");
  const [override, setOverride] = useState(false);
  const mutation = useMutation({
    mutationFn: () => api.post<Booking>(`/api/admin/bookings/${booking.id}/reassign`, { resource_id: resourceId, override_rules: override }),
    onSuccess: () => {
      queryClient.invalidateQueries();
      onClose();
    },
  });
  return (
    <Modal
      open
      title="Reassign to another resource"
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button disabled={!resourceId} loading={mutation.isPending} onClick={() => mutation.mutate()}>
            Reassign
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {mutation.error && <Alert>{errorMessage(mutation.error)}</Alert>}
        <p className="text-sm text-muted">Same time, different {booking.resource.type.toLowerCase()}. The new resource is checked for availability first.</p>
        <Field label="Resource">
          <Select value={resourceId} onChange={(e) => setResourceId(e.target.value)}>
            <option value="">Choose…</option>
            {service.data?.resources
              .filter((r) => r.resource_id !== booking.resource.id)
              .map((r) => (
                <option key={r.resource_id} value={r.resource_id}>
                  {r.resource_name}
                </option>
              ))}
          </Select>
        </Field>
        <Checkbox label="Override booking rules (audited)" checked={override} onChange={(e) => setOverride(e.target.checked)} />
      </div>
    </Modal>
  );
}

export function AdminBookingModal({ booking, onClose }: { booking: Booking; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { can } = useAuth();
  const [sub, setSub] = useState<"cancel" | "reschedule" | "reassign" | null>(null);
  const [notes, setNotes] = useState(booking.notes ?? "");
  const current = useQuery({
    queryKey: ["admin-booking", booking.id],
    queryFn: () => api.get<Booking>(`/api/admin/bookings/${booking.id}`),
    initialData: booking,
  });
  const b = current.data;
  const action = useMutation({
    mutationFn: (name: "confirm" | "complete" | "no-show") => api.post<Booking>(`/api/admin/bookings/${b.id}/${name}`),
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const save = useMutation({
    mutationFn: () => api.put<Booking>(`/api/admin/bookings/${b.id}`, { notes }),
    onSuccess: () => queryClient.invalidateQueries(),
  });
  // A booking made for someone who has not accepted yet: the admin can only send the link again.
  const resend = useMutation({
    mutationFn: () => api.post<Invitation>(`/api/admin/bookings/${b.id}/resend-invitation`),
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const manage = can("bookings:manage");
  const open = ["PENDING", "CONFIRMED", "CONFLICTED"].includes(b.status);
  const started = new Date(b.start_datetime) <= new Date();

  return (
    <>
      <Modal open={!sub} wide title="Booking" onClose={onClose}>
        <div className="space-y-5">
          <div className="flex flex-wrap justify-between gap-4">
            <BookingSummary booking={b} />
            <div className="text-right text-sm">
              <div className="font-medium">{b.user.name}</div>
              <div className="text-muted">{b.user.email}</div>
              <div className="mt-1 text-muted">{formatMoney(b.price)}</div>
            </div>
          </div>
          {b.conflict_reason && <Alert tone="warning">Conflict: {b.conflict_reason}</Alert>}
          {b.status === "COMPLETED" && b.completed_at && <p className="text-sm text-muted">Completed {formatDateTime(b.completed_at, b.timezone)}</p>}
          {action.error && <Alert>{errorMessage(action.error)}</Alert>}
          {b.awaiting_customer && (
            <Alert tone="info">
              Waiting for {b.user.email} to confirm. They were e-mailed a link; only they can confirm it, and if they decline it is cancelled. If you
              move it, they get a new link for the new time.
            </Alert>
          )}
          {b.held_in_chat && (
            <Alert tone="info">
              Held by the booking assistant for {b.user.email}. They confirm or delete it in the chat; if they don't answer in a few minutes, the
              time is released by itself. You can cancel it, but not confirm or move it.
            </Alert>
          )}
          {resend.error && <Alert>{errorMessage(resend.error)}</Alert>}
          {resend.isSuccess && <Alert tone="success">A new link was sent to {b.user.email}. The old one no longer works.</Alert>}
          {manage && (
            <div className="flex flex-wrap gap-2">
              {b.status === "PENDING" && !b.awaiting_customer && !b.held_in_chat && <Button size="sm" onClick={() => action.mutate("confirm")}>Confirm</Button>}
              {b.awaiting_customer && (
                <Button size="sm" variant="secondary" loading={resend.isPending} onClick={() => resend.mutate()}>
                  Resend invitation
                </Button>
              )}
              {b.status === "CONFIRMED" && started && (
                <>
                  <Button size="sm" onClick={() => action.mutate("complete")}>Mark completed</Button>
                  <Button size="sm" variant="secondary" onClick={() => action.mutate("no-show")}>Mark no-show</Button>
                </>
              )}
              {/* Past bookings complete by themselves, so either can be corrected. */}
              {b.status === "COMPLETED" && <Button size="sm" variant="secondary" onClick={() => action.mutate("no-show")}>Mark no-show instead</Button>}
              {b.status === "NO_SHOW" && <Button size="sm" variant="secondary" onClick={() => action.mutate("complete")}>Mark completed instead</Button>}
              {open && !b.held_in_chat && <Button size="sm" variant="secondary" onClick={() => setSub("reschedule")}>Reschedule</Button>}
              {open && !b.held_in_chat && <Button size="sm" variant="secondary" onClick={() => setSub("reassign")}>Reassign resource</Button>}
              {b.can_cancel && <Button size="sm" variant="danger" onClick={() => setSub("cancel")}>Cancel</Button>}
            </div>
          )}
          {manage && (
            <div className="space-y-2">
              <Field label="Internal notes">
                <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} />
              </Field>
              <Button size="sm" variant="secondary" loading={save.isPending} onClick={() => save.mutate()} disabled={notes === (b.notes ?? "")}>
                Save notes
              </Button>
            </div>
          )}
          <div>
            <h3 className="mb-2 text-sm font-semibold text-fg">History</h3>
            <History bookingId={b.id} />
          </div>
        </div>
      </Modal>
      {sub === "cancel" && <CancelModal booking={b} open admin onClose={() => setSub(null)} />}
      {sub === "reschedule" && <RescheduleModal booking={b} open admin onClose={() => { setSub(null); onClose(); }} />}
      {sub === "reassign" && <ReassignModal booking={b} onClose={() => setSub(null)} />}
    </>
  );
}

/** `again`: this e-mail was invited before but has not registered, so it is invited again, not booked directly. */
type Customer = { kind: "user"; user: User } | { kind: "invite"; email: string; again?: boolean };

const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * One box for choosing who a booking is for: type a name or e-mail to find a
 * customer, or type an e-mail nobody has registered to invite them instead.
 */
function CustomerPicker({ id, value, onChange }: { id?: string; value: Customer | null; onChange: (customer: Customer | null) => void }) {
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  // Search as they type, but not on every keystroke.
  useEffect(() => {
    const t = setTimeout(() => setQuery(text.trim()), 250);
    return () => clearTimeout(t);
  }, [text]);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const users = useQuery({
    queryKey: ["admin", "users", query],
    queryFn: () => api.get<Page<User>>("/api/admin/users", { q: query, limit: 8 }),
    enabled: open && !value,
    placeholderData: keepPreviousData,
  });
  const found = users.data?.items ?? [];
  const typed = text.trim().toLowerCase();
  // Only offer an invitation once the search for this exact text has come back without them.
  const settled = query === text.trim() && !users.isFetching;
  const inviteEmail = settled && EMAIL_SHAPE.test(typed) && !found.some((u) => u.email.toLowerCase() === typed) ? typed : null;
  const options: Customer[] = [
    ...found.map((user): Customer => (user.status === "INVITED" ? { kind: "invite", email: user.email, again: true } : { kind: "user", user })),
    ...(inviteEmail ? [{ kind: "invite", email: inviteEmail } as Customer] : []),
  ];

  const choose = (customer: Customer) => {
    onChange(customer);
    setOpen(false);
    setText("");
    setQuery("");
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => (options.length ? (a + (e.key === "ArrowDown" ? 1 : options.length - 1)) % options.length : 0));
    } else if (e.key === "Enter" && open && options[active]) {
      e.preventDefault();
      choose(options[active]);
    } else if (e.key === "Escape" && open) {
      e.stopPropagation(); // close the list, not the whole dialog
      setOpen(false);
    }
  };

  if (value) {
    return (
      <div className="flex items-center justify-between gap-2 rounded-md border border-line-strong bg-surface px-3 py-2 text-sm">
        <span className="min-w-0 truncate">
          {value.kind === "user" ? (
            <>
              <span className="font-medium text-fg">{value.user.name}</span> <span className="text-muted">({value.user.email})</span>
            </>
          ) : (
            <>
              <span className="font-medium text-fg">{value.email}</span> <span className="text-muted">· not registered yet, will be invited</span>
            </>
          )}
        </span>
        <button type="button" className="shrink-0 text-xs font-medium text-accent-text hover:underline" onClick={() => onChange(null)}>
          Change
        </button>
      </div>
    );
  }
  return (
    <div className="relative" ref={box}>
      <Input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && options[active] ? `${listId}-${active}` : undefined}
        autoComplete="off"
        placeholder="Search by name or e-mail, or type a new e-mail…"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {open && (
        <ul id={listId} role="listbox" className="absolute z-10 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-line bg-surface py-1 shadow-lg">
          {options.map((o, i) => (
            <li
              key={o.kind === "user" ? o.user.id : `invite-${o.email}`}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(o)}
              onMouseEnter={() => setActive(i)}
              className={cx("cursor-pointer px-3 py-2 text-sm", i === active && "bg-subtle")}
            >
              {o.kind === "user" ? (
                <>
                  <span className="font-medium text-fg">{o.user.name}</span> <span className="text-muted">{o.user.email}</span>
                </>
              ) : (
                <>
                  Invite <span className="font-medium text-fg">{o.email}</span>{" "}
                  <span className="text-muted">{o.again ? "again · invited before, not registered yet" : "to register"}</span>
                </>
              )}
            </li>
          ))}
          {options.length === 0 && (
            <li className="px-3 py-2 text-sm text-muted">
              {!settled ? "Searching…" : EMAIL_SHAPE.test(typed) ? "No match" : text.trim() ? "No customer found. Type a full e-mail to invite someone new." : "Type a name or e-mail."}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

function CreateBookingModal({ onClose, initial }: { onClose: () => void; initial?: { serviceId?: string; resourceId?: string } }) {
  const queryClient = useQueryClient();
  const config = useConfig();
  const { services } = useCatalog();
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [invitedEmail, setInvitedEmail] = useState<string | null>(null);
  const [bookedFor, setBookedFor] = useState<User | null>(null);
  const [serviceId, setServiceId] = useState(initial?.serviceId ?? "");
  const [resourceId, setResourceId] = useState(initial?.resourceId ?? "");
  const [date, setDate] = useState(todayIn(config.data?.default_timezone ?? "UTC"));
  const [picked, setPicked] = useState<PickedSlot | null>(null);
  const [override, setOverride] = useState(false);
  const [manualTime, setManualTime] = useState("09:00");
  const [notes, setNotes] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [duration, setDuration] = useState<number | null>(null); // null = the default length

  const service = useQuery({
    queryKey: ["service", serviceId],
    queryFn: () => api.get<ServiceDetail>(`/api/services/${serviceId}`),
    enabled: !!serviceId,
  });
  // Flexible services can be booked for longer, in steps of the resource's (or service's) length.
  const offering = service.data?.resources.find((r) => r.resource_id === resourceId);
  const baseLength = offering?.duration_minutes ?? service.data?.duration_minutes ?? 0;
  const lengths = baseLength ? lengthOptions(baseLength, offering?.max_duration_minutes ?? service.data?.max_duration_minutes) : [];
  const customLength = duration && duration !== baseLength && lengths.includes(duration) ? duration : undefined;
  const details = () => ({
    service_id: serviceId,
    resource_id: resourceId || null,
    // With an override the admin may type any wall-clock time at the resource.
    start: override ? `${date}T${manualTime}` : picked!.start,
    quantity,
    duration_minutes: customLength,
    notes: notes || null,
    override_rules: override,
  });
  const book = useMutation({
    mutationFn: (user: User) => api.post<Booking>("/api/admin/bookings", { ...details(), user_id: user.id }),
    onSuccess: (_booking, user) => {
      queryClient.invalidateQueries();
      setBookedFor(user);
    },
  });
  // Someone with no account yet: the booking goes on the calendar now, and they are
  // e-mailed a link to create their account and confirm it (or decline, which cancels it).
  const invite = useMutation({
    mutationFn: (email: string) => api.post<Invitation>("/api/admin/bookings/invite", { ...details(), customer_email: email }),
    onSuccess: (sent) => {
      queryClient.invalidateQueries();
      setInvitedEmail(sent.email);
    },
  });
  const mutation = customer?.kind === "invite" ? invite : book;
  const submit = () => (customer?.kind === "invite" ? invite.mutate(customer.email) : customer && book.mutate(customer.user));

  return (
    <Modal
      open
      wide
      title="New booking"
      onClose={onClose}
      footer={
        invitedEmail || bookedFor ? (
          <Button onClick={onClose}>Done</Button>
        ) : (
          <>
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button disabled={!customer || !serviceId || (!override && !picked)} loading={mutation.isPending} onClick={submit}>
              {customer?.kind === "invite" ? "Book and send invitation" : "Create booking"}
            </Button>
          </>
        )
      }
    >
      {bookedFor ? (
        <Alert tone="success">
          Booked for {bookedFor.name}. A confirmation e-mail is on its way to {bookedFor.email}.
        </Alert>
      ) : invitedEmail ? (
        <Alert tone="success">
          Booked for {invitedEmail}: it is on the calendar now, as pending. We e-mailed them a link to create an account and confirm it. If they
          decline, it is taken off the calendar and you will get a "Booking rejected" notice here.
        </Alert>
      ) : (
      <div className="space-y-4">
        {mutation.error && <Alert>{errorMessage(mutation.error)}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Field label="Customer">
              <CustomerPicker value={customer} onChange={setCustomer} />
            </Field>
            {customer?.kind === "invite" && (
              <Alert tone="info">
                No account uses this e-mail. The booking goes on the calendar now, and we e-mail them a link to create an account and confirm it. If
                they decline, it is removed.
              </Alert>
            )}
            {customer?.kind === "user" && (
              <Alert tone="info">They will be e-mailed a confirmation of this booking.</Alert>
            )}
          </div>
          <div className="space-y-4">
            <Field label="Service">
              <Select value={serviceId} onChange={(e) => { setServiceId(e.target.value); setResourceId(""); setPicked(null); setDuration(null); }}>
                <option value="">Choose…</option>
                {services.filter((s) => s.status === "ACTIVE").map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Resource">
              <Select value={resourceId} onChange={(e) => { setResourceId(e.target.value); setPicked(null); }} disabled={!serviceId}>
                <option value="">Any available</option>
                {service.data?.resources.map((r) => (
                  <option key={r.resource_id} value={r.resource_id}>
                    {r.resource_name}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        </div>
        {service.data?.booking_type === "CAPACITY" && (
          <Field label="Places">
            <Input type="number" min={1} value={quantity} onChange={(e) => setQuantity(Math.max(1, Number(e.target.value)))} className="w-24" />
          </Field>
        )}
        {lengths.length > 1 && (
          <Field label="Length">
            <Select
              value={customLength ?? baseLength}
              onChange={(e) => {
                setDuration(Number(e.target.value));
                setPicked(null);
              }}
              className="w-40"
            >
              {lengths.map((m) => (
                <option key={m} value={m}>
                  {formatDuration(m)}
                </option>
              ))}
            </Select>
          </Field>
        )}
        {serviceId && (
          <>
            <DateNav date={date} onChange={(d) => { setDate(d); setPicked(null); }} />
            <Checkbox label="Override booking rules (outside hours, notice, blocks, unlisted start times — audited; clashes are still prevented)" checked={override} onChange={(e) => setOverride(e.target.checked)} />
            {override ? (
              <Field label="Start time (resource's local time)">
                <TimeSelect step={5} value={manualTime} onChange={setManualTime} className="w-40" />
              </Field>
            ) : (
              <SlotPicker
                serviceId={serviceId}
                resourceId={resourceId || null}
                date={date}
                quantity={quantity}
                duration={customLength}
                selected={picked}
                onSelect={setPicked}
              />
            )}
            {override && !resourceId && <p className="text-xs text-warn-text">Choose a resource when overriding rules.</p>}
          </>
        )}
        <Field label="Notes">
          <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
      </div>
      )}
    </Modal>
  );
}

const LIMIT = 25;

export function AdminBookingsPage() {
  const { resources, services, locations } = useCatalog();
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const [filters, setFilters] = useState({ status: "", resource_id: "", service_id: "", location_id: "", start_date: "", end_date: "", q: "" });
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Booking | null>(null);
  // ?new=1 (optionally with service and resource) opens "New booking";
  // ?open=<id> opens one booking, e.g. from a notification.
  const [creating, setCreating] = useState(() => params.get("new") === "1");
  const openId = params.get("open");
  const opened = useQuery({
    queryKey: ["admin", "booking", openId],
    queryFn: () => api.get<Booking>(`/api/admin/bookings/${openId}`),
    enabled: !!openId,
  });
  const shown = selected ?? (openId ? opened.data : null);
  const dropParams = (...keys: string[]) =>
    setParams(
      (p) => {
        keys.forEach((k) => p.delete(k));
        return p;
      },
      { replace: true },
    );
  const bookings = useQuery({
    queryKey: ["admin", "bookings", filters, offset],
    queryFn: () => api.get<Page<Booking>>("/api/admin/bookings", { ...filters, status: filters.status ? [filters.status] : [], limit: LIMIT, offset }),
  });
  const set = (k: keyof typeof filters) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setFilters({ ...filters, [k]: e.target.value });
    setOffset(0);
  };
  const focus = params.get("focus");

  return (
    <>
      <PageHeader title="Bookings" actions={can("bookings:manage") && <Button onClick={() => setCreating(true)}>New booking</Button>} />
      <div className="mb-4 grid grid-cols-2 gap-2 lg:grid-cols-4">
        <Input placeholder="Customer name or e-mail" value={filters.q} onChange={set("q")} className="col-span-2 lg:col-span-1" />
        <Select value={filters.status} onChange={set("status")} aria-label="Status">
          <option value="">All statuses</option>
          {BOOKING_STATUSES.map((s) => (
            <option key={s} value={s}>
              {titleCase(s)}
            </option>
          ))}
        </Select>
        <Select value={filters.resource_id} onChange={set("resource_id")} aria-label="Resource">
          <option value="">All resources</option>
          {resources.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </Select>
        <Select value={filters.service_id} onChange={set("service_id")} aria-label="Service">
          <option value="">All services</option>
          {services.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </Select>
        <Select value={filters.location_id} onChange={set("location_id")} aria-label="Location">
          <option value="">All locations</option>
          {locations.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </Select>
        <Input type="date" value={filters.start_date} onChange={set("start_date")} aria-label="From" />
        <Input type="date" value={filters.end_date} onChange={set("end_date")} aria-label="To" />
      </div>
      {bookings.isLoading ? (
        <Loading />
      ) : bookings.error ? (
        <Alert>{errorMessage(bookings.error)}</Alert>
      ) : (
        <>
          <Table head={["When", "Customer", "Service", "Resource", "Status", ""]}>
            {bookings.data!.items.map((b) => (
              <tr key={b.id} className={focus === b.id ? "bg-accent-soft" : "hover:bg-subtle"}>
                <Td>
                  <div className="font-medium">{formatDateTime(b.start_datetime, b.timezone)}</div>
                  <div className="text-xs text-muted">
                    until {sameDay(b.start_datetime, b.end_datetime, b.timezone) ? formatTime(b.end_datetime, b.timezone) : formatDateTime(b.end_datetime, b.timezone)}
                  </div>
                </Td>
                <Td>
                  <div>{b.user.name}</div>
                  <div className="text-xs text-muted">{b.user.email}</div>
                </Td>
                <Td>
                  {b.service.name}
                  {b.quantity > 1 && <span className="text-xs text-muted"> ×{b.quantity}</span>}
                </Td>
                <Td>{b.resource.name}</Td>
                <Td>
                  <StatusBadge status={b.awaiting_customer || b.held_in_chat ? "AWAITING_CUSTOMER" : b.status} />
                </Td>
                <Td className="text-right">
                  <Button size="sm" variant="secondary" onClick={() => setSelected(b)}>
                    Open
                  </Button>
                </Td>
              </tr>
            ))}
            {bookings.data!.items.length === 0 && (
              <tr>
                <Td colSpan={6} className="py-8 text-center text-muted">
                  No bookings match these filters.
                </Td>
              </tr>
            )}
          </Table>
          <Pagination total={bookings.data!.total} limit={LIMIT} offset={offset} onChange={setOffset} />
        </>
      )}
      {shown && (
        <AdminBookingModal
          booking={shown}
          onClose={() => {
            setSelected(null);
            dropParams("open");
          }}
        />
      )}
      {opened.error && <Alert>{errorMessage(opened.error)}</Alert>}
      {creating && can("bookings:manage") && (
        <CreateBookingModal
          initial={{ serviceId: params.get("service") ?? undefined, resourceId: params.get("resource") ?? undefined }}
          onClose={() => {
            setCreating(false);
            dropParams("new", "service", "resource");
          }}
        />
      )}
    </>
  );
}
