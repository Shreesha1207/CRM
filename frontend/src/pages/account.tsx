import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { Booking, Page, Service } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { BookingListItem, BookingRows, BookingSummary, DateNav, SlotPicker, type PickedSlot } from "../components/booking";
import { useConfig } from "../components/Layout";
import {
  Alert,
  BackLink,
  Button,
  Card,
  Checkbox,
  EmptyState,
  Field,
  Input,
  Loading,
  Modal,
  PageHeader,
  Pagination,
  Tabs,
  Textarea,
  buttonClass,
  linkClass,
} from "../components/ui";
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
  if (!bookings.data?.items.length) return scope === "waitlisted" ? <NoWaitlists /> : <EmptyState title={empty} />;
  return (
    <>
      <BookingRows>
        {bookings.data.items.map((b) => (
          <BookingListItem key={b.id} booking={b} to={`/bookings/${b.id}`} />
        ))}
      </BookingRows>
      <Pagination total={bookings.data.total} limit={20} offset={offset} onChange={setOffset} />
    </>
  );
}

/** How to get on a waitlist, since nothing else in the account area says so. */
function NoWaitlists() {
  const services = useQuery({ queryKey: ["services"], queryFn: () => api.get<Service[]>("/api/services") });
  const groups = (services.data ?? []).filter((s) => s.booking_type === "CAPACITY");
  return (
    <EmptyState title="You're not on any waitlists">
      <p className="mx-auto max-w-md">
        When a group session is full, choose it on the booking page to join its waitlist. If a place opens up, we'll book you in
        automatically and let you know.
      </p>
      {groups.length > 0 && (
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {groups.map((s) => (
            <Link key={s.id} to={`/book?service=${s.id}`} className={buttonClass("secondary", "sm")}>
              Book {s.name}
            </Link>
          ))}
        </div>
      )}
    </EmptyState>
  );
}

/** The Waitlisted tab is shown while the waitlist is open, or while the user is still on one. */
function useShowWaitlist() {
  const config = useConfig();
  const waitlisted = useBookings("waitlisted", 5);
  return { show: !!config.data?.allow_waitlist || !!waitlisted.data?.total, waitlisted };
}

const newBooking = (
  <Link to="/book" className={buttonClass()}>
    New booking
  </Link>
);

export function DashboardPage() {
  const { user } = useAuth();
  const upcoming = useBookings("upcoming", 3);
  const { show: showWaitlist, waitlisted } = useShowWaitlist();
  const [tab, setTab] = useState<Scope>("upcoming");
  const next = upcoming.data?.items[0];
  return (
    <>
      <PageHeader title={`Hello, ${user?.name.split(" ")[0]}`} subtitle="Here's what's coming up." actions={newBooking} />
      <div className="mb-10 grid gap-4 md:grid-cols-3">
        <Card className="md:col-span-2">
          <p className="text-xs font-medium text-muted">Next booking</p>
          {upcoming.isLoading ? (
            <Loading />
          ) : next ? (
            <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
              <BookingSummary booking={next} />
              <Link to={`/bookings/${next.id}`} className={buttonClass("secondary", "sm")}>
                View
              </Link>
            </div>
          ) : (
            <p className="mt-3 text-sm text-muted">
              No upcoming bookings.{" "}
              <Link to="/book" className={linkClass}>
                Book something
              </Link>
              .
            </p>
          )}
        </Card>
        <Card>
          <p className="text-xs font-medium text-muted">At a glance</p>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-muted">Upcoming</dt>
              <dd className="tabular font-semibold text-fg">{upcoming.data?.total ?? "–"}</dd>
            </div>
            {showWaitlist && (
              <div className="flex justify-between">
                <dt className="text-muted">On a waitlist</dt>
                <dd className="tabular font-semibold text-fg">{waitlisted.data?.total ?? "–"}</dd>
              </div>
            )}
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
          ...(showWaitlist ? [{ value: "waitlisted" as Scope, label: "Waitlisted" }] : []),
        ]}
      />
      <BookingList key={tab} scope={tab} empty={`No ${tab} bookings.`} />
    </>
  );
}

