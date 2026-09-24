import { useMutation, useQuery } from "@tanstack/react-query";
import type { FormEvent } from "react";
import { useState } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { Location, Resource, ResourceDetail, Service, ServiceDetail } from "../api/types";
import { RESOURCE_TYPES } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useConfig } from "../components/Layout";
import { Alert, Badge, Button, Card, EmptyState, Field, Input, Loading, PageHeader, Select } from "../components/ui";
import { WEEKDAYS, formatDuration, formatMoney, hhmm, titleCase } from "../lib/format";

export function HomePage() {
  const { user } = useAuth();
  const config = useConfig();
  const services = useQuery({ queryKey: ["services"], queryFn: () => api.get<Service[]>("/api/services") });
  return (
    <div className="space-y-12">
      <section className="rounded-2xl bg-gradient-to-br from-brand-600 to-brand-700 px-8 py-14 text-white shadow-lg">
        <h1 className="max-w-2xl text-4xl font-semibold tracking-tight">Book {config.data?.business_name ?? "your next appointment"} in seconds</h1>
        <p className="mt-3 max-w-xl text-brand-100">
          Pick a service, choose a person, room or facility, and grab a time that works. Real-time availability, instant confirmation.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link to={user ? "/book" : "/register"} className="rounded-lg bg-white px-5 py-2.5 text-sm font-semibold text-brand-700 hover:bg-brand-50">
            {user ? "Book now" : "Get started"}
          </Link>
          <Link to="/services" className="rounded-lg px-5 py-2.5 text-sm font-semibold text-white ring-1 ring-white/40 hover:bg-white/10">
            Browse services
          </Link>
        </div>
      </section>
      <section>
        <h2 className="mb-4 text-lg font-semibold">Popular services</h2>
        {services.isLoading ? <Loading /> : <ServiceGrid services={(services.data ?? []).slice(0, 6)} />}
      </section>
    </div>
  );
}

function ServiceGrid({ services }: { services: Service[] }) {
  if (!services.length) return <EmptyState title="No services yet" />;
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {services.map((s) => (
        <Link key={s.id} to={`/services/${s.id}`} className="group">
          <Card className="h-full transition group-hover:border-brand-300 group-hover:shadow-md">
            <div className="flex items-start justify-between gap-3">
              <h3 className="font-semibold text-slate-900">{s.name}</h3>
              {s.booking_type === "CAPACITY" && <Badge tone="WAITLISTED">Group · {s.capacity}</Badge>}
            </div>
            <p className="mt-2 line-clamp-2 text-sm text-slate-500">{s.description}</p>
            <div className="mt-4 flex gap-4 text-sm text-slate-600">
              <span>{formatDuration(s.duration_minutes)}</span>
              {s.price && <span>{formatMoney(s.price)}</span>}
            </div>
          </Card>
        </Link>
      ))}
    </div>
  );
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
      <div className="mb-6 grid gap-3 sm:grid-cols-3">
        <Input placeholder="Search services…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search services" />
        <Select value={locationId} onChange={(e) => setLocationId(e.target.value)} aria-label="Location">
          <option value="">All locations</option>
          {locations.data?.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </Select>
        <Input type="number" min={0} placeholder="Max price" value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} aria-label="Max price" />
      </div>
      {services.isLoading ? <Loading /> : <ServiceGrid services={services.data ?? []} />}
    </>
  );
}

