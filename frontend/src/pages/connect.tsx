import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, api, errorMessage } from "../api/client";
import type { AgentConnection, AgentConnections, OAuthRequestInfo } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Check, Lock } from "../components/icons";
import { Alert, Badge, Button, Card, Checkbox, EmptyState, Field, Input, Loading, Modal, PageHeader, Select, Tabs, linkClass } from "../components/ui";
import { formatMoney, formatStamp } from "../lib/format";

// Not a charge: a cap on what an assistant may book in total, on top of the price of each booking.
const SPEND_HINT =
  "The most this app may book for you in 7 days, all bookings added up. Each booking still has its own price (and payment); over the limit, you book on this website.";

function limitLabel(value: string | null, currency: string): string {
  return value === null ? "No limit" : `${formatMoney(value)} ${currency} a week`;
}

function LimitSelect({ value, onChange, limits, currency, id }: { value: string | null; onChange: (v: string | null) => void; limits: (string | null)[]; currency: string; id?: string }) {
  return (
    <Select id={id} value={value ?? "none"} onChange={(e) => onChange(e.target.value === "none" ? null : e.target.value)}>
      {limits.map((v) => (
        <option key={v ?? "none"} value={v ?? "none"}>
          {limitLabel(v, currency)}
        </option>
      ))}
    </Select>
  );
}

/** Whole amounts as the API knows them ("100" for 100.00). */
function plain(value: string | null): string | null {
  return value === null ? null : String(Number(value));
}

// ---------------------------------------------------------------- the consent page

/** Where an AI app (Claude, ChatGPT...) sends the customer to ask for access.
 * The app never sees the password: the customer signs in here and approves. */
