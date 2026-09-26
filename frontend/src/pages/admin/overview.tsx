import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../../api/client";
import type { Alternative, Booking, Dashboard, Page } from "../../api/types";
import { useConfig } from "../../components/Layout";
import { Alert, Button, Card, EmptyState, Field, Loading, Modal, PageHeader, Stat, StatusBadge, Textarea, linkClass } from "../../components/ui";
import { formatDateTime, formatDuration, formatRange } from "../../lib/format";

export function AdminOverviewPage() {
  const dashboard = useQuery({ queryKey: ["admin", "dashboard"], queryFn: () => api.get<Dashboard>("/api/admin/dashboard") });
  const upcoming = useQuery({
    queryKey: ["admin", "bookings", "next"],
    queryFn: () => api.get<Page<Booking>>("/api/admin/bookings", { status: ["PENDING", "CONFIRMED"], start_date: new Date().toISOString().slice(0, 10), limit: 200 }),
  });
  if (dashboard.isLoading) return <Loading />;
  if (dashboard.error) return <Alert>{errorMessage(dashboard.error)}</Alert>;
  const d = dashboard.data!;
  const next = (upcoming.data?.items ?? [])
    .filter((b) => new Date(b.end_datetime) > new Date())
    .sort((a, b) => a.start_datetime.localeCompare(b.start_datetime))
    .slice(0, 6);
  return (
    <>
      <PageHeader title="Overview" subtitle={`All times in ${d.timezone}`} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Today's bookings" value={d.todays_bookings} />
        <Stat label="Upcoming" value={d.upcoming_bookings} />
        <Stat label="Pending" value={d.pending_bookings} />
        <Stat label="Conflicts" value={d.conflicts} tone={d.conflicts ? "warn" : undefined} />
        <Stat label="Total users" value={d.total_users} />
        <Stat label="Active resources" value={d.active_resources} />
        <Stat label="Completed" value={d.completed_bookings} />
        <Stat label="Cancelled" value={d.cancelled_bookings} />
      </div>
      {d.conflicts > 0 && (
        <div className="mt-4">
          <Alert tone="warning">
            {d.conflicts} booking{d.conflicts > 1 ? "s need" : " needs"} attention after a schedule change.{" "}
            <Link to="/admin/conflicts" className="font-medium underline">
              Resolve conflicts
            </Link>
          </Alert>
        </div>
      )}
      <div className="mt-8 grid gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="mb-1 text-sm font-semibold text-fg">Resource utilization</h2>
          <p className="mb-4 text-xs text-muted">Booked time as a share of bookable time, next 7 days.</p>
          <div className="space-y-3">
            {d.resource_utilization.map((u) => (
              <div key={u.resource_id}>
                <div className="mb-1 flex justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate text-fg">{u.resource_name}</span>
                  <span className="tabular shrink-0 text-muted">
                    {Math.round(u.utilization * 100)}% · {formatDuration(u.booked_minutes)} of {formatDuration(u.available_minutes)}
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-subtle">
                  <div className="h-1.5 rounded-full bg-accent" style={{ width: `${Math.min(100, u.utilization * 100)}%` }} />
                </div>
              </div>
            ))}
            {!d.resource_utilization.length && <p className="text-sm text-muted">No active resources.</p>}
          </div>
        </Card>
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-fg">Coming up</h2>
            <Link to="/admin/calendar" className={`text-sm ${linkClass}`}>
              Open calendar
            </Link>
          </div>
          {next.length === 0 ? (
            <p className="text-sm text-muted">Nothing scheduled.</p>
          ) : (
            <ul className="-my-2.5 divide-y divide-line">
              {next.map((b) => (
                <li key={b.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                  <div className="min-w-0">
                    <div className="font-medium text-fg">
                      {b.service.name} · {b.resource.name}
                    </div>
                    <div className="tabular text-muted">
                      {formatDateTime(b.start_datetime, b.timezone)} · {b.user.name}
                    </div>
                  </div>
                  <StatusBadge status={b.status} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

function ResolveModal({ booking, onClose }: { booking: Booking; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const detail = useQuery({
    queryKey: ["conflict", booking.id],
    queryFn: () => api.get<{ booking: Booking; alternatives: Alternative[] }>(`/api/admin/conflicts/${booking.id}`),
  });
  const resolve = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post<Booking>(`/api/admin/conflicts/${booking.id}/resolve`, { reason: reason || null, ...body }),
    onSuccess: () => {
      onClose();
      // The conflict no longer exists; don't refetch its detail.
      queryClient.removeQueries({ queryKey: ["conflict", booking.id] });
      queryClient.invalidateQueries();
    },
  });
  return (
    <Modal open wide title="Resolve conflict" onClose={onClose}>
      <div className="space-y-5">
        <div>
          <div className="font-medium text-fg">
            {booking.service.name} · {booking.resource.name} · {booking.user.name}
          </div>
          <div className="tabular text-sm text-muted">{formatRange(booking.start_datetime, booking.end_datetime, booking.timezone)}</div>
          <div className="mt-1 text-sm text-danger-text">{booking.conflict_reason}</div>
        </div>
        {resolve.error && <Alert>{errorMessage(resolve.error)}</Alert>}
        <Field label="Note for the audit log / customer (optional)">
          <Textarea value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <div>
          <h3 className="mb-2 text-sm font-semibold text-fg">Move to an available slot</h3>
          {detail.isLoading ? (
            <Loading label="Finding alternatives…" />
          ) : detail.data?.alternatives.length ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {detail.data.alternatives.map((a) => (
                <button
                  key={`${a.resource_id}${a.start}`}
                  onClick={() =>
                    resolve.mutate(
                      a.same_resource ? { action: "reschedule", start: a.start } : { action: "reschedule", start: a.start, resource_id: a.resource_id },
                    )
                  }
                  className="rounded-md border border-line-strong bg-surface px-3 py-2 text-left text-sm hover:border-accent"
                >
                  <span className="block font-medium text-fg">{a.resource_name}</span>
                  <span className="text-xs text-muted">{formatDateTime(a.start, a.timezone)}</span>
                </button>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted">No nearby free slots. Use Bookings → Reschedule for other dates.</p>
          )}
        </div>
        <div className="flex flex-wrap gap-2 border-t border-line pt-4">
          <Button variant="secondary" loading={resolve.isPending} onClick={() => resolve.mutate({ action: "override" })}>
            Override &amp; keep booking
          </Button>
          <Button variant="danger" loading={resolve.isPending} onClick={() => resolve.mutate({ action: "cancel" })}>
            Cancel booking
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export function AdminConflictsPage() {
  const config = useConfig();
  const conflicts = useQuery({ queryKey: ["admin", "conflicts"], queryFn: () => api.get<Page<Booking>>("/api/admin/conflicts") });
  const [selected, setSelected] = useState<Booking | null>(null);
  return (
    <>
      <PageHeader title="Conflicts" subtitle="Bookings invalidated by schedule changes. Reschedule, reassign, cancel or override each one." />
      {conflicts.isLoading ? (
        <Loading />
      ) : !conflicts.data?.items.length ? (
        <EmptyState title="No conflicts">Every booking fits the current schedule.</EmptyState>
      ) : (
        <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface">
          {conflicts.data.items.map((b) => (
            <li key={b.id} className="flex flex-wrap items-center justify-between gap-4 px-4 py-3.5 sm:px-5">
              <div className="min-w-0">
                <div className="font-medium text-fg">
                  {b.service.name} · {b.resource.name}
                </div>
                <div className="tabular text-sm text-muted">
                  {formatRange(b.start_datetime, b.end_datetime, b.timezone ?? config.data?.default_timezone ?? "UTC")} · {b.user.name}
                </div>
                <div className="text-sm text-danger-text">{b.conflict_reason}</div>
              </div>
              <Button size="sm" onClick={() => setSelected(b)}>
                Resolve
              </Button>
            </li>
          ))}
        </ul>
      )}
      {selected && <ResolveModal booking={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
