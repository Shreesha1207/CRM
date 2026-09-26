import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, errorMessage } from "../../api/client";
import type { AuditLog, Booking, Location, Page, Resource, Service, ServiceDetail, User } from "../../api/types";
import { BOOKING_STATUSES } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { BookingSummary, DateNav, SlotPicker, type PickedSlot } from "../../components/booking";
import { useConfig } from "../../components/Layout";
import { Alert, Button, Checkbox, Field, Input, Loading, Modal, PageHeader, Pagination, Select, StatusBadge, Table, Td, Textarea } from "../../components/ui";
import { CancelModal, RescheduleModal } from "../account";
import { formatDateTime, formatMoney, formatTime, sameDay, titleCase, todayIn } from "../../lib/format";

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
          <span className="text-muted"> · {h.actor_name ?? "system"} · {new Date(h.created_at).toLocaleString()}</span>
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
          {action.error && <Alert>{errorMessage(action.error)}</Alert>}
          {manage && (
            <div className="flex flex-wrap gap-2">
              {b.status === "PENDING" && <Button size="sm" onClick={() => action.mutate("confirm")}>Confirm</Button>}
              {b.status === "CONFIRMED" && started && (
                <>
                  <Button size="sm" onClick={() => action.mutate("complete")}>Mark completed</Button>
                  <Button size="sm" variant="secondary" onClick={() => action.mutate("no-show")}>Mark no-show</Button>
                </>
              )}
              {open && <Button size="sm" variant="secondary" onClick={() => setSub("reschedule")}>Reschedule</Button>}
              {open && <Button size="sm" variant="secondary" onClick={() => setSub("reassign")}>Reassign resource</Button>}
              {(open || b.status === "WAITLISTED") && <Button size="sm" variant="danger" onClick={() => setSub("cancel")}>Cancel</Button>}
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

function CreateBookingModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const config = useConfig();
  const { services } = useCatalog();
  const [userQuery, setUserQuery] = useState("");
  const [userId, setUserId] = useState("");
  const [serviceId, setServiceId] = useState("");
  const [resourceId, setResourceId] = useState("");
  const [date, setDate] = useState(todayIn(config.data?.default_timezone ?? "UTC"));
  const [picked, setPicked] = useState<PickedSlot | null>(null);
  const [override, setOverride] = useState(false);
  const [manualTime, setManualTime] = useState("09:00");
  const [notes, setNotes] = useState("");
  const [quantity, setQuantity] = useState(1);

  const users = useQuery({
    queryKey: ["admin", "users", userQuery],
    queryFn: () => api.get<Page<User>>("/api/admin/users", { q: userQuery, limit: 10 }),
  });
  const service = useQuery({
    queryKey: ["service", serviceId],
    queryFn: () => api.get<ServiceDetail>(`/api/services/${serviceId}`),
    enabled: !!serviceId,
  });
  const mutation = useMutation({
    mutationFn: () =>
      api.post<Booking>("/api/admin/bookings", {
        user_id: userId,
        service_id: serviceId,
        resource_id: resourceId || null,
        // With an override the admin may type any wall-clock time at the resource.
        start: override ? `${date}T${manualTime}` : picked!.start,
        quantity,
        notes: notes || null,
        override_rules: override,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries();
      onClose();
    },
  });

  return (
    <Modal
      open
      wide
      title="New booking"
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!userId || !serviceId || (!override && !picked)} loading={mutation.isPending} onClick={() => mutation.mutate()}>
            Create booking
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {mutation.error && <Alert>{errorMessage(mutation.error)}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Customer">
            <Input placeholder="Search name or e-mail…" value={userQuery} onChange={(e) => setUserQuery(e.target.value)} />
            <Select className="mt-2" value={userId} onChange={(e) => setUserId(e.target.value)}>
              <option value="">Choose…</option>
              {users.data?.items.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name} ({u.email})
                </option>
              ))}
            </Select>
          </Field>
          <div className="space-y-4">
            <Field label="Service">
              <Select value={serviceId} onChange={(e) => { setServiceId(e.target.value); setResourceId(""); setPicked(null); }}>
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
        {serviceId && (
          <>
            <DateNav date={date} onChange={(d) => { setDate(d); setPicked(null); }} />
            <Checkbox label="Override booking rules (outside hours, notice, blocks — audited; clashes are still prevented)" checked={override} onChange={(e) => setOverride(e.target.checked)} />
            {override ? (
              <Field label="Start time (resource's local time)">
                <Input type="time" value={manualTime} onChange={(e) => setManualTime(e.target.value)} className="w-40" />
              </Field>
            ) : (
              <SlotPicker serviceId={serviceId} resourceId={resourceId || null} date={date} quantity={quantity} selected={picked} onSelect={setPicked} />
            )}
            {override && !resourceId && <p className="text-xs text-warn-text">Choose a resource when overriding rules.</p>}
          </>
        )}
        <Field label="Notes">
          <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

const LIMIT = 25;

export function AdminBookingsPage() {
  const { resources, services, locations } = useCatalog();
  const { can } = useAuth();
  const [params] = useSearchParams();
  const [filters, setFilters] = useState({ status: "", resource_id: "", service_id: "", location_id: "", start_date: "", end_date: "", q: "" });
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Booking | null>(null);
  const [creating, setCreating] = useState(false);
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
                  <StatusBadge status={b.status} />
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
      {selected && <AdminBookingModal booking={selected} onClose={() => setSelected(null)} />}
      {creating && <CreateBookingModal onClose={() => setCreating(false)} />}
    </>
  );
}
