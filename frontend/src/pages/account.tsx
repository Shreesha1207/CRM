import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { Booking, Page } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { BookingListItem, BookingSummary, DateNav, SlotPicker, type PickedSlot } from "../components/booking";
import { useConfig } from "../components/Layout";
import { Alert, Button, Card, EmptyState, Field, Input, Loading, Modal, PageHeader, Pagination, Tabs, Textarea } from "../components/ui";
import { formatDateTime, formatDuration, formatMoney, formatRange, todayIn, zonedParts } from "../lib/format";

type Scope = "upcoming" | "past" | "cancelled" | "waitlisted" | "all";

function useBookings(scope: Scope, limit = 20, offset = 0) {
  return useQuery({
    queryKey: ["my-bookings", scope, limit, offset],
    queryFn: () => api.get<Page<Booking>>("/api/bookings", { scope, limit, offset }),
  });
}

function BookingList({ scope, empty }: { scope: Scope; empty: string }) {
  const [offset, setOffset] = useState(0);
  const bookings = useBookings(scope, 20, offset);
  if (bookings.isLoading) return <Loading />;
  if (bookings.error) return <Alert>{errorMessage(bookings.error)}</Alert>;
  if (!bookings.data?.items.length) return <EmptyState title={empty} />;
  return (
    <div className="space-y-3">
      {bookings.data.items.map((b) => (
        <BookingListItem key={b.id} booking={b} to={`/bookings/${b.id}`} />
      ))}
      <Pagination total={bookings.data.total} limit={20} offset={offset} onChange={setOffset} />
    </div>
  );
}

export function DashboardPage() {
  const { user } = useAuth();
  const upcoming = useBookings("upcoming", 3);
  const waitlisted = useBookings("waitlisted", 5);
  const [tab, setTab] = useState<Scope>("upcoming");
  const next = upcoming.data?.items[0];
  return (
    <>
      <PageHeader
        title={`Hello, ${user?.name.split(" ")[0]}`}
        subtitle="Here's what's coming up."
        actions={<Link to="/book" className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">New booking</Link>}
      />
      <div className="mb-8 grid gap-4 md:grid-cols-3">
        <Card className="md:col-span-2">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Next booking</p>
          {upcoming.isLoading ? (
            <Loading />
          ) : next ? (
            <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
              <BookingSummary booking={next} />
              <div className="flex gap-2">
                <Link to={`/bookings/${next.id}`} className="rounded-lg px-3 py-1.5 text-sm font-medium text-brand-700 ring-1 ring-brand-200 hover:bg-brand-50">
                  View
                </Link>
              </div>
            </div>
          ) : (
            <p className="mt-3 text-sm text-slate-500">No upcoming bookings. <Link to="/book" className="text-brand-700 hover:underline">Book something</Link>.</p>
          )}
        </Card>
        <Card>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">At a glance</p>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-500">Upcoming</dt>
              <dd className="font-semibold">{upcoming.data?.total ?? "–"}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">On a waitlist</dt>
              <dd className="font-semibold">{waitlisted.data?.total ?? "–"}</dd>
            </div>
          </dl>
        </Card>
      </div>
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "upcoming", label: "Upcoming" },
          { value: "past", label: "Past" },
          { value: "cancelled", label: "Cancelled" },
          { value: "waitlisted", label: "Waitlisted" },
        ]}
      />
      <BookingList scope={tab} empty={`No ${tab} bookings.`} />
    </>
  );
}

export function BookingsPage() {
  const location = useLocation();
  const [tab, setTab] = useState<Scope>("upcoming");
  return (
    <>
      <PageHeader title="My bookings" actions={<Link to="/book" className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">New booking</Link>} />
      {(location.state as { created?: boolean } | null)?.created && <div className="mb-4"><Alert tone="success">Your bookings were created.</Alert></div>}
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "upcoming", label: "Upcoming" },
          { value: "past", label: "Past" },
          { value: "cancelled", label: "Cancelled" },
          { value: "waitlisted", label: "Waitlisted" },
          { value: "all", label: "All" },
        ]}
      />
      <BookingList scope={tab} empty="Nothing here yet." />
    </>
  );
}