export function OAuthAuthorizePage() {
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { logout } = useAuth();
  const query = Object.fromEntries(params.entries());
  const info = useQuery({
    queryKey: ["oauth", "authorize", params.toString()],
    queryFn: () => api.get<OAuthRequestInfo>("/api/oauth/authorize/info", query),
    retry: false,
  });
  const [allowChanges, setAllowChanges] = useState(true);
  const [limit, setLimit] = useState<string | null | undefined>(undefined);
  const answer = useMutation({
    mutationFn: (approve: boolean) =>
      api.post<{ redirect: string }>("/api/oauth/authorize", {
        ...query,
        approve,
        allow_changes: allowChanges && !!info.data?.asks_for_changes,
        weekly_spend_limit: limit === undefined ? info.data?.default_spend_limit : limit,
      }),
    // Back to the app, with the one-time code (or the refusal).
    onSuccess: ({ redirect }) => window.location.assign(redirect),
  });
  const signInAgain = async () => {
    await logout();
    navigate(`/login?next=${encodeURIComponent(location.pathname + location.search)}`, { replace: true });
  };

  if (info.isLoading) return <Loading />;
  if (info.error) {
    const paywall = info.error instanceof ApiError && info.error.status === 402;
    return (
      <div className="mx-auto max-w-lg space-y-4">
        <PageHeader title="Connect an app" />
        {paywall ? (
          <Alert tone="info">
            Connecting AI assistants is part of the booking assistant.{" "}
            <Link to="/chat/upgrade" className="font-medium underline">
              Get the assistant
            </Link>{" "}
            first, then try connecting again from the app.
          </Alert>
        ) : (
          <Alert>{errorMessage(info.error)}</Alert>
        )}
      </div>
    );
  }
  const i = info.data!;
  const chosenLimit = limit === undefined ? i.default_spend_limit : limit;
  const needsSignIn = !i.signed_in_recently || (answer.error instanceof ApiError && answer.error.code === "SIGN_IN_AGAIN");

  return (
    <div className="mx-auto max-w-lg">
      <PageHeader title={`Connect ${i.app}`} subtitle={`to your account at ${i.business}`} />
      <Card>
        <p className="text-sm text-fg">
          <span className="font-semibold">{i.app}</span> wants to use your account. After you allow it, you'll go back to{" "}
          <span className="font-semibold">{i.return_host}</span>.
        </p>
        <p className="mt-1 text-xs text-muted">If you don't recognise {i.return_host}, or didn't just ask to connect an app, cancel.</p>
        <h2 className="mt-5 text-sm font-semibold text-fg">It will be able to</h2>
        <ul className="mt-2 space-y-1.5 text-sm text-fg">
          {i.scopes
            .filter((s) => s.scope !== "bookings:write")
            .map((s) => (
              <li key={s.scope} className="flex gap-2">
                <Check className="mt-0.5 h-4 w-4 shrink-0 text-accent-text" />
                {s.description}
              </li>
            ))}
        </ul>
        {i.asks_for_changes && (
          <div className="mt-3 rounded-md border border-line p-3">
            <Checkbox
              label="Hold, move and cancel bookings, and contact the admin, for me"
              checked={allowChanges}
              onChange={(e) => setAllowChanges(e.target.checked)}
            />
            <p className="mt-1 pl-6 text-xs text-muted">
              It reads each change and its price back to you and only goes ahead when you say yes. Every booking, move and cancellation is e-mailed to you.
            </p>
            {allowChanges && (
              <div className="mt-3 pl-6">
                <Field label="Spending limit" hint={SPEND_HINT}>
                  <LimitSelect value={plain(chosenLimit)} onChange={setLimit} limits={i.spend_limits.map(plain)} currency={i.currency} />
                </Field>
              </div>
            )}
          </div>
        )}
        <p className="mt-4 flex gap-2 text-xs text-muted">
          <Lock className="h-4 w-4 shrink-0" />
          It never sees your password or card details, can't see other customers, and can't change your account. Disconnect it any time under Connected apps.
        </p>
        {answer.error && !needsSignIn && (
          <div className="mt-4">
            <Alert>{errorMessage(answer.error)}</Alert>
          </div>
        )}
        {needsSignIn ? (
          <div className="mt-5 space-y-3">
            <Alert tone="info">For your security, sign in again to connect an app.</Alert>
            <Button onClick={signInAgain}>Sign in again</Button>
          </div>
        ) : (
          <div className="mt-5 flex gap-2">
            <Button loading={answer.isPending && answer.variables} disabled={answer.isPending} onClick={() => answer.mutate(true)}>
              Allow
            </Button>
            <Button variant="secondary" loading={answer.isPending && !answer.variables} disabled={answer.isPending} onClick={() => answer.mutate(false)}>
              Cancel
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- Connected apps

type Guide = "claude" | "chatgpt" | "gemini" | "siri" | "muse";

function Steps({ guide, url }: { guide: Guide; url: string }) {
  const steps: Record<Guide, string[]> = {
    claude: [
      "In Claude, open Settings → Connectors → Add custom connector.",
      `Paste ${url} as the URL and press Add.`,
      "Claude sends you to this website: sign in and press Allow.",
    ],
    chatgpt: [
      "In ChatGPT, open Settings → Apps & Connectors and add a custom connector (developer mode).",
      `Paste ${url} as the MCP server URL, with OAuth.`,
      "Sign in here when ChatGPT asks, and press Allow.",
    ],
    gemini: [
      "Gemini connects custom MCP servers in Gemini Enterprise (an admin adds it) and in the Gemini CLI.",
      `Use ${url} with OAuth; you sign in here and press Allow.`,
      "Google Assistant itself takes no custom connectors any more: use Gemini.",
    ],
    siri: [
      "Siri has no connectors yet, so use a Shortcut: create an access token below (allow changes only if you need them).",
      `In Shortcuts, add “Get contents of URL”: POST to ${url}, header Authorization: Bearer <your token>, JSON body {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_my_bookings","arguments":{}}}.`,
      "Name the Shortcut (“My bookings”) and say “Hey Siri, my bookings”. Keep “Run when locked” off.",
    ],
    muse: [
      "Create an access token below.",
      `In Meta Muse, add a custom connector for ${url} and put the token in its credential store, not in the chat.`,
      "Ask Muse about your bookings; it reads each change back to you before confirming.",
    ],
  };
  return (
    <ol className="list-decimal space-y-1.5 pl-5 text-sm text-fg">
      {steps[guide].map((s) => (
        <li key={s} className="break-words">
          {s}
        </li>
      ))}
    </ol>
  );
}

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="sm"
      variant="secondary"
      onClick={async () => {
        await navigator.clipboard?.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? "Copied" : label}
    </Button>
  );
}

function ConnectionCard({ c, data }: { c: AgentConnection; data: AgentConnections }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["connections"] });
  const update = useMutation({ mutationFn: (body: object) => api.put(`/api/connections/${c.id}`, body), onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => api.del(`/api/connections/${c.id}`), onSuccess: refresh });
  const [showAll, setShowAll] = useState(false);
  const activity = showAll ? c.activity : c.activity.slice(0, 4);

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 font-semibold text-fg">
            {c.name}
            <Badge>{c.kind === "OAUTH" ? "App" : "Access token"}</Badge>
            <Badge tone={c.allow_changes ? "accent" : "neutral"}>{c.allow_changes ? "Can make changes" : "Read only"}</Badge>
          </p>
          <p className="mt-0.5 text-xs text-muted">
            {c.return_host ? `${c.return_host} · ` : ""}Connected {formatStamp(c.created_at)}
            {c.last_used_at ? ` · last used ${formatStamp(c.last_used_at)}` : " · not used yet"}
            {c.expires_at ? ` · expires ${formatStamp(c.expires_at)}` : ""}
          </p>
        </div>
        <Button size="sm" variant="danger" onClick={() => setConfirming(true)}>
          Disconnect
        </Button>
      </div>
      {c.allow_changes && (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="text-sm">
            <p className="text-muted">Booked through it this week</p>
            <p className="tabular font-semibold text-fg">
              {formatMoney(c.spent_this_week)} {data.currency}
              {c.weekly_spend_limit !== null && <span className="font-normal text-muted"> of {formatMoney(c.weekly_spend_limit)}</span>}
            </p>
          </div>
          <Field label="Spending limit" hint={SPEND_HINT}>
            <LimitSelect
              value={plain(c.weekly_spend_limit)}
              limits={data.spend_limits.map(plain)}
              currency={data.currency}
              onChange={(v) => update.mutate({ weekly_spend_limit: v })}
            />
          </Field>
        </div>
      )}
      {update.error && (
        <div className="mt-3">
          <Alert>{errorMessage(update.error)}</Alert>
        </div>
      )}
      <div className="mt-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted">What it did</h3>
        {c.activity.length === 0 ? (
          <p className="mt-1 text-sm text-muted">Nothing changed yet.</p>
        ) : (
          <ul className="mt-1 divide-y divide-line text-sm">
            {activity.map((a) => (
              <li key={a.at + a.text} className="flex flex-wrap justify-between gap-x-3 py-1.5">
                <span className="text-fg">{a.text}</span>
                <span className="tabular text-xs text-muted">{formatStamp(a.at)}</span>
              </li>
            ))}
          </ul>
        )}
        {c.activity.length > 4 && (
          <button className={`mt-1 text-sm ${linkClass}`} onClick={() => setShowAll((s) => !s)}>
            {showAll ? "Show less" : "Show all"}
          </button>
        )}
      </div>
      {c.allow_changes && (
        <button className={`mt-3 text-sm ${linkClass}`} onClick={() => update.mutate({ allow_changes: false })}>
          Make it read only
        </button>
      )}
      <Modal
        open={confirming}
        title={`Disconnect ${c.name}?`}
        onClose={() => setConfirming(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
              Disconnect
            </Button>
          </>
        }
      >
        <p className="text-sm text-fg">It stops working at once. Anything it holds for you that you haven't confirmed is released.</p>
      </Modal>
    </Card>
  );
}

function CreateToken({ data }: { data: AgentConnections }) {
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();
  const { logout } = useAuth();
  const [name, setName] = useState("");
  const [allowChanges, setAllowChanges] = useState(false);
  const [days, setDays] = useState(90);
  const [limit, setLimit] = useState<string | null>("100");
  const [made, setMade] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api.post<{ token: string }>("/api/connections/tokens", { name, allow_changes: allowChanges, days, weekly_spend_limit: limit }),
    onSuccess: ({ token }) => {
      setMade(token);
      setName("");
      queryClient.invalidateQueries({ queryKey: ["connections"] });
    },
  });
  const signInAgain = create.error instanceof ApiError && create.error.code === "SIGN_IN_AGAIN";
  const paywall = create.error instanceof ApiError && create.error.status === 402;

  return (
    <Card>
      <h2 className="text-sm font-semibold text-fg">Access token</h2>
      <p className="mt-1 text-sm text-muted">For assistants that can't sign in here themselves: a Siri Shortcut, Meta Muse, a script. Treat it like a password.</p>
      {made ? (
        <div className="mt-4 space-y-3">
          <Alert tone="warning">Copy it now: it is shown only once. Anyone with it can use your account within what you allowed.</Alert>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-md bg-subtle px-3 py-2 text-xs text-fg">{made}</code>
            <CopyButton text={made} />
          </div>
          <Button variant="secondary" onClick={() => setMade(null)}>
            Done
          </Button>
        </div>
      ) : (
        <form
          className="mt-4 grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label="Name">
            <Input value={name} maxLength={60} required placeholder="Siri Shortcut" onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Expires after">
            <Select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {data.token_days.map((d) => (
                <option key={d} value={d}>
                  {d} days
                </option>
              ))}
            </Select>
          </Field>
          <div className="sm:col-span-2">
            <Checkbox label="Let it hold, move and cancel bookings for me" checked={allowChanges} onChange={(e) => setAllowChanges(e.target.checked)} />
          </div>
          {allowChanges && (
            <Field label="Spending limit" hint={SPEND_HINT}>
              <LimitSelect value={limit} onChange={setLimit} limits={data.spend_limits.map(plain)} currency={data.currency} />
            </Field>
          )}
          <div className="sm:col-span-2">
            {signInAgain ? (
              <div className="space-y-3">
                <Alert tone="info">For your security, sign in again to make an access token.</Alert>
                <Button
                  onClick={async () => {
                    await logout();
                    navigate(`/login?next=${encodeURIComponent(location.pathname)}`, { replace: true });
                  }}
                >
                  Sign in again
                </Button>
              </div>
            ) : paywall ? (
              <Alert tone="info">
                Access tokens are part of the booking assistant.{" "}
                <Link to="/chat/upgrade" className="font-medium underline">
                  Get the assistant
                </Link>
              </Alert>
            ) : (
              <>
                {create.error && (
                  <div className="mb-3">
                    <Alert>{errorMessage(create.error)}</Alert>
                  </div>
                )}
                <Button type="submit" loading={create.isPending} disabled={!name.trim()}>
                  Create token
                </Button>
              </>
            )}
          </div>
        </form>
      )}
    </Card>
  );
}

