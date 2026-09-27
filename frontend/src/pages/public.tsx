import { useMutation, useQuery } from "@tanstack/react-query";
import type { FormEvent } from "react";
import { useState } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { Location, Resource, ResourceDetail, Service, ServiceDetail } from "../api/types";
import { RESOURCE_TYPES } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useConfig } from "../components/Layout";
import { ChevronRight, Mark } from "../components/icons";
import { Alert, BackLink, Badge, Button, Card, EmptyState, Field, Input, Loading, MapLink, PageHeader, Select, buttonClass, linkClass } from "../components/ui";
import { WEEKDAYS, formatLengthRange, formatRate, hhmm, titleCase } from "../lib/format";

export function HomePage() {
  const { user } = useAuth();
  const config = useConfig();
  const services = useQuery({ queryKey: ["services"], queryFn: () => api.get<Service[]>("/api/services") });
  const locations = useQuery({ queryKey: ["locations"], queryFn: () => api.get<Location[]>("/api/locations") });
  return (
    <div className="space-y-12 sm:space-y-16">
      <section className="max-w-2xl pt-2 sm:pt-8">
        <h1 className="text-3xl font-semibold tracking-tight text-fg sm:text-4xl">Book a time that suits you.</h1>
        <p className="mt-4 text-base text-muted sm:text-lg">
          Choose a service, then the person or place you want, or simply the time that works for you. Availability is live
          {config.data?.require_admin_confirmation ? ", and our team confirms each request." : ", and your booking is confirmed straight away."}
        </p>
        <div className="mt-7 flex flex-wrap gap-3">
          <Link to={user ? "/book" : "/register"} className={buttonClass("primary", "md", "h-10 px-5")}>
            {user ? "Book now" : "Get started"}
          </Link>
          <Link to="/services" className={buttonClass("secondary", "md", "h-10 px-5")}>
            Browse services
          </Link>
        </div>
      </section>
      <section>
        <div className="mb-3 flex items-baseline justify-between gap-4">
          <h2 className="text-base font-semibold text-fg">Services</h2>
          <Link to="/services" className={`text-sm ${linkClass}`}>
            See all
          </Link>
        </div>
        {services.isLoading ? <Loading /> : <ServiceList services={(services.data ?? []).slice(0, 6)} />}
      </section>
      {!!locations.data?.length && (
        <section>
          <h2 className="mb-3 text-base font-semibold text-fg">Where to find us</h2>
          <ul className="grid gap-3 sm:grid-cols-2">
            {locations.data.map((l) => (
              <li key={l.id} className="rounded-lg border border-line bg-surface px-4 py-3.5 sm:px-5">
                <div className="font-medium text-fg">{l.name}</div>
                {l.address && <div className="text-sm text-muted">{l.address}</div>}
                <MapLink url={l.google_maps_url} className="mt-2" />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** A bordered list whose rows are links. */
function LinkList({ children }: { children: React.ReactNode }) {
  return <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface">{children}</ul>;
}

function GroupBadge({ capacity }: { capacity: number | null }) {
  return <Badge>Group{capacity ? ` · ${capacity} places` : ""}</Badge>;
}

function ServiceList({ services }: { services: Service[] }) {
  if (!services.length) return <EmptyState title="No services match" />;
  return (
    <LinkList>
      {services.map((s) => (
        <li key={s.id}>
          <Link to={`/services/${s.id}`} className="flex items-center gap-4 px-4 py-4 hover:bg-subtle sm:px-5">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                <h3 className="font-medium text-fg">{s.name}</h3>
                {s.booking_type === "CAPACITY" && <GroupBadge capacity={s.capacity} />}
              </div>
              {s.description && <p className="mt-0.5 line-clamp-2 text-sm text-muted">{s.description}</p>}
            </div>
            <div className="shrink-0 text-right">
              {s.price && <div className="tabular text-sm font-medium text-fg">{formatRate(s.price)}</div>}
              <div className="tabular text-xs text-muted">{formatLengthRange(s.duration_minutes, s.max_duration_minutes)}</div>
            </div>
            <ChevronRight className="h-4 w-4 shrink-0 text-faint" />
          </Link>
        </li>
      ))}
    </LinkList>
  );
}

function Filters({ children }: { children: React.ReactNode }) {
  return <div className="mb-5 grid gap-2 sm:grid-cols-3">{children}</div>;
}

export function ServicesPage() {
  const [q, setQ] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [locationId, setLocationId] = useState("");
  const locations = useQuery({ queryKey: ["locations"], queryFn: () => api.get<Location[]>("/api/locations") });
  const services = useQuery({
    queryKey: ["services", q, maxPrice, locationId],
    queryFn: () => api.get<Service[]>("/api/services", { q, max_price: maxPrice, location_id: locationId }),
  });
  return (
    <>
      <PageHeader title="Services" subtitle="Everything you can book." />
      <Filters>
        <Input type="search" placeholder="Search services…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search services" />
        <Select value={locationId} onChange={(e) => setLocationId(e.target.value)} aria-label="Location">
          <option value="">All locations</option>
          {locations.data?.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </Select>
        <Input type="number" min={0} placeholder="Max price / h" value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} aria-label="Max price per hour" />
      </Filters>
      {services.isLoading ? <Loading /> : <ServiceList services={services.data ?? []} />}
    </>
  );
}

export function ServiceDetailPage() {
  const { id } = useParams();
  const config = useConfig();
  const service = useQuery({ queryKey: ["service", id], queryFn: () => api.get<ServiceDetail>(`/api/services/${id}`) });
  if (service.isLoading) return <Loading />;
  if (service.error) return <Alert>{errorMessage(service.error)}</Alert>;
  const s = service.data!;
  const waitlist = s.booking_type === "CAPACITY" && config.data?.allow_waitlist;
  return (
    <>
      <BackLink to="/services">Services</BackLink>
      <PageHeader
        title={s.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {formatLengthRange(s.duration_minutes, s.max_duration_minutes)}
            {s.price && <span>· from {formatRate(s.price)}</span>}
            {s.booking_type === "CAPACITY" && <GroupBadge capacity={s.capacity} />}
          </span>
        }
        actions={
          <Link to={`/book?service=${s.id}`} className={buttonClass()}>
            Book this service
          </Link>
        }
      />
      {(s.description || waitlist) && (
        <div className="-mt-2 mb-8 max-w-2xl space-y-2">
          {s.description && <p className="text-muted">{s.description}</p>}
          {waitlist && (
            <p className="text-sm text-muted">
              Full sessions have a waitlist: choose a full time when you book to join it, and we'll book you in if a place opens up.
            </p>
          )}
        </div>
      )}
      <h2 className="mb-3 text-base font-semibold text-fg">Available with</h2>
      {!s.resources.length ? (
        <EmptyState title="Not bookable right now" />
      ) : (
        <LinkList>
          {s.resources.map((o) => (
            <li key={o.resource_id} className="flex items-center gap-4 px-4 py-3.5 sm:px-5">
              <div className="min-w-0 flex-1">
                <Link to={`/resources/${o.resource_id}`} className="font-medium text-fg hover:underline">
                  {o.resource_name}
                </Link>
                <div className="text-sm text-muted">
                  {titleCase(o.resource_type)}
                  {o.location_name && ` · ${o.location_name}`} · {formatLengthRange(o.duration_minutes, o.max_duration_minutes)}
                  {o.price && ` · ${formatRate(o.price)}`}
                </div>
              </div>
              <Link to={`/book?service=${s.id}&resource=${o.resource_id}`} className={buttonClass("secondary", "sm")}>
                Book
              </Link>
            </li>
          ))}
        </LinkList>
      )}
    </>
  );
}

export function ResourcesPage() {
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [locationId, setLocationId] = useState("");
  const locations = useQuery({ queryKey: ["locations"], queryFn: () => api.get<Location[]>("/api/locations") });
  const resources = useQuery({
    queryKey: ["resources", q, type, locationId],
    queryFn: () => api.get<Resource[]>("/api/resources", { q, type, location_id: locationId }),
  });
  return (
    <>
      <PageHeader title="Resources" subtitle="People, rooms, facilities and equipment you can reserve." />
      <Filters>
        <Input type="search" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search resources" />
        <Select value={type} onChange={(e) => setType(e.target.value)} aria-label="Type">
          <option value="">All types</option>
          {RESOURCE_TYPES.map((t) => (
            <option key={t} value={t}>
              {titleCase(t)}
            </option>
          ))}
        </Select>
        <Select value={locationId} onChange={(e) => setLocationId(e.target.value)} aria-label="Location">
          <option value="">All locations</option>
          {locations.data?.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </Select>
      </Filters>
      {resources.isLoading ? (
        <Loading />
      ) : !resources.data?.length ? (
        <EmptyState title="No resources match your filters" />
      ) : (
        <LinkList>
          {resources.data.map((r) => (
            <li key={r.id}>
              <Link to={`/resources/${r.id}`} className="flex items-center gap-4 px-4 py-4 hover:bg-subtle sm:px-5">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                    <h3 className="font-medium text-fg">{r.name}</h3>
                    <Badge>{titleCase(r.type)}</Badge>
                  </div>
                  {r.description && <p className="mt-0.5 line-clamp-2 text-sm text-muted">{r.description}</p>}
                  <p className="mt-1 text-xs text-muted">
                    {r.location?.name ?? "No location"}
                    {r.capacity && ` · capacity ${r.capacity}`}
                  </p>
                </div>
                <ChevronRight className="h-4 w-4 shrink-0 text-faint" />
              </Link>
            </li>
          ))}
        </LinkList>
      )}
    </>
  );
}

function MetadataList({ metadata }: { metadata: Record<string, unknown> }) {
  const entries = Object.entries(metadata);
  if (!entries.length) return null;
  return (
    <dl className="space-y-2 text-sm">
      {entries.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-4">
          <dt className="text-muted">{titleCase(k)}</dt>
          <dd className="text-right font-medium text-fg">{typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ResourceDetailPage() {
  const { id } = useParams();
  const resource = useQuery({ queryKey: ["resource", id], queryFn: () => api.get<ResourceDetail>(`/api/resources/${id}`) });
  if (resource.isLoading) return <Loading />;
  if (resource.error) return <Alert>{errorMessage(resource.error)}</Alert>;
  const r = resource.data!;
  const byDay = WEEKDAYS.map((_, d) => r.availability.filter((a) => a.day_of_week === d && a.is_available));
  return (
    <>
      <BackLink to="/resources">Resources</BackLink>
      <PageHeader title={r.name} subtitle={`${titleCase(r.type)}${r.location ? ` · ${r.location.name}` : ""} · ${r.timezone}`} />
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {r.description && <p className="-mt-2 text-muted">{r.description}</p>}
          <section>
            <h2 className="mb-3 text-base font-semibold text-fg">Services</h2>
            {!r.services.length ? (
              <EmptyState title="Nothing to book here yet" />
            ) : (
              <LinkList>
                {r.services.map((o) => (
                  <li key={o.service_id} className="flex items-center gap-4 px-4 py-3.5 sm:px-5">
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-fg">{o.service_name}</div>
                      <div className="text-sm text-muted">
                        {formatLengthRange(o.duration_minutes, o.max_duration_minutes)}
                        {o.price && ` · ${formatRate(o.price)}`}
                        {o.booking_type === "CAPACITY" && " · group session"}
                      </div>
                    </div>
                    <Link to={`/book?service=${o.service_id}&resource=${r.id}`} className={buttonClass("primary", "sm")}>
                      Book
                    </Link>
                  </li>
                ))}
              </LinkList>
            )}
          </section>
        </div>
        <div className="space-y-4">
          {r.location && (
            <Card>
              <h2 className="mb-2 text-sm font-semibold text-fg">Location</h2>
              <p className="text-sm text-fg">{r.location.name}</p>
              {r.location.address && <p className="text-sm text-muted">{r.location.address}</p>}
              <MapLink url={r.location.google_maps_url} className="mt-2" />
            </Card>
          )}
          <Card>
            <h2 className="mb-3 text-sm font-semibold text-fg">Weekly hours</h2>
            {r.availability.length === 0 ? (
              <p className="text-sm text-muted">Available during business hours.</p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {byDay.map((rules, d) => (
                  <li key={d} className="flex justify-between gap-4">
                    <span className="text-muted">{WEEKDAYS[d]}</span>
                    <span className="tabular text-right text-fg">
                      {rules.length === 0 ? <span className="text-faint">Closed</span> : rules.map((x) => `${hhmm(x.start_time)}–${hhmm(x.end_time)}`).join(", ")}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          {Object.keys(r.metadata).length > 0 && (
            <Card>
              <h2 className="mb-3 text-sm font-semibold text-fg">Details</h2>
              <MetadataList metadata={r.metadata} />
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- auth pages

function AuthCard({ title, children, footer }: { title: string; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-sm pt-2 sm:pt-10">
      <div className="mb-6 text-center">
        <Mark className="mx-auto h-8 w-8" />
        <h1 className="mt-4 text-xl font-semibold tracking-tight text-fg">{title}</h1>
      </div>
      <Card className="sm:p-6">{children}</Card>
      {footer && <p className="mt-5 text-center text-sm text-muted">{footer}</p>}
    </div>
  );
}

export function LoginPage() {
  const { login, user, isStaff } = useAuth();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const mutation = useMutation({ mutationFn: () => login(email, password) });
  // Once signed in (now or earlier), go where the user was heading.
  const next = params.get("next");
  if (user) return <Navigate to={next && next.startsWith("/") && !next.startsWith("//") ? next : isStaff ? "/admin" : "/dashboard"} replace />;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    mutation.mutate();
  };
  return (
    <AuthCard title="Sign in" footer={<>No account? <Link to="/register" className={linkClass}>Create one</Link></>}>
      <form onSubmit={submit} className="space-y-4">
        {mutation.error && <Alert>{errorMessage(mutation.error)}</Alert>}
        <Field label="E-mail">
          <Input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password">
          <Input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Button type="submit" className="w-full" loading={mutation.isPending}>
          Sign in
        </Button>
        <Link to="/forgot-password" className="block text-center text-sm text-muted hover:text-fg">
          Forgot your password?
        </Link>
      </form>
    </AuthCard>
  );
}

export function RegisterPage() {
  const { register, user } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", phone: "", password: "" });
  const mutation = useMutation({ mutationFn: () => register({ ...form, phone: form.phone || undefined }), onSuccess: () => navigate("/dashboard") });
  if (user) return <Navigate to="/dashboard" replace />;
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  return (
    <AuthCard title="Create your account" footer={<>Already registered? <Link to="/login" className={linkClass}>Sign in</Link></>}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate();
        }}
        className="space-y-4"
      >
        {mutation.error && <Alert>{errorMessage(mutation.error)}</Alert>}
        <Field label="Name">
          <Input required autoComplete="name" value={form.name} onChange={set("name")} />
        </Field>
        <Field label="E-mail">
          <Input type="email" required autoComplete="email" value={form.email} onChange={set("email")} />
        </Field>
        <Field label="Phone (optional)">
          <Input type="tel" autoComplete="tel" value={form.phone} onChange={set("phone")} />
        </Field>
        <Field label="Password" hint="At least 8 characters.">
          <Input type="password" required minLength={8} autoComplete="new-password" value={form.password} onChange={set("password")} />
        </Field>
        <Button type="submit" className="w-full" loading={mutation.isPending}>
          Create account
        </Button>
      </form>
    </AuthCard>
  );
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const mutation = useMutation({ mutationFn: () => api.post<{ message: string }>("/api/auth/forgot-password", { email }) });
  return (
    <AuthCard title="Reset your password" footer={<Link to="/login" className={linkClass}>Back to sign in</Link>}>
      {mutation.isSuccess ? (
        <Alert tone="success">{mutation.data.message}</Alert>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
          className="space-y-4"
        >
          {mutation.error && <Alert>{errorMessage(mutation.error)}</Alert>}
          <Field label="E-mail">
            <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Button type="submit" className="w-full" loading={mutation.isPending}>
            Send reset link
          </Button>
        </form>
      )}
    </AuthCard>
  );
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const [password, setPassword] = useState("");
  const token = params.get("token") ?? "";
  const mutation = useMutation({ mutationFn: () => api.post<{ message: string }>("/api/auth/reset-password", { token, password }) });
  return (
    <AuthCard title="Choose a new password">
      {mutation.isSuccess ? (
        <div className="space-y-4">
          <Alert tone="success">{mutation.data.message}</Alert>
          <Link to="/login" className={`block text-center text-sm ${linkClass}`}>
            Sign in
          </Link>
        </div>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
          className="space-y-4"
        >
          {!token && <Alert>This link is missing its token.</Alert>}
          {mutation.error && <Alert>{errorMessage(mutation.error)}</Alert>}
          <Field label="New password" hint="At least 8 characters.">
            <Input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Button type="submit" className="w-full" disabled={!token} loading={mutation.isPending}>
            Set password
          </Button>
        </form>
      )}
    </AuthCard>
  );
}
