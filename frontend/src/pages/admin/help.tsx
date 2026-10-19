import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage } from "../../api/client";
import type { HelpChatItem, HelpRequest, HelpRequestDetail } from "../../api/types";
import { useConfig } from "../../components/Layout";
import { Alert, BackLink, Badge, Button, Card, EmptyState, Field, Loading, PageHeader, Tabs, Textarea, cx, linkClass } from "../../components/ui";
import { dueLabel, formatDateTime } from "../../lib/format";

function Due({ request }: { request: HelpRequest }) {
  if (request.status === "RESOLVED") return <Badge tone="accent">Resolved</Badge>;
  return <Badge tone={request.overdue ? "danger" : "warn"}>{dueLabel(request.due_at)}</Badge>;
}

export function AdminHelpPage() {
  const config = useConfig();
  const tz = config.data?.default_timezone ?? "UTC";
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const list = useQuery({
    queryKey: ["admin", "help", status],
    queryFn: () => api.get<HelpRequest[]>("/api/admin/help-requests", { status }),
    refetchInterval: 60_000,
  });
  return (
    <>
      <PageHeader
        title="Help requests"
        subtitle="Problems customers sent from the booking assistant, with their chat. Contact them by e-mail or phone, then mark the request resolved."
      />
      <Tabs
        value={status}
        onChange={setStatus}
        tabs={[
          { value: "open", label: "Open" },
          { value: "resolved", label: "Resolved" },
        ]}
      />
      {list.isLoading ? (
        <Loading />
      ) : !list.data?.length ? (
        <EmptyState title={status === "open" ? "No open requests" : "Nothing resolved yet"}>
          {status === "open" ? "Nobody is waiting for the admin right now." : "Resolved requests will show here."}
        </EmptyState>
      ) : (
        <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface">
          {list.data.map((r) => (
            <li key={r.id}>
              <Link to={`/admin/help/${r.id}`} className="flex flex-wrap items-start justify-between gap-3 px-4 py-3.5 hover:bg-subtle sm:px-5">
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-fg">{r.customer.name}</div>
                  <p className="mt-0.5 line-clamp-2 text-sm text-muted">{r.summary}</p>
                  <div className="tabular mt-1 text-xs text-faint">Sent {formatDateTime(r.created_at, tz)}</div>
                </div>
                <Due request={r} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function ChatLine({ item, tz }: { item: HelpChatItem; tz: string }) {
  const who = item.kind === "user" ? "Customer" : item.kind === "assistant" ? "Assistant" : "Notice";
  return (
    <li className={cx("flex flex-col gap-1", item.kind === "user" ? "items-end" : "items-start")}>
      <span className="text-[11px] text-faint">
        {who} · {formatDateTime(item.created_at, tz)}
      </span>
      {item.text && (
        <p
          className={cx(
            "max-w-[85%] whitespace-pre-wrap break-words rounded-lg px-3 py-2 text-sm",
            item.kind === "user" ? "bg-accent text-accent-fg" : item.kind === "notice" ? "bg-subtle text-muted" : "border border-line bg-surface text-fg",
          )}
        >
          {item.text}
        </p>
      )}
      {item.blocked && <span className="text-xs text-danger-text">Blocked: {item.blocked}</span>}
      {item.cards.map((card) => (
        <span key={card.id} className="max-w-[85%] rounded-md border border-dashed border-line px-2.5 py-1.5 text-xs text-muted">
          {card.kind === "HANDOFF"
            ? `Card: contact the admin (${card.status.toLowerCase()})`
            : `Card: ${card.kind.toLowerCase()} ${card.service ?? ""}${card.resource ? ` with ${card.resource}` : ""}${card.when ? `, ${card.when.date}, ${card.when.time}` : ""} (${card.status.toLowerCase()})`}
        </span>
      ))}
    </li>
  );
}

export function AdminHelpRequestPage() {
  const { id } = useParams();
  const config = useConfig();
  const tz = config.data?.default_timezone ?? "UTC";
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const detail = useQuery({ queryKey: ["admin", "help-request", id], queryFn: () => api.get<HelpRequestDetail>(`/api/admin/help-requests/${id}`) });
  const resolve = useMutation({
    mutationFn: () => api.post<HelpRequest>(`/api/admin/help-requests/${id}/resolve`, { note: note.trim() || null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "help"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "help-request", id] });
      queryClient.invalidateQueries({ queryKey: ["admin", "help-count"] });
    },
  });

  if (detail.isLoading) return <Loading />;
  if (detail.error || !detail.data) return <Alert>{errorMessage(detail.error)}</Alert>;
  const r = detail.data;

  return (
    <>
      <BackLink to="/admin/help">Help requests</BackLink>
      <PageHeader title={r.customer.name} subtitle={`Sent ${formatDateTime(r.created_at, tz)} · reply promised by ${formatDateTime(r.due_at, tz)}`} actions={<Due request={r} />} />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-5">
          <Card>
            <h2 className="text-sm font-semibold text-fg">The problem, in their words</h2>
            <p className="mt-2 whitespace-pre-wrap text-sm text-fg">{r.summary}</p>
          </Card>
          <Card>
            <h2 className="text-sm font-semibold text-fg">The whole chat</h2>
            <p className="mt-0.5 text-xs text-muted">As the customer saw it when they sent it, oldest first.</p>
            <ul className="mt-4 space-y-3">
              {r.transcript.length ? r.transcript.map((item) => <ChatLine key={item.id} item={item} tz={tz} />) : <li className="text-sm text-muted">No messages.</li>}
            </ul>
            {r.since.length > 0 && (
              <>
                <h3 className="mt-6 border-t border-line pt-4 text-xs font-medium uppercase tracking-wide text-muted">After they contacted the admin</h3>
                <ul className="mt-3 space-y-3">
                  {r.since.map((item) => (
                    <ChatLine key={item.id} item={item} tz={tz} />
                  ))}
                </ul>
              </>
            )}
          </Card>
        </div>
        <div className="space-y-5">
          <Card>
            <h2 className="text-sm font-semibold text-fg">Contact</h2>
            <dl className="mt-2 space-y-1 text-sm">
              <div>
                <dt className="sr-only">E-mail</dt>
                <dd>
                  <a className={linkClass} href={`mailto:${r.customer.email}`}>
                    {r.customer.email}
                  </a>
                </dd>
              </div>
              {r.customer.phone && (
                <div>
                  <dt className="sr-only">Phone</dt>
                  <dd>
                    <a className={linkClass} href={`tel:${r.customer.phone.replace(/\s+/g, "")}`}>
                      {r.customer.phone}
                    </a>
                  </dd>
                </div>
              )}
            </dl>
          </Card>
          <Card>
            <h2 className="text-sm font-semibold text-fg">Upcoming bookings</h2>
            {r.bookings.length ? (
              <ul className="mt-2 space-y-2 text-sm">
                {r.bookings.map((b) => (
                  <li key={b.id}>
                    <Link to={`/admin/bookings?open=${b.id}`} className="font-medium text-fg hover:underline">
                      {b.service} · {b.resource}
                    </Link>
                    <div className="tabular text-xs text-muted">
                      {b.date}, {b.time} · {b.status}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-muted">None.</p>
            )}
          </Card>
          <Card>
            {r.status === "OPEN" ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  resolve.mutate();
                }}
                className="space-y-3"
              >
                <Field label="What was done (optional)" hint="The customer sees this note in their chat and by e-mail.">
                  <Textarea rows={3} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
                </Field>
                {resolve.error && <Alert>{errorMessage(resolve.error)}</Alert>}
                <Button type="submit" className="w-full" loading={resolve.isPending}>
                  Mark resolved
                </Button>
              </form>
            ) : (
              <div className="text-sm">
                <p className="font-medium text-fg">
                  Resolved{r.resolved_by ? ` by ${r.resolved_by}` : ""}
                  {r.resolved_at ? `, ${formatDateTime(r.resolved_at, tz)}` : ""}
                </p>
                {r.resolution_note && <p className="mt-1 whitespace-pre-wrap text-muted">{r.resolution_note}</p>}
              </div>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