/** The AI assistants that can use this account, what they did, and how to connect one. */
export function ConnectionsPage() {
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => api.get<AgentConnections>("/api/connections") });
  const [guide, setGuide] = useState<Guide>("claude");
  if (connections.isLoading) return <Loading />;
  if (connections.error) return <Alert>{errorMessage(connections.error)}</Alert>;
  const data = connections.data!;

  return (
    <>
      <PageHeader
        title="Connected apps"
        subtitle="Book, move and cancel from your own AI assistant: Claude, ChatGPT, Gemini, Siri or Meta Muse. They only act for you, through a few safe actions, and ask before every change."
      />
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {data.connections.length === 0 ? (
            <EmptyState title="No apps connected">Connect one with the steps on this page.</EmptyState>
          ) : (
            data.connections.map((c) => <ConnectionCard key={c.id} c={c} data={data} />)
          )}
          <CreateToken data={data} />
          {data.disconnected.length > 0 && (
            <div className="text-xs text-muted">
              Disconnected: {data.disconnected.map((c) => `${c.name} (${formatStamp(c.revoked_at!)})`).join(", ")}
            </div>
          )}
        </div>
        <Card className="self-start">
          <h2 className="text-sm font-semibold text-fg">How to connect</h2>
          <p className="mt-2 text-xs text-muted">The connector address</p>
          <div className="mt-1 flex items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-md bg-subtle px-2 py-1.5 text-xs text-fg">{data.mcp_url}</code>
            <CopyButton text={data.mcp_url} />
          </div>
          <div className="mt-4">
            <Tabs
              value={guide}
              onChange={setGuide}
              tabs={[
                { value: "claude", label: "Claude" },
                { value: "chatgpt", label: "ChatGPT" },
                { value: "gemini", label: "Gemini" },
                { value: "siri", label: "Siri" },
                { value: "muse", label: "Muse" },
              ]}
            />
            <Steps guide={guide} url={data.mcp_url} />
          </div>
          <p className="mt-4 text-xs text-muted">
            Prices are read back to you before anything is booked, and paying happens only on this website.
          </p>
        </Card>
      </div>
    </>
  );
}
