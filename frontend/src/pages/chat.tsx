import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, api, errorMessage, streamPost } from "../api/client";
import type { ChatCard, ChatConversation, ChatConversationDetail, ChatItem, ChatSubscription } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useConfig } from "../components/Layout";
import { Check, ChatBubble, Lock, Send, ThumbDown, ThumbUp } from "../components/icons";
import { Alert, Button, Card, Loading, MapLink, Select, Spinner, Textarea, buttonClass, cx, linkClass } from "../components/ui";

const MAX_CHARS = 1000;
// A description for the admin: long enough to say what went wrong.
const HELP_MIN = 20;
const HELP_MAX = 500;

function useSubscription() {
  return useQuery({ queryKey: ["chat", "subscription"], queryFn: () => api.get<ChatSubscription>("/api/chat/subscription") });
}

// ---------------------------------------------------------------- the (demo) paywall

const FEATURES = [
  ["Book in one sentence", "“Book strength coaching with Sam next Tuesday at 10 for two hours.” The time is held for you to confirm."],
  ["See what's free", "Free times across the week, for any service or person."],
  ["Move or cancel", "Change a booking by asking; nothing changes until you confirm."],
  ["Answers about us", "Opening hours, prices and policies, any time."],
  ["From your own AI", "Connect Claude, ChatGPT, Gemini or a Siri Shortcut to book the same way. They ask before every change."],
] as const;

export function ChatUpgradePage() {
  const subscription = useSubscription();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const pay = useMutation({
    mutationFn: () => api.post<ChatSubscription>("/api/chat/subscription"),
    onSuccess: (sub) => {
      queryClient.setQueryData(["chat", "subscription"], sub);
      navigate("/chat");
    },
  });

  if (subscription.isLoading) return <Loading />;
  if (subscription.data?.active) return <Navigate to="/chat" replace />;
  const price = subscription.data?.price_label ?? "9.99 / month";

  return (
    <div className="mx-auto max-w-md pt-2 sm:pt-8">
      <div className="mb-6 text-center">
        <span className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-accent-soft text-accent-text">
          <ChatBubble className="h-5 w-5" />
        </span>
        <h1 className="mt-4 text-xl font-semibold tracking-tight text-fg">Booking Assistant</h1>
        <p className="mt-1 text-sm text-muted">Book, move and cancel appointments by just asking.</p>
      </div>
      <Card className="sm:p-6">
        <ul className="space-y-3">
          {FEATURES.map(([title, text]) => (
            <li key={title} className="flex gap-3">
              <Check className="mt-0.5 h-4 w-4 shrink-0 text-accent-text" />
              <div>
                <p className="text-sm font-medium text-fg">{title}</p>
                <p className="text-sm text-muted">{text}</p>
              </div>
            </li>
          ))}
        </ul>
        <dl className="mt-6 flex justify-between gap-4 border-t border-line pt-4 text-sm">
          <dt className="text-muted">Booking Assistant</dt>
          <dd className="tabular font-semibold text-fg">{price}</dd>
        </dl>
        {pay.error && (
          <div className="mt-4">
            <Alert>{errorMessage(pay.error)}</Alert>
          </div>
        )}
        <Button className="mt-5 w-full" loading={pay.isPending} onClick={() => pay.mutate()}>
          <Lock className="h-4 w-4" /> Pay {price}
        </Button>
        <p className="mt-3 text-center text-xs text-muted">Demo checkout: no card is needed and nothing is charged.</p>
        <p className="mt-1 text-center text-xs text-muted">Your chats are private. If you contact the admin about a problem, they can read that chat to help you.</p>
      </Card>
      <p className="mt-5 text-center text-sm text-muted">
        Prefer to book yourself?{" "}
        <Link to="/book" className={linkClass}>
          Open the booking page
        </Link>
      </p>
    </div>
  );
}

// ---------------------------------------------------------------- booking cards

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

function countdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

const KIND_LABEL = { HOLD: "Held for you", RESCHEDULE: "Move booking", CANCEL: "Cancel booking", HANDOFF: "Contact admin" } as const;