export function BookingsPage() {
  const location = useLocation();
  const [tab, setTab] = useState<Scope>("upcoming");
  const { show: showWaitlist } = useShowWaitlist();
  return (
    <>
      <PageHeader title="My bookings" actions={newBooking} />
      {(location.state as { created?: boolean } | null)?.created && (
        <div className="mb-4">
          <Alert tone="success">Your bookings were created.</Alert>
        </div>
      )}
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "upcoming", label: "Upcoming" },
          { value: "past", label: "Past" },
          { value: "cancelled", label: "Cancelled" },
          ...(showWaitlist ? [{ value: "waitlisted" as Scope, label: "Waitlisted" }] : []),
          { value: "all", label: "All" },
        ]}
      />
      <BookingList key={tab} scope={tab} empty="Nothing here yet." />
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
      <p className="mb-4 text-sm text-muted">
        Currently {formatRange(booking.start_datetime, booking.end_datetime, booking.timezone)} with {booking.resource.name}. Your current slot is kept
        until the new one is confirmed.
      </p>
      {mutation.error && (
        <div className="mb-4">
          <Alert>{errorMessage(mutation.error)}</Alert>
        </div>
      )}
      <div className="mb-4">
        <DateNav
          date={date}
          onChange={(d) => {
            setDate(d);
            setPicked(null);
          }}
          min={todayIn(config.data?.default_timezone ?? "UTC")}
        />
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
        <div className="mt-4">
          <Checkbox label="Override booking rules (audited)" checked={override} onChange={(e) => setOverride(e.target.checked)} />
        </div>
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
        <p className="text-sm text-muted">
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

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_1fr] gap-4 py-3 sm:grid-cols-[8rem_1fr]">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="min-w-0 text-sm text-fg">{children}</dd>
    </div>
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
  const notes = [windowNote(config.data?.cancellation_window ?? 0, "cancel"), windowNote(config.data?.rescheduling_window ?? 0, "reschedule")].filter(Boolean);

  return (
    <>
      <BackLink to="/bookings">My bookings</BackLink>
      <PageHeader title="Booking details" />
      <div className="mb-4 space-y-3 empty:hidden">
        {state?.created && b.status === "CONFIRMED" && <Alert tone="success">You're booked! A confirmation has been sent.</Alert>}
        {state?.created && b.status === "PENDING" && <Alert tone="info">Request received. We'll confirm shortly.</Alert>}
        {state?.created && b.status === "WAITLISTED" && (
          <Alert tone="info">You're #{b.waitlist_position} on the waitlist. We'll confirm you automatically if a place opens.</Alert>
        )}
        {state?.rescheduled && <Alert tone="success">Your booking has been moved.</Alert>}
        {b.status === "CONFLICTED" && (
          <Alert tone="warning">
            A schedule change affects this booking{b.conflict_reason ? ` (${b.conflict_reason})` : ""}. Our team will contact you, or you can reschedule it now.
          </Alert>
        )}
        {b.rescheduled_to_id && (
          <Alert tone="info">
            This booking was moved.{" "}
            <Link to={`/bookings/${b.rescheduled_to_id}`} className="font-medium underline">
              See the new booking
            </Link>
            .
          </Alert>
        )}
      </div>
      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <BookingSummary booking={b} compact />
          <dl className="mt-4 divide-y divide-line border-t border-line">
            <DetailRow label="Reference">
              <span className="font-mono text-xs">{b.id.slice(0, 8).toUpperCase()}</span>
            </DetailRow>
            <DetailRow label="Length">{formatDuration((Date.parse(b.end_datetime) - Date.parse(b.start_datetime)) / 60_000)}</DetailRow>
            <DetailRow label="Price">
              <span className="tabular">{formatMoney(b.price)}</span>
            </DetailRow>
            {b.quantity > 1 && <DetailRow label="Places">{b.quantity}</DetailRow>}
            <DetailRow label="Booked">{formatDateTime(b.created_at, b.timezone)}</DetailRow>
            <DetailRow label="Timezone">{b.timezone}</DetailRow>
            {b.notes && (
              <DetailRow label="Notes">
                <span className="whitespace-pre-line">{b.notes}</span>
              </DetailRow>
            )}
            {b.cancelled_at && (
              <DetailRow label="Cancelled">
                {formatDateTime(b.cancelled_at, b.timezone)}
                {b.cancellation_reason && ` — ${b.cancellation_reason}`}
              </DetailRow>
            )}
            {b.rescheduled_from_id && (
              <DetailRow label="Moved from">
                <Link to={`/bookings/${b.rescheduled_from_id}`} className={linkClass}>
                  previous booking
                </Link>
              </DetailRow>
            )}
          </dl>
        </Card>
        <Card className="self-start">
          <h2 className="mb-3 text-sm font-semibold text-fg">Manage</h2>
          <div className="flex flex-col gap-2">
            <Button variant="secondary" disabled={!b.can_reschedule} onClick={() => setModal("reschedule")}>
              Reschedule
            </Button>
            <Button variant="danger" disabled={!b.can_cancel} onClick={() => setModal("cancel")}>
              {b.status === "WAITLISTED" ? "Leave waitlist" : "Cancel booking"}
            </Button>
          </div>
          {notes.length > 0 && (
            <div className="mt-4 space-y-1 text-xs text-muted">
              {notes.map((n) => (
                <p key={n}>{n}</p>
              ))}
            </div>
          )}
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
          <h2 className="mb-4 text-sm font-semibold text-fg">Your details</h2>
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
          <h2 className="mb-4 text-sm font-semibold text-fg">Change password</h2>
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
              <Input
                type="password"
                required
                autoComplete="current-password"
                value={passwords.current_password}
                onChange={(e) => setPasswords({ ...passwords, current_password: e.target.value })}
              />
            </Field>
            <Field label="New password" hint="At least 8 characters.">
              <Input
                type="password"
                required
                minLength={8}
                autoComplete="new-password"
                value={passwords.new_password}
                onChange={(e) => setPasswords({ ...passwords, new_password: e.target.value })}
              />
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