export function RescheduleModal({
  booking,
  open,
  onClose,
  admin,
}: {
  booking: Booking;
  open: boolean;
  onClose: () => void;
  admin?: boolean;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const config = useConfig();
  const [date, setDate] = useState(zonedParts(booking.start_datetime, booking.timezone).date);
  const [picked, setPicked] = useState<PickedSlot | null>(null);
  const [override, setOverride] = useState(false);
  const mutation = useMutation({
    mutationFn: () =>
      api.post<Booking>(`/api/${admin ? "admin/" : ""}bookings/${booking.id}/reschedule`, {
        start: picked!.start,
        ...(admin ? { override_rules: override } : {}),
      }),
    onSuccess: (b) => {
      queryClient.invalidateQueries();
      onClose();
      navigate(admin ? `/admin/bookings?focus=${b.id}` : `/bookings/${b.id}`, { state: { rescheduled: true } });
    },
  });
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Reschedule booking"
      wide
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Keep current time
          </Button>
          <Button disabled={!picked} loading={mutation.isPending} onClick={() => mutation.mutate()}>
            Move booking
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-slate-600">
        Currently {formatRange(booking.start_datetime, booking.end_datetime, booking.timezone)} with {booking.resource.name}. Your current slot is kept
        until the new one is confirmed.
      </p>
      {mutation.error && <div className="mb-4"><Alert>{errorMessage(mutation.error)}</Alert></div>}
      <div className="mb-4">
        <DateNav date={date} onChange={(d) => { setDate(d); setPicked(null); }} min={todayIn(config.data?.default_timezone ?? "UTC")} />
      </div>
      <SlotPicker
        serviceId={booking.service.id}
        resourceId={booking.resource.id}
        date={date}
        quantity={booking.quantity}
        selected={picked}
        onSelect={setPicked}
        moving={booking}
      />
      {admin && (
        <label className="mt-4 flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} /> Override booking rules (audited)
        </label>
      )}
    </Modal>
  );
}

