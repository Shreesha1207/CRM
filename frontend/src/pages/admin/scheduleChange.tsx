import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { errorMessage } from "../../api/client";
import type { ConflictAction, ScheduleChange } from "../../api/types";
import { Button, Modal, StatusBadge } from "../../components/ui";
import { formatDateTime } from "../../lib/format";

type ChangeRequest<T> = (params: { dry_run: boolean; conflict_action: ConflictAction }) => Promise<ScheduleChange<T>>;

const ACTIONS: { value: ConflictAction; label: string; description: string }[] = [
  { value: "mark_conflicted", label: "Mark them as conflicts", description: "Keep them for now and resolve each one (reschedule, reassign, cancel or override) from the Conflicts page." },
  { value: "keep", label: "Keep existing bookings", description: "Apply the change for new bookings only; existing bookings stay confirmed." },
  { value: "cancel", label: "Cancel them", description: "Cancel the affected bookings and notify the customers." },
];

/**
 * Every availability-affecting admin change runs twice: first as a dry run
 * that reports the bookings it would break ("This change affects 7 existing
 * bookings"), then -- after the admin picks what to do -- for real.
 */
export function useScheduleChange<T>(defaultTimeZone: string, onApplied?: (result: ScheduleChange<T>) => void) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<{ preview: ScheduleChange<T>; request: ChangeRequest<T> } | null>(null);
  const [action, setAction] = useState<ConflictAction>("mark_conflicted");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const finish = (result: ScheduleChange<T>) => {
    setPending(null);
    queryClient.invalidateQueries();
    onApplied?.(result);
  };

  const run = async (request: ChangeRequest<T>) => {
    setError(null);
    setBusy(true);
    try {
      const preview = await request({ dry_run: true, conflict_action: "mark_conflicted" });
      if (preview.affected_count === 0) finish(await request({ dry_run: false, conflict_action: "keep" }));
      else {
        setAction("mark_conflicted");
        setPending({ preview, request });
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      finish(await pending.request({ dry_run: false, conflict_action: action }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const modal = pending && (
    <Modal
      open
      wide
      title={`This change affects ${pending.preview.affected_count} existing booking${pending.preview.affected_count === 1 ? "" : "s"}`}
      onClose={() => setPending(null)}
      footer={
        <>
          <Button variant="secondary" onClick={() => setPending(null)}>
            Don't change anything
          </Button>
          <Button variant={action === "cancel" ? "danger" : "primary"} loading={busy} onClick={confirm}>
            Apply change
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        {error && <p className="text-sm text-danger-text">{error}</p>}
        <ul className="divide-y divide-line rounded-md border border-line">
          {pending.preview.affected_bookings.map((a) => (
            <li key={a.booking_id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
              <div className="min-w-0">
                <div className="font-medium text-fg">
                  {a.booking.service_name} · {a.booking.resource_name}
                </div>
                <div className="tabular text-muted">
                  {formatDateTime(a.booking.start_datetime, defaultTimeZone)} · {a.booking.user_name}
                </div>
              </div>
              <div className="text-right">
                <StatusBadge status={a.booking.status} />
                <div className="mt-1 text-xs text-danger-text">{a.message}</div>
              </div>
            </li>
          ))}
        </ul>
        <fieldset className="space-y-2">
          <legend className="mb-2 text-sm font-medium text-fg">What should happen to them?</legend>
          {ACTIONS.map((a) => (
            <label key={a.value} className="flex cursor-pointer gap-3 rounded-md border border-line p-3 has-[:checked]:border-accent has-[:checked]:bg-accent-soft">
              <input type="radio" name="conflict_action" value={a.value} checked={action === a.value} onChange={() => setAction(a.value)} className="mt-1" />
              <span>
                <span className="block text-sm font-medium text-fg">{a.label}</span>
                <span className="block text-xs text-muted">{a.description}</span>
              </span>
            </label>
          ))}
        </fieldset>
      </div>
    </Modal>
  );

  return { run, modal, error: pending ? null : error, busy };
}
