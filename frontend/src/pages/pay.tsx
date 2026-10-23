import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { Payment } from "../api/types";
import { Alert, BackLink, Button, Card, Loading, PageHeader, buttonClass, linkClass } from "../components/ui";
import { formatMoney } from "../lib/format";

function useCountdown(until: string, running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  return Math.max(0, Date.parse(until) - now);
}

function clock(ms: number): string {
  const total = Math.ceil(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

const ENDED: Record<string, { tone: "info" | "success" | "warning"; text: string }> = {
  PAID: { tone: "success", text: "Paid. You're booked, and a confirmation is on its way to your inbox." },
  EXPIRED: { tone: "warning", text: "This payment wasn't made in time, so the time was released. You can book again." },
  CANCELLED: { tone: "info", text: "You cancelled this payment, so the time was released." },
  REFUND_DUE: { tone: "info", text: "This payment is being refunded to you by the business." },
  REFUNDED: { tone: "success", text: "This payment was refunded." },
};

/** The checkout page: where customers pay for a booking held for its payment
 * (also the page AI assistants send them to). With the demo gateway nothing is charged. */
export function PayPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const payment = useQuery({ queryKey: ["payment", id], queryFn: () => api.get<Payment>(`/api/payments/${id}`) });
  const p = payment.data;
  const left = useCountdown(p?.expires_at ?? new Date().toISOString(), p?.status === "PENDING");
  const done = (updated: Payment) => {
    queryClient.setQueryData(["payment", id], updated);
    queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
    queryClient.invalidateQueries({ queryKey: ["booking"] });
    if (updated.status === "PAID" && updated.booking_id) navigate(`/bookings/${updated.booking_id}`, { state: { paid: true } });
  };
  const pay = useMutation({ mutationFn: () => api.post<Payment>(`/api/payments/${id}/demo/complete`), onSuccess: done });
  const cancel = useMutation({ mutationFn: () => api.post<Payment>(`/api/payments/${id}/cancel`), onSuccess: done });

  if (payment.isLoading) return <Loading />;
  if (payment.error || !p) return <Alert>{errorMessage(payment.error)}</Alert>;
  const open = p.status === "PENDING" && left > 0;
  const status = p.status === "PENDING" && !open ? "EXPIRED" : p.status;
  const amount = `${formatMoney(p.amount)} ${p.currency}`;

  return (
    <div className="mx-auto max-w-lg">
      <BackLink to="/bookings">My bookings</BackLink>
      <PageHeader title={open ? `Pay ${amount}` : "Payment"} subtitle={open ? "Your booking is held while you pay." : undefined} />
      <Card>
        {p.booking && (
          <>
            <p className="text-xs text-muted">
              {p.booking.when.date} ({p.booking.when.zone})
            </p>
            <p className="mt-0.5 text-xl font-semibold tracking-tight text-fg">{p.booking.when.time}</p>
            <p className="mt-3 font-semibold text-fg">{p.booking.service}</p>
            <p className="text-sm text-muted">with {p.booking.resource}</p>
            {p.booking.place && <p className="mt-2 text-sm text-fg">{p.booking.place}</p>}
          </>
        )}
        <dl className="mt-4 flex justify-between border-t border-line pt-3 text-sm">
          <dt className="text-muted">Total</dt>
          <dd className="tabular font-semibold text-fg">{amount}</dd>
        </dl>
        {open ? (
          <>
            {p.demo && (
              <div className="mt-4">
                <Alert tone="info">Demo gateway: no card is needed and nothing is charged.</Alert>
              </div>
            )}
            <p className="tabular mt-4 text-xs text-muted" aria-label="Time left to pay">
              {clock(left)} left to pay
            </p>
            {(pay.error || cancel.error) && (
              <div className="mt-3">
                <Alert>{errorMessage(pay.error ?? cancel.error)}</Alert>
              </div>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              {p.demo ? (
                <Button loading={pay.isPending} disabled={cancel.isPending} onClick={() => pay.mutate()}>
                  Pay {amount} (demo)
                </Button>
              ) : (
                p.checkout_url && (
                  <a href={p.checkout_url} className={buttonClass()}>
                    Continue to payment
                  </a>
                )
              )}
              <Button variant="secondary" loading={cancel.isPending} disabled={pay.isPending} onClick={() => cancel.mutate()}>
                Cancel booking
              </Button>
            </div>
            <p className="mt-4 text-xs text-muted">
              You pay on this site only. Nobody, including an AI assistant, will ever ask you for card or bank details in a chat.
            </p>
          </>
        ) : (
          <div className="mt-4 space-y-3">
            <Alert tone={ENDED[status]?.tone ?? "info"}>{ENDED[status]?.text ?? status}</Alert>
            {p.booking_id && (
              <Link to={`/bookings/${p.booking_id}`} className={linkClass}>
                View booking
              </Link>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