function outcome(card: ChatCard): string {
  if (card.kind === "HANDOFF") {
    if (card.status === "CONFIRMED") return card.result_message ?? "Sent to the admin.";
    if (card.status === "FAILED") return card.result_message ?? "That didn't work, so nothing was sent.";
    return card.status === "EXPIRED" ? "Not sent: this offer expired." : "Not sent.";
  }
  if (card.status === "FAILED") return card.result_message ?? "That didn't work, so nothing changed.";
  if (card.status === "EXPIRED") return card.kind === "HOLD" ? "Not confirmed in time, so the time was released." : "This offer expired; nothing changed.";
  if (card.status === "DECLINED") return card.kind === "HOLD" ? "Deleted. The time is free again." : "Kept as it was.";
  if (card.kind === "HOLD" && card.payment?.status === "PENDING") return card.payment.checkout_url ? "Confirmed. Pay to book it; the time is held until then." : "Not paid in time, so the time was released.";
  if (card.kind === "HOLD") return card.booking_status === "PENDING" ? "Booked. The business will confirm it shortly." : "Booked. A confirmation is on its way to your inbox.";
  return card.kind === "RESCHEDULE" ? "Moved. You'll get an e-mail with the new time." : "Cancelled.";
}

/** The same details as the booking e-mails, with Confirm and Delete (or Decline). */
export function BookingCard({ card, onAnswered }: { card: ChatCard; onAnswered: (card: ChatCard) => void }) {
  const queryClient = useQueryClient();
  const expiresAt = new Date(card.expires_at).getTime();
  const now = useNow(card.status === "PENDING");
  const open = card.status === "PENDING" && now < expiresAt;
  const status = card.status === "PENDING" && !open ? "EXPIRED" : card.status;
  // HANDOFF: the customer checks or writes the description before it goes to the admin.
  const [description, setDescription] = useState(card.summary ?? "");
  const answer = useMutation({
    mutationFn: (action: "confirm" | "decline") =>
      api.post<ChatCard>(`/api/chat/proposals/${card.id}/${action}`, action === "confirm" && card.kind === "HANDOFF" ? { summary: description.trim() } : undefined),
    onSuccess: (updated) => {
      onAnswered(updated);
      queryClient.invalidateQueries({ queryKey: ["bookings"] });
      queryClient.invalidateQueries({ queryKey: ["booking"] });
    },
  });
  const struck = (card.kind === "CANCEL" && status === "CONFIRMED") || (card.kind === "HOLD" && (status === "DECLINED" || status === "EXPIRED"));

  return (
    <div className="w-full max-w-md rounded-lg border border-line bg-surface p-4 shadow-sm" data-testid="booking-card">
      <div className="mb-3 flex items-center justify-between gap-3">
        <span className="text-xs font-medium uppercase tracking-wide text-accent-text">{KIND_LABEL[card.kind]}</span>
        {open && card.kind === "HOLD" && (
          <span className="tabular text-xs text-muted" aria-label="Time left to confirm">
            {countdown(expiresAt - now)} left
          </span>
        )}
      </div>
      {card.when && (
        <>
          <p className="text-xs text-muted">
            {card.when.date} ({card.when.zone})
          </p>
          <p className={cx("mt-0.5 text-xl font-semibold tracking-tight text-fg", struck && "text-faint line-through")}>{card.when.time}</p>
          {card.was && (
            <p className="mt-0.5 text-xs text-muted">
              Was{" "}
              <span className="line-through">
                {card.was.date}, {card.was.time}
              </span>
            </p>
          )}
          <p className="mt-3 font-semibold text-fg">
            {card.service}
            {(card.quantity ?? 1) > 1 && <span className="font-normal text-muted"> ({card.quantity} places)</span>}
          </p>
          <p className="text-sm text-muted">with {card.resource}</p>
          {card.place && (
            <div className="mt-3">
              <p className="text-xs text-muted">Where</p>
              <p className="text-sm text-fg">{card.place}</p>
              <MapLink url={card.map_url} className="mt-1" />
            </div>
          )}
          {card.price && (
            <dl className="mt-3 flex justify-between border-t border-line pt-2 text-sm">
              <dt className="text-muted">Price</dt>
              <dd className="tabular font-semibold text-fg">{card.price}</dd>
            </dl>
          )}
        </>
      )}
      {card.kind === "HANDOFF" &&
        (open ? (
          <>
            <label htmlFor={`handoff-${card.id}`} className="text-sm font-medium text-fg">
              Tell the admin what happened
            </label>
            <Textarea
              id={`handoff-${card.id}`}
              rows={3}
              maxLength={HELP_MAX}
              value={description}
              placeholder="e.g. I was charged twice for my session on 20 Oct"
              onChange={(e) => setDescription(e.target.value)}
              className="mt-2"
            />
            <p className="mt-1 flex justify-between gap-2 text-xs text-muted">
              <span>They'll read it with this chat and contact you by e-mail or phone within {card.response_hours ?? 6} hours.</span>
              <span className="tabular shrink-0">{description.trim().length < HELP_MIN ? `${HELP_MIN}+ characters` : `${description.trim().length}/${HELP_MAX}`}</span>
            </p>
          </>
        ) : (
          <>
            <p className="text-sm font-medium text-fg">Your message to the admin</p>
            <blockquote className="mt-2 whitespace-pre-wrap border-l-2 border-line pl-3 text-sm text-fg">{card.summary}</blockquote>
          </>
        ))}
      {answer.error && (
        <div className="mt-3">
          <Alert>{errorMessage(answer.error)}</Alert>
        </div>
      )}
      {open ? (
        <div className="mt-4 flex gap-2">
          <Button
            size="sm"
            loading={answer.isPending && answer.variables === "confirm"}
            disabled={answer.isPending || (card.kind === "HANDOFF" && description.trim().length < HELP_MIN)}
            onClick={() => answer.mutate("confirm")}
          >
            {card.kind === "HANDOFF" ? "Send to admin" : "Confirm"}
          </Button>
          <Button size="sm" variant="secondary" loading={answer.isPending && answer.variables === "decline"} disabled={answer.isPending} onClick={() => answer.mutate("decline")}>
            {card.kind === "HOLD" ? "Delete" : card.kind === "HANDOFF" ? "No thanks" : "Decline"}
          </Button>
        </div>
      ) : (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-sm">
          <span className={status === "FAILED" ? "text-danger-text" : "text-muted"}>{outcome({ ...card, status })}</span>
          {status === "CONFIRMED" && card.payment?.checkout_url ? (
            <Link to={`/pay/${card.payment.id}`} className={buttonClass("primary", "sm")}>
              Pay {card.payment.amount} {card.payment.currency} now
            </Link>
          ) : (
            status === "CONFIRMED" &&
            card.kind !== "CANCEL" &&
            card.booking_id && (
              <Link to={`/bookings/${card.booking_id}`} className={linkClass}>
                View booking
              </Link>
            )
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- contacting the admin

/** After the assistant failed: the customer describes the problem and sends it, with this chat, to the admin. */
function ContactAdmin({ conversationId }: { conversationId: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const send = useMutation({
    mutationFn: () => api.post(`/api/chat/conversations/${conversationId}/help`, { summary: text.trim() }),
    onSuccess: () => {
      setOpen(false);
      queryClient.invalidateQueries({ queryKey: ["chat", "conversation", conversationId] });
    },
  });
  if (!open) {
    return (
      <button className={buttonClass("secondary", "sm", "rounded-full")} onClick={() => setOpen(true)}>
        Contact admin
      </button>
    );
  }
  const length = text.trim().length;
  return (
    <form
      className="w-full max-w-md rounded-lg border border-line bg-surface p-4 shadow-sm"
      onSubmit={(e) => {
        e.preventDefault();
        send.mutate();
      }}
    >
      <label htmlFor="help-text" className="text-sm font-medium text-fg">
        What went wrong?
      </label>
      <p className="mt-0.5 text-xs text-muted">The admin gets this and the chat, and contacts you by e-mail or phone.</p>
      <Textarea id="help-text" rows={3} maxLength={HELP_MAX} value={text} onChange={(e) => setText(e.target.value)} className="mt-2" />
      <p className="tabular mt-1 text-right text-xs text-muted">
        {length < HELP_MIN ? `At least ${HELP_MIN} characters` : `${length}/${HELP_MAX}`}
      </p>
      {send.error && (
        <div className="mt-2">
          <Alert>{errorMessage(send.error)}</Alert>
        </div>
      )}
      <div className="mt-3 flex gap-2">
        <Button type="submit" size="sm" disabled={length < HELP_MIN} loading={send.isPending}>
          Send to admin
        </Button>
        <Button type="button" size="sm" variant="secondary" onClick={() => setOpen(false)} disabled={send.isPending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- the conversation

function Feedback({ item }: { item: ChatItem }) {
  const [value, setValue] = useState(item.feedback);
  const send = useMutation({
    mutationFn: (v: 1 | -1 | null) => api.post(`/api/chat/messages/${item.id}/feedback`, { value: v }),
    onMutate: (v) => setValue(v),
  });
  const toggle = (v: 1 | -1) => send.mutate(value === v ? null : v);
  const cls = (on: boolean) => cx("rounded p-1 hover:bg-subtle", on ? "text-accent-text" : "text-faint hover:text-muted");
  return (
    <div className="flex gap-0.5">
      <button className={cls(value === 1)} onClick={() => toggle(1)} aria-label="Helpful" aria-pressed={value === 1}>
        <ThumbUp className="h-3.5 w-3.5" />
      </button>
      <button className={cls(value === -1)} onClick={() => toggle(-1)} aria-label="Not helpful" aria-pressed={value === -1}>
        <ThumbDown className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function Message({
  item,
  last,
  busy,
  conversationId,
  onChoose,
  onCard,
}: {
  item: ChatItem;
  last: boolean;
  busy: boolean;
  conversationId: string | null;
  onChoose: (text: string) => void;
  onCard: (card: ChatCard) => void;
}) {
  if (item.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg rounded-br-sm bg-accent px-3.5 py-2 text-sm text-accent-fg">{item.text}</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-start gap-2">
      {item.text && (
        <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg rounded-bl-sm border border-line bg-surface px-3.5 py-2 text-sm text-fg">{item.text}</p>
      )}
      {item.cards.map((card) => (
        <BookingCard key={card.id} card={card} onAnswered={onCard} />
      ))}
      {last && item.contact_admin && conversationId && <ContactAdmin conversationId={conversationId} />}
      {last && item.choices.length > 0 && (
        <div className="flex max-w-full flex-wrap gap-2">
          {item.choices.map((choice) => (
            <button key={choice} className={buttonClass("secondary", "sm", "rounded-full")} disabled={busy} onClick={() => onChoose(choice)}>
              {choice}
            </button>
          ))}
        </div>
      )}
      {item.id !== "pending" && <Feedback item={item} />}
    </div>
  );
}

export function ChatPage() {
  const subscription = useSubscription();
  const config = useConfig();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const conversations = useQuery({
    queryKey: ["chat", "conversations"],
    queryFn: () => api.get<ChatConversation[]>("/api/chat/conversations"),
    enabled: !!subscription.data?.active,
  });
  const selected = params.get("c") ?? conversations.data?.[0]?.id ?? null;
  const detail = useQuery({
    queryKey: ["chat", "conversation", selected],
    queryFn: () => api.get<ChatConversationDetail>(`/api/chat/conversations/${selected}`),
    enabled: !!selected && !!subscription.data?.active,
  });

  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const items = detail.data?.items ?? [];

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [items.length, pending, status]);

  const cancel = useMutation({
    mutationFn: () => api.del<ChatSubscription>("/api/chat/subscription"),
    onSuccess: (sub) => {
      queryClient.setQueryData(["chat", "subscription"], sub);
      navigate("/chat/upgrade");
    },
  });

  if (subscription.isLoading) return <Loading />;
  if (!subscription.data?.active) return <Navigate to="/chat/upgrade" replace />;
  const paused = subscription.data.paused_until && new Date(subscription.data.paused_until).getTime() > Date.now();

  const select = (id: string) => setParams({ c: id });

  const newChat = async () => {
    const created = await api.post<ChatConversation>("/api/chat/conversations");
    await queryClient.invalidateQueries({ queryKey: ["chat", "conversations"] });
    select(created.id);
    return created.id;
  };

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || pending) return;
    setError(null);
    setPending(message);
    setStatus("Reading your message…");
    setDraft("");
    try {
      const id = selected ?? (await newChat());
      await streamPost(`/api/chat/conversations/${id}/messages`, { text: message }, ({ event, data }) => {
        if (event === "status") setStatus((data as { label: string }).label);
        if (event === "reply") {
          queryClient.setQueryData<ChatConversationDetail>(["chat", "conversation", id], (old) =>
            old ? { ...old, items: [...old.items, { ...placeholder(message), id: `sent-${Date.now()}` }, data as ChatItem] } : old,
          );
        }
      });
      await queryClient.invalidateQueries({ queryKey: ["chat", "conversation", id] });
      queryClient.invalidateQueries({ queryKey: ["chat", "conversations"] });
    } catch (e) {
      setDraft(message);
      setError(errorMessage(e));
      if (e instanceof ApiError && (e.code === "CHAT_PAUSED" || e.status === 402)) queryClient.invalidateQueries({ queryKey: ["chat", "subscription"] });
    } finally {
      setPending(null);
      setStatus(null);
    }
  };

  const replaceCard = (card: ChatCard) => {
    queryClient.setQueryData<ChatConversationDetail>(["chat", "conversation", selected], (old) =>
      old ? { ...old, items: old.items.map((i) => ({ ...i, cards: i.cards.map((c) => (c.id === card.id ? card : c)) })) } : old,
    );
    // Sending a problem to the admin adds its confirmation to the chat.
    if (card.kind === "HANDOFF" && card.status === "CONFIRMED") queryClient.invalidateQueries({ queryKey: ["chat", "conversation", selected] });
  };

  const starters = detail.data?.starters ?? ["Book an appointment", "What's free this week?", "My upcoming bookings", "Move a booking"];
  const business = config.data?.business_name ?? "us";
  const empty = !detail.isLoading && items.length === 0 && !pending;

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-fg sm:text-2xl">Booking Assistant</h1>
          <p className="mt-0.5 text-sm text-muted">Ask it to book, move or cancel. You confirm every change.</p>
        </div>
        <div className="flex items-center gap-2">
          {(conversations.data?.length ?? 0) > 1 && (
            <div className="w-44 lg:hidden">
              <Select value={selected ?? ""} onChange={(e) => select(e.target.value)} aria-label="Conversation">
                {conversations.data?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.title}
                  </option>
                ))}
              </Select>
            </div>
          )}
          <Button variant="secondary" onClick={() => newChat()} disabled={!!pending}>
            New chat
          </Button>
        </div>
      </div>

      {!subscription.data.configured && (
        <div className="mb-4">
          <Alert tone="info">The assistant isn't switched on for this site yet: its API keys haven't been set. You can still browse your chats.</Alert>
        </div>
      )}

      <div className="flex gap-5">
        <aside className="hidden w-56 shrink-0 lg:block" aria-label="Chats">
          <ul className="space-y-0.5">
            {conversations.data?.map((c) => (
              <li key={c.id}>
                <button
                  onClick={() => select(c.id)}
                  className={cx(
                    "block w-full truncate rounded-md px-2.5 py-1.5 text-left text-sm",
                    c.id === selected ? "bg-subtle font-medium text-fg" : "text-muted hover:bg-subtle hover:text-fg",
                  )}
                >
                  {c.title}
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <section className="flex min-w-0 flex-1 flex-col rounded-lg border border-line bg-canvas">
          <div className="h-[calc(100dvh-20rem)] min-h-80 space-y-4 overflow-y-auto p-4 sm:p-5" aria-live="polite">
            {detail.isLoading && <Loading />}
            {empty && (
              <div className="py-6 text-center">
                <p className="text-sm font-medium text-fg">Hi {user?.name.split(" ")[0]}, how can I help?</p>
                <p className="mx-auto mt-1 max-w-sm text-sm text-muted">
                  Try “Book a session next Tuesday at 10 for an hour”, or ask anything about {business}.
                </p>
                <div className="mt-4 flex flex-wrap justify-center gap-2">
                  {starters.map((s) => (
                    <button key={s} className={buttonClass("secondary", "sm", "rounded-full")} onClick={() => send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {items.map((item, i) => (
              <Message
                key={item.id}
                item={item}
                last={i === items.length - 1 && !pending}
                busy={!!pending}
                conversationId={selected}
                onChoose={send}
                onCard={replaceCard}
              />
            ))}
            {pending && (
              <>
                <Message item={placeholder(pending)} last={false} busy conversationId={selected} onChoose={send} onCard={replaceCard} />
                <div className="flex items-center gap-2 text-sm text-muted" role="status">
                  <Spinner className="h-4 w-4" /> {status ?? "Thinking…"}
                </div>
              </>
            )}
            <div ref={bottom} />
          </div>

          <form
            className="border-t border-line p-3 sm:p-4"
            onSubmit={(e) => {
              e.preventDefault();
              send(draft);
            }}
          >
            {error && (
              <div className="mb-3">
                <Alert>{error}</Alert>
              </div>
            )}
            {paused ? (
              <Alert tone="warning">The assistant is paused for your account for a while after several messages it couldn't help with.</Alert>
            ) : (
              <div className="flex items-end gap-2">
                <Textarea
                  rows={2}
                  value={draft}
                  maxLength={MAX_CHARS}
                  placeholder="Book strength coaching with Sam next Tuesday at 10 for two hours"
                  aria-label="Message"
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send(draft);
                    }
                  }}
                  className="resize-none"
                />
                <Button type="submit" disabled={!draft.trim() || !!pending} aria-label="Send" className="h-10 shrink-0">
                  <Send className="h-4 w-4" />
                </Button>
              </div>
            )}
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
              <span>
                Prefer to book yourself?{" "}
                <Link to="/book" className={linkClass}>
                  Open the booking page
                </Link>
                <span className="mt-0.5 block">If you contact the admin, they can read this chat to help you.</span>
              </span>
              <span className="tabular">
                {draft.length}/{MAX_CHARS}
              </span>
            </div>
          </form>
        </section>
      </div>

      <p className="mt-4 text-right text-xs text-muted">
        <button className="underline-offset-2 hover:underline" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
          Cancel subscription (demo)
        </button>
      </p>
    </div>
  );
}

function placeholder(text: string): ChatItem {
  return { id: "pending", role: "user", text, choices: [], cards: [], paused: false, feedback: null, created_at: new Date().toISOString() };
}