export function ServiceDetailPage() {
  const { id } = useParams();
  const service = useQuery({ queryKey: ["service", id], queryFn: () => api.get<ServiceDetail>(`/api/services/${id}`) });
  if (service.isLoading) return <Loading />;
  if (service.error) return <Alert>{errorMessage(service.error)}</Alert>;
  const s = service.data!;
  return (
    <>
      <PageHeader
        title={s.name}
        subtitle={`${formatDuration(s.duration_minutes)}${s.price ? ` · from ${formatMoney(s.price)}` : ""}`}
        actions={<Link to={`/book?service=${s.id}`} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">Book this service</Link>}
      />
      {s.description && <p className="mb-6 max-w-2xl text-slate-600">{s.description}</p>}
      <h2 className="mb-3 font-semibold">Available with</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {s.resources.map((o) => (
          <Card key={o.resource_id} className="flex items-center justify-between gap-4">
            <div>
              <Link to={`/resources/${o.resource_id}`} className="font-medium hover:text-brand-700">
                {o.resource_name}
              </Link>
              <div className="text-sm text-slate-500">
                {titleCase(o.resource_type)}
                {o.location_name && ` · ${o.location_name}`} · {formatDuration(o.duration_minutes)} · {formatMoney(o.price)}
              </div>
            </div>
            <Link to={`/book?service=${s.id}&resource=${o.resource_id}`} className="text-sm font-medium text-brand-700 hover:underline">
              Book
            </Link>
          </Card>
        ))}
      </div>
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
      <div className="mb-6 grid gap-3 sm:grid-cols-3">
        <Input placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search resources" />
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
      </div>
      {resources.isLoading ? (
        <Loading />
      ) : !resources.data?.length ? (
        <EmptyState title="No resources match your filters" />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {resources.data.map((r) => (
            <Link key={r.id} to={`/resources/${r.id}`} className="group">
              <Card className="h-full transition group-hover:border-brand-300 group-hover:shadow-md">
                <div className="flex items-start justify-between gap-2">
                  <h3 className="font-semibold">{r.name}</h3>
                  <Badge>{titleCase(r.type)}</Badge>
                </div>
                {r.description && <p className="mt-2 line-clamp-2 text-sm text-slate-500">{r.description}</p>}
                <div className="mt-3 text-sm text-slate-500">
                  {r.location?.name ?? "No location"}
                  {r.capacity && ` · capacity ${r.capacity}`}
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

function MetadataList({ metadata }: { metadata: Record<string, unknown> }) {
  const entries = Object.entries(metadata);
  if (!entries.length) return null;
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
      {entries.map(([k, v]) => (
        <div key={k}>
          <dt className="text-slate-500">{titleCase(k)}</dt>
          <dd className="font-medium text-slate-800">{typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)}</dd>
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
  const byDay = WEEKDAYS.map((_, d) => r.availability.filter((a) => a.day_of_week === d));
  return (
    <>
      <PageHeader title={r.name} subtitle={`${titleCase(r.type)}${r.location ? ` · ${r.location.name}` : ""} · ${r.timezone}`} />
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {r.description && <p className="text-slate-600">{r.description}</p>}
          <Card>
            <h2 className="mb-3 font-semibold">Services</h2>
            <div className="divide-y divide-slate-100">
              {r.services.map((o) => (
                <div key={o.service_id} className="flex items-center justify-between py-3">
                  <div>
                    <div className="font-medium">{o.service_name}</div>
                    <div className="text-sm text-slate-500">
                      {formatDuration(o.duration_minutes)} · {formatMoney(o.price)}
                      {o.booking_type === "CAPACITY" && " · group session"}
                    </div>
                  </div>
                  <Link to={`/book?service=${o.service_id}&resource=${r.id}`} className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700">
                    Book
                  </Link>
                </div>
              ))}
            </div>
          </Card>
        </div>
        <div className="space-y-6">
          <Card>
            <h2 className="mb-3 font-semibold">Weekly hours</h2>
            {r.availability.length === 0 ? (
              <p className="text-sm text-slate-500">Available during business hours.</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {byDay.map((rules, d) => (
                  <li key={d} className="flex justify-between gap-4">
                    <span className="text-slate-500">{WEEKDAYS[d]}</span>
                    <span className="text-right">
                      {rules.filter((x) => x.is_available).length === 0
                        ? "Unavailable"
                        : rules
                            .filter((x) => x.is_available)
                            .map((x) => `${hhmm(x.start_time)}–${hhmm(x.end_time)}`)
                            .join(", ")}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          {Object.keys(r.metadata).length > 0 && (
            <Card>
              <h2 className="mb-3 font-semibold">Details</h2>
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
    <div className="mx-auto mt-6 max-w-md">
      <Card className="p-8">
        <h1 className="mb-6 text-xl font-semibold">{title}</h1>
        {children}
      </Card>
      {footer && <p className="mt-4 text-center text-sm text-slate-500">{footer}</p>}
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
    <AuthCard title="Sign in" footer={<>No account? <Link to="/register" className="text-brand-700 hover:underline">Create one</Link></>}>
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
        <Link to="/forgot-password" className="block text-center text-sm text-slate-500 hover:text-slate-700">
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
    <AuthCard title="Create your account" footer={<>Already registered? <Link to="/login" className="text-brand-700 hover:underline">Sign in</Link></>}>
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
    <AuthCard title="Reset your password" footer={<Link to="/login" className="text-brand-700 hover:underline">Back to sign in</Link>}>
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
          <Link to="/login" className="block text-center text-sm text-brand-700 hover:underline">
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