export function CancelModal({ booking, open, onClose, admin }: { booking: Booking; open: boolean; onClose: () => void; admin?: boolean }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const mutation = useMutation({
    mutationFn: () => api.post<Booking>(`/api/${admin ? "admin/" : ""}bookings/${booking.id}/cancel`, { reason: reason || null }),
    onSuccess: () => {
      queryClient.invalidateQueries();
      onClose();
    },
  });
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Cancel booking"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Keep booking
          </Button>
          <Button variant="danger" loading={mutation.isPending} onClick={() => mutation.mutate()}>
            Cancel booking
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-slate-600">
          {booking.service.name} · {formatRange(booking.start_datetime, booking.end_datetime, booking.timezone)}
        </p>
        {mutation.error && <Alert>{errorMessage(mutation.error)}</Alert>}
        <Field label="Reason (optional)">
          <Textarea value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

export function BookingDetailPage() {
  const { id } = useParams();
  const location = useLocation();
  const config = useConfig();
  const [modal, setModal] = useState<"cancel" | "reschedule" | null>(null);
  const booking = useQuery({ queryKey: ["booking", id], queryFn: () => api.get<Booking>(`/api/bookings/${id}`) });
  if (booking.isLoading) return <Loading />;
  if (booking.error) return <Alert>{errorMessage(booking.error)}</Alert>;
  const b = booking.data!;
  const state = location.state as { created?: boolean; rescheduled?: boolean } | null;
  const windowNote = (minutes: number, verb: string) =>
    minutes > 0 ? `You can ${verb} online until ${formatDuration(minutes)} before the start.` : null;

  return (
    <>
      <PageHeader title="Booking details" actions={<Link to="/bookings" className="text-sm text-slate-500 hover:text-slate-700">← All bookings</Link>} />
      <div className="mb-4 space-y-3">
        {state?.created && b.status === "CONFIRMED" && <Alert tone="success">You're booked! A confirmation has been sent.</Alert>}
        {state?.created && b.status === "PENDING" && <Alert tone="info">Request received. We'll confirm shortly.</Alert>}
        {state?.created && b.status === "WAITLISTED" && <Alert tone="info">You're #{b.waitlist_position} on the waitlist. We'll confirm you automatically if a place opens.</Alert>}
        {state?.rescheduled && <Alert tone="success">Your booking has been moved.</Alert>}
        {b.status === "CONFLICTED" && <Alert tone="warning">A schedule change affects this booking{b.conflict_reason ? ` (${b.conflict_reason})` : ""}. Our team will contact you, or you can reschedule it now.</Alert>}
        {b.rescheduled_to_id && (
          <Alert tone="info">
            This booking was moved. <Link to={`/bookings/${b.rescheduled_to_id}`} className="font-medium underline">See the new booking</Link>.
          </Alert>
        )}
      </div>
      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <BookingSummary booking={b} />
          <dl className="mt-6 grid gap-4 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-slate-500">Reference</dt>
              <dd className="font-mono text-xs">{b.id.slice(0, 8).toUpperCase()}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Price</dt>
              <dd>{formatMoney(b.price)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Booked</dt>
              <dd>{formatDateTime(b.created_at, b.timezone)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Timezone</dt>
              <dd>{b.timezone}</dd>
            </div>
            {b.notes && (
              <div className="sm:col-span-2">
                <dt className="text-slate-500">Notes</dt>
                <dd className="whitespace-pre-line">{b.notes}</dd>
              </div>
            )}
            {b.cancelled_at && (
              <div className="sm:col-span-2">
                <dt className="text-slate-500">Cancelled</dt>
                <dd>
                  {formatDateTime(b.cancelled_at, b.timezone)}
                  {b.cancellation_reason && ` — ${b.cancellation_reason}`}
                </dd>
              </div>
            )}
            {b.rescheduled_from_id && (
              <div className="sm:col-span-2">
                <dt className="text-slate-500">Moved from</dt>
                <dd>
                  <Link to={`/bookings/${b.rescheduled_from_id}`} className="text-brand-700 hover:underline">
                    previous booking
                  </Link>
                </dd>
              </div>
            )}
          </dl>
        </Card>
        <Card>
          <h2 className="mb-3 font-semibold">Manage</h2>
          <div className="flex flex-col gap-2">
            <Button variant="secondary" disabled={!b.can_reschedule} onClick={() => setModal("reschedule")}>
              Reschedule
            </Button>
            <Button variant="danger" disabled={!b.can_cancel} onClick={() => setModal("cancel")}>
              {b.status === "WAITLISTED" ? "Leave waitlist" : "Cancel booking"}
            </Button>
          </div>
          <div className="mt-4 space-y-1 text-xs text-slate-500">
            <p>{windowNote(config.data?.cancellation_window ?? 0, "cancel")}</p>
            <p>{windowNote(config.data?.rescheduling_window ?? 0, "reschedule")}</p>
          </div>
        </Card>
      </div>
      {modal === "cancel" && <CancelModal booking={b} open onClose={() => setModal(null)} />}
      {modal === "reschedule" && <RescheduleModal booking={b} open onClose={() => setModal(null)} />}
    </>
  );
}

export function ProfilePage() {
  const { user, refresh } = useAuth();
  const [name, setName] = useState(user?.name ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [passwords, setPasswords] = useState({ current_password: "", new_password: "" });
  const profile = useMutation({ mutationFn: () => api.put("/api/auth/me", { name, phone: phone || null }), onSuccess: () => refresh() });
  const password = useMutation({
    mutationFn: () => api.post("/api/auth/change-password", passwords),
    onSuccess: () => setPasswords({ current_password: "", new_password: "" }),
  });
  return (
    <>
      <PageHeader title="Profile" subtitle={user?.email} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="mb-4 font-semibold">Your details</h2>
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              profile.mutate();
            }}
          >
            {profile.isSuccess && <Alert tone="success">Saved.</Alert>}
            {profile.error && <Alert>{errorMessage(profile.error)}</Alert>}
            <Field label="Name">
              <Input required value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Phone">
              <Input type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
            </Field>
            <Button type="submit" loading={profile.isPending}>
              Save
            </Button>
          </form>
        </Card>
        <Card>
          <h2 className="mb-4 font-semibold">Change password</h2>
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              password.mutate();
            }}
          >
            {password.isSuccess && <Alert tone="success">Password updated. Other sessions were signed out.</Alert>}
            {password.error && <Alert>{errorMessage(password.error)}</Alert>}
            <Field label="Current password">
              <Input type="password" required autoComplete="current-password" value={passwords.current_password} onChange={(e) => setPasswords({ ...passwords, current_password: e.target.value })} />
            </Field>
            <Field label="New password" hint="At least 8 characters.">
              <Input type="password" required minLength={8} autoComplete="new-password" value={passwords.new_password} onChange={(e) => setPasswords({ ...passwords, new_password: e.target.value })} />
            </Field>
            <Button type="submit" loading={password.isPending}>
              Update password
            </Button>
          </form>
        </Card>
      </div>
    </>
  );
}
