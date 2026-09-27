import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, errorMessage } from "../../api/client";
import type { AvailabilityRule, Location, Offering, Resource, ResourceDetail, ScheduleChange, Service } from "../../api/types";
import { RESOURCE_TYPES } from "../../api/types";
import { useConfig } from "../../components/Layout";
import { Close } from "../../components/icons";
import { Alert, Badge, Button, Checkbox, EmptyState, Field, Input, Loading, MapLink, Modal, PageHeader, Select, StatusBadge, Table, Tabs, Td, Textarea } from "../../components/ui";
import { WEEKDAYS, formatLengthRange, formatRate, hhmm, titleCase } from "../../lib/format";
import { useCatalog } from "./bookings";
import { useScheduleChange } from "./scheduleChange";

const numOrNull = (v: string) => (v === "" ? null : Number(v));
const strOrNull = (v: string) => (v === "" ? null : v);

// ---------------------------------------------------------------- metadata editor

type Pair = { key: string; value: string };

function toPairs(metadata: Record<string, unknown>): Pair[] {
  return Object.entries(metadata).map(([key, value]) => ({ key, value: typeof value === "string" ? value : JSON.stringify(value) }));
}

function fromPairs(pairs: Pair[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const { key, value } of pairs) {
    if (!key.trim()) continue;
    // Numbers / booleans / JSON are kept typed; anything else is a string.
    try {
      out[key.trim()] = JSON.parse(value);
    } catch {
      out[key.trim()] = value;
    }
  }
  return out;
}

function MetadataEditor({ pairs, onChange }: { pairs: Pair[]; onChange: (p: Pair[]) => void }) {
  return (
    <div className="space-y-2">
      {pairs.map((p, i) => (
        <div key={i} className="flex gap-2">
          <Input placeholder="key (e.g. specialization)" value={p.key} onChange={(e) => onChange(pairs.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} />
          <Input placeholder="value (e.g. Strength, 8, true)" value={p.value} onChange={(e) => onChange(pairs.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
          <Button variant="ghost" onClick={() => onChange(pairs.filter((_, j) => j !== i))} aria-label="Remove">
            <Close />
          </Button>
        </div>
      ))}
      <Button variant="secondary" size="sm" onClick={() => onChange([...pairs, { key: "", value: "" }])}>
        Add attribute
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------- resources

function ResourceDetailsForm({ resource, onSaved }: { resource: ResourceDetail | null; onSaved: (r: ResourceDetail) => void }) {
  const config = useConfig();
  const { locations } = useCatalog();
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    name: resource?.name ?? "",
    description: resource?.description ?? "",
    type: resource?.type ?? "PERSON",
    capacity: resource?.capacity?.toString() ?? "",
    location_id: resource?.location_id ?? "",
    status: resource?.status ?? "ACTIVE",
  });
  const [pairs, setPairs] = useState<Pair[]>(toPairs(resource?.metadata ?? {}));
  const body = () => ({
    ...form,
    description: strOrNull(form.description),
    capacity: numOrNull(form.capacity),
    location_id: strOrNull(form.location_id),
    metadata: fromPairs(pairs),
  });
  const change = useScheduleChange<ResourceDetail>(config.data?.default_timezone ?? "UTC", (r) => onSaved(r.result));
  const create = useMutation({
    mutationFn: () => api.post<ResourceDetail>("/api/admin/resources", body()),
    onSuccess: (r) => {
      queryClient.invalidateQueries();
      onSaved(r);
    },
  });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: e.target.value });

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (resource) change.run((p) => api.put<ScheduleChange<ResourceDetail>>(`/api/admin/resources/${resource.id}`, body(), p));
        else create.mutate();
      }}
    >
      {(create.error || change.error) && <Alert>{create.error ? errorMessage(create.error) : change.error}</Alert>}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name">
          <Input required value={form.name} onChange={set("name")} />
        </Field>
        <Field label="Type">
          <Select value={form.type} onChange={set("type")}>
            {RESOURCE_TYPES.map((t) => (
              <option key={t} value={t}>
                {titleCase(t)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Location" hint="Sets the timezone and operating hours used for this resource.">
          <Select value={form.location_id} onChange={set("location_id")}>
            <option value="">No location (default timezone)</option>
            {locations.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name} ({l.timezone})
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Capacity" hint="Max people per booking, or seats for group sessions. Optional.">
          <Input type="number" min={1} value={form.capacity} onChange={set("capacity")} />
        </Field>
        <Field label="Status">
          <Select value={form.status} onChange={set("status")}>
            <option value="ACTIVE">Active</option>
            <option value="INACTIVE">Inactive (no new bookings)</option>
          </Select>
        </Field>
      </div>
      <Field label="Description">
        <Textarea value={form.description} onChange={set("description")} />
      </Field>
      <div>
        <p className="mb-2 text-sm font-medium text-fg">Custom attributes</p>
        <MetadataEditor pairs={pairs} onChange={setPairs} />
      </div>
      <Button type="submit" loading={create.isPending || change.busy}>
        {resource ? "Save changes" : "Create resource"}
      </Button>
      {change.modal}
    </form>
  );
}

function ResourceServicesForm({ resource }: { resource: ResourceDetail }) {
  const { services } = useCatalog();
  const queryClient = useQueryClient();
  const [links, setLinks] = useState<Record<string, Partial<Offering>>>(() =>
    Object.fromEntries(resource.services.map((o) => [o.service_id, o])),
  );
  const save = useMutation({
    mutationFn: () =>
      api.put(
        `/api/admin/resources/${resource.id}/services`,
        Object.entries(links).map(([service_id, o]) => ({
          service_id,
          custom_duration: o.custom_duration ?? null,
          custom_price: o.custom_price ?? null,
          custom_buffer_before: o.custom_buffer_before ?? null,
          custom_buffer_after: o.custom_buffer_after ?? null,
          status: o.status ?? "ACTIVE",
        })),
      ),
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const update = (id: string, patch: Partial<Offering>) => setLinks({ ...links, [id]: { ...links[id], ...patch } });
  return (
    <div className="space-y-4">
      {save.isSuccess && <Alert tone="success">Saved.</Alert>}
      {save.error && <Alert>{errorMessage(save.error)}</Alert>}
      <p className="text-sm text-muted">Choose the services this resource provides. Leave overrides empty to use the service defaults.</p>
      <div className="divide-y divide-line rounded-lg border border-line">
        {services.map((s) => {
          const link = links[s.id];
          return (
            <div key={s.id} className="p-3">
              <Checkbox
                label={
                  <span>
                    <span className="font-medium">{s.name}</span>{" "}
                    <span className="text-muted">
                      {formatLengthRange(s.duration_minutes, s.max_duration_minutes)} · {formatRate(s.price)}
                    </span>
                  </span>
                }
                checked={!!link}
                onChange={(e) => {
                  const next = { ...links };
                  if (e.target.checked) next[s.id] = { status: "ACTIVE" };
                  else delete next[s.id];
                  setLinks(next);
                }}
              />
              {link && (
                <div className="mt-2 grid gap-2 pl-6 sm:grid-cols-4">
                  <Input
                    type="number"
                    min={1}
                    placeholder={`Duration (${s.duration_minutes})`}
                    aria-label={`${s.name} duration`}
                    value={link.custom_duration ?? ""}
                    onChange={(e) => update(s.id, { custom_duration: numOrNull(e.target.value) })}
                  />
                  <Input
                    type="number"
                    min={0}
                    step="0.01"
                    placeholder={`Price / h (${s.price ?? "—"})`}
                    aria-label={`${s.name} price per hour`}
                    value={link.custom_price ?? ""}
                    onChange={(e) => update(s.id, { custom_price: strOrNull(e.target.value) })}
                  />
                  <Input
                    type="number"
                    min={0}
                    placeholder="Buffer before"
                    aria-label={`${s.name} buffer before`}
                    value={link.custom_buffer_before ?? ""}
                    onChange={(e) => update(s.id, { custom_buffer_before: numOrNull(e.target.value) })}
                  />
                  <Input
                    type="number"
                    min={0}
                    placeholder="Buffer after"
                    aria-label={`${s.name} buffer after`}
                    value={link.custom_buffer_after ?? ""}
                    onChange={(e) => update(s.id, { custom_buffer_after: numOrNull(e.target.value) })}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
      <Button loading={save.isPending} onClick={() => save.mutate()}>
        Save services
      </Button>
    </div>
  );
}

const blankRule = (day: number): AvailabilityRule => ({ day_of_week: day, start_time: "09:00", end_time: "17:00", valid_from: null, valid_until: null, is_available: true });

export function WeeklyRulesEditor<T extends { day_of_week: number; start_time: string; end_time: string }>({
  rules,
  onChange,
  blank,
  extra,
}: {
  rules: T[];
  onChange: (rules: T[]) => void;
  blank: (day: number) => T;
  extra?: (rule: T, update: (patch: Partial<T>) => void) => React.ReactNode;
}) {
  return (
    <div className="divide-y divide-line rounded-lg border border-line">
      {WEEKDAYS.map((day, d) => {
        const dayRules = rules.map((r, i) => ({ r, i })).filter(({ r }) => r.day_of_week === d);
        return (
          <div key={d} className="flex flex-wrap items-start gap-3 p-3">
            <div className="w-full pt-2 text-sm font-medium sm:w-24">{day}</div>
            <div className="flex-1 space-y-2">
              {dayRules.length === 0 && <p className="pt-2 text-sm text-faint">Closed / unavailable</p>}
              {dayRules.map(({ r, i }) => {
                const update = (patch: Partial<T>) => onChange(rules.map((x, j) => (j === i ? { ...x, ...patch } : x)));
                return (
                  <div key={i} className="flex flex-wrap items-center gap-2">
                    <Input type="time" aria-label={`${day} start`} value={hhmm(r.start_time)} onChange={(e) => update({ start_time: e.target.value } as Partial<T>)} className="w-32" />
                    <span className="text-faint">–</span>
                    <Input type="time" aria-label={`${day} end`} value={hhmm(r.end_time)} onChange={(e) => update({ end_time: e.target.value } as Partial<T>)} className="w-32" />
                    {extra?.(r, update)}
                    <Button variant="ghost" size="sm" onClick={() => onChange(rules.filter((_, j) => j !== i))} aria-label="Remove period">
                      <Close />
                    </Button>
                  </div>
                );
              })}
            </div>
            <Button variant="secondary" size="sm" onClick={() => onChange([...rules, blank(d)])}>
              Add period
            </Button>
          </div>
        );
      })}
    </div>
  );
}

function ResourceAvailabilityForm({ resource }: { resource: ResourceDetail }) {
  const config = useConfig();
  const [rules, setRules] = useState<AvailabilityRule[]>(resource.availability.map((r) => ({ ...r, start_time: hhmm(r.start_time), end_time: hhmm(r.end_time) })));
  const [saved, setSaved] = useState(false);
  const change = useScheduleChange(config.data?.default_timezone ?? "UTC", () => setSaved(true));
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        Recurring weekly hours in {resource.timezone}. An end time earlier than the start runs past midnight. With no periods at all the resource follows the
        operating hours. "Break" periods are carved out of available ones.
      </p>
      {change.error && <Alert>{change.error}</Alert>}
      {saved && <Alert tone="success">Schedule saved.</Alert>}
      <WeeklyRulesEditor
        rules={rules}
        onChange={(r) => {
          setRules(r);
          setSaved(false);
        }}
        blank={blankRule}
        extra={(r, update) => (
          <>
            <Select aria-label="Kind" value={r.is_available ? "1" : "0"} onChange={(e) => update({ is_available: e.target.value === "1" })} className="w-36">
              <option value="1">Available</option>
              <option value="0">Break</option>
            </Select>
            <span className="text-xs text-muted">valid</span>
            <Input type="date" aria-label="Valid from" title="Valid from (optional)" value={r.valid_from ?? ""} onChange={(e) => update({ valid_from: strOrNull(e.target.value) })} className="w-36" />
            <span className="text-xs text-muted">to</span>
            <Input type="date" aria-label="Valid until" title="Valid until (optional)" value={r.valid_until ?? ""} onChange={(e) => update({ valid_until: strOrNull(e.target.value) })} className="w-36" />
          </>
        )}
      />
      <Button
        loading={change.busy}
        onClick={() =>
          change.run((p) =>
            api.put<ScheduleChange>(
              `/api/admin/resources/${resource.id}/availability`,
              rules.map(({ id: _id, ...r }) => r),
              p,
            ),
          )
        }
      >
        Save schedule
      </Button>
      {change.modal}
    </div>
  );
}

function ResourceEditor({ resourceId, onClose }: { resourceId: string | null; onClose: () => void }) {
  const [id, setId] = useState(resourceId);
  const [tab, setTab] = useState<"details" | "services" | "availability">("details");
  const resource = useQuery({
    queryKey: ["admin", "resource", id],
    queryFn: () => api.get<ResourceDetail>(`/api/admin/resources/${id}`),
    enabled: !!id,
  });
  return (
    <Modal open wide title={id ? resource.data?.name ?? "Resource" : "New resource"} onClose={onClose}>
      {id && resource.isLoading ? (
        <Loading />
      ) : (
        <>
          {id && (
            <Tabs
              value={tab}
              onChange={setTab}
              tabs={[
                { value: "details", label: "Details" },
                { value: "services", label: "Services" },
                { value: "availability", label: "Weekly availability" },
              ]}
            />
          )}
          {tab === "details" && <ResourceDetailsForm key={resource.data?.updated_at} resource={resource.data ?? null} onSaved={(r) => r && setId(r.id)} />}
          {tab === "services" && resource.data && <ResourceServicesForm resource={resource.data} />}
          {tab === "availability" && resource.data && <ResourceAvailabilityForm resource={resource.data} />}
        </>
      )}
    </Modal>
  );
}

export function AdminResourcesPage() {
  const config = useConfig();
  const [editing, setEditing] = useState<string | null | undefined>(undefined);
  const [status, setStatus] = useState("");
  const resources = useQuery({ queryKey: ["admin", "resources", status], queryFn: () => api.get<Resource[]>("/api/admin/resources", { status }) });
  const remove = useScheduleChange<{ deleted: boolean }>(config.data?.default_timezone ?? "UTC");
  return (
    <>
      <PageHeader
        title="Resources"
        subtitle="Anything that can be booked: people, rooms, courts, equipment…"
        actions={<Button onClick={() => setEditing(null)}>New resource</Button>}
      />
      <div className="mb-4 w-full sm:w-48">
        <Select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="">All statuses</option>
          <option value="ACTIVE">Active</option>
          <option value="INACTIVE">Inactive</option>
        </Select>
      </div>
      {remove.error && <div className="mb-4"><Alert>{remove.error}</Alert></div>}
      {resources.isLoading ? (
        <Loading />
      ) : !resources.data?.length ? (
        <EmptyState title="No resources yet">Create your first resource to start taking bookings.</EmptyState>
      ) : (
        <Table head={["Name", "Type", "Location", "Capacity", "Status", ""]}>
          {resources.data.map((r) => (
            <tr key={r.id} className="hover:bg-subtle">
              <Td>
                <div className="font-medium">{r.name}</div>
                {Object.keys(r.metadata).length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {Object.entries(r.metadata).slice(0, 3).map(([k, v]) => (
                      <Badge key={k}>
                        {k}: {String(v)}
                      </Badge>
                    ))}
                  </div>
                )}
              </Td>
              <Td>{titleCase(r.type)}</Td>
              <Td>
                {r.location?.name ?? "—"}
                <div className="text-xs text-muted">{r.timezone}</div>
              </Td>
              <Td>{r.capacity ?? "—"}</Td>
              <Td>
                <StatusBadge status={r.status} />
              </Td>
              <Td className="whitespace-nowrap text-right">
                <Button size="sm" variant="secondary" onClick={() => setEditing(r.id)}>
                  Edit
                </Button>{" "}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    confirm(`Delete ${r.name}? Resources with booking history are deactivated instead.`) &&
                    remove.run((p) => api.del<ScheduleChange<{ deleted: boolean }>>(`/api/admin/resources/${r.id}`, p))
                  }
                >
                  Delete
                </Button>
              </Td>
            </tr>
          ))}
        </Table>
      )}
      {editing !== undefined && <ResourceEditor resourceId={editing} onClose={() => setEditing(undefined)} />}
      {remove.modal}
    </>
  );
}

// ---------------------------------------------------------------- services

function ServiceForm({ service, onClose }: { service: Service | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    name: service?.name ?? "",
    description: service?.description ?? "",
    duration_minutes: String(service?.duration_minutes ?? 60),
    max_duration_minutes: service?.max_duration_minutes?.toString() ?? "",
    price: service?.price ?? "",
    capacity: service?.capacity?.toString() ?? "",
    booking_type: service?.booking_type ?? "INDIVIDUAL",
    buffer_before: service?.buffer_before?.toString() ?? "",
    buffer_after: service?.buffer_after?.toString() ?? "",
    status: service?.status ?? "ACTIVE",
  });
  const save = useMutation({
    mutationFn: () => {
      const body = {
        ...form,
        description: strOrNull(form.description),
        duration_minutes: Number(form.duration_minutes),
        // Group sessions always run for their set length.
        max_duration_minutes: form.booking_type === "INDIVIDUAL" ? numOrNull(form.max_duration_minutes) : null,
        price: strOrNull(form.price),
        capacity: numOrNull(form.capacity),
        buffer_before: numOrNull(form.buffer_before),
        buffer_after: numOrNull(form.buffer_after),
      };
      return service ? api.put(`/api/admin/services/${service.id}`, body) : api.post("/api/admin/services", body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries();
      onClose();
    },
  });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: e.target.value });
  return (
    <Modal
      open
      wide
      title={service ? `Edit ${service.name}` : "New service"}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button loading={save.isPending} onClick={() => save.mutate()}>
            Save
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {save.error && <Alert>{errorMessage(save.error)}</Alert>}
        {service && <Alert tone="info">Changing the length, price or buffers only affects new bookings; existing bookings keep their times and prices.</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name">
            <Input required value={form.name} onChange={set("name")} />
          </Field>
          <Field label="Booking type">
            <Select value={form.booking_type} onChange={set("booking_type")}>
              <option value="INDIVIDUAL">Individual — one booking holds the resource</option>
              <option value="CAPACITY">Capacity — many people share a session</option>
            </Select>
          </Field>
          <Field label="Duration (minutes)" hint="The standard booking length.">
            <Input type="number" min={5} max={1440} required value={form.duration_minutes} onChange={set("duration_minutes")} />
          </Field>
          {form.booking_type === "INDIVIDUAL" && (
            <Field label="Longest booking (minutes)" hint="Optional. Customers may then book longer, in steps of the duration.">
              <Input type="number" min={5} max={1440} value={form.max_duration_minutes} onChange={set("max_duration_minutes")} />
            </Field>
          )}
          <Field label="Price per hour" hint="A booking costs this rate × its hours (× places for group sessions).">
            <Input type="number" min={0} step="0.01" value={form.price} onChange={set("price")} />
          </Field>
          {form.booking_type === "CAPACITY" && (
            <Field label="Capacity per session">
              <Input type="number" min={1} value={form.capacity} onChange={set("capacity")} />
            </Field>
          )}
          <Field label="Buffer before (minutes)" hint="Preparation time blocked before each booking.">
            <Input type="number" min={0} value={form.buffer_before} onChange={set("buffer_before")} />
          </Field>
          <Field label="Buffer after (minutes)" hint="Clean-up time blocked after each booking.">
            <Input type="number" min={0} value={form.buffer_after} onChange={set("buffer_after")} />
          </Field>
          <Field label="Status">
            <Select value={form.status} onChange={set("status")}>
              <option value="ACTIVE">Active</option>
              <option value="INACTIVE">Inactive</option>
            </Select>
          </Field>
        </div>
        <Field label="Description">
          <Textarea value={form.description} onChange={set("description")} />
        </Field>
      </div>
    </Modal>
  );
}

export function AdminServicesPage() {
  const queryClient = useQueryClient();
  const services = useQuery({ queryKey: ["admin", "services"], queryFn: () => api.get<Service[]>("/api/admin/services") });
  const [editing, setEditing] = useState<Service | null | undefined>(undefined);
  const remove = useMutation({
    mutationFn: (id: string) => api.del<{ deleted: boolean }>(`/api/admin/services/${id}`),
    onSuccess: () => queryClient.invalidateQueries(),
  });
  return (
    <>
      <PageHeader title="Services" subtitle="What customers book." actions={<Button onClick={() => setEditing(null)}>New service</Button>} />
      {remove.error && <div className="mb-4"><Alert>{errorMessage(remove.error)}</Alert></div>}
      {services.isLoading ? (
        <Loading />
      ) : (
        <Table head={["Name", "Type", "Length", "Buffers", "Rate", "Status", ""]}>
          {services.data?.map((s) => (
            <tr key={s.id} className="hover:bg-subtle">
              <Td className="font-medium">{s.name}</Td>
              <Td>{s.booking_type === "CAPACITY" ? `Group (${s.capacity ?? 1})` : "Individual"}</Td>
              <Td>{formatLengthRange(s.duration_minutes, s.max_duration_minutes)}</Td>
              <Td>{s.buffer_before || s.buffer_after ? `${s.buffer_before ?? 0} / ${s.buffer_after ?? 0} min` : "—"}</Td>
              <Td>{formatRate(s.price)}</Td>
              <Td>
                <StatusBadge status={s.status} />
              </Td>
              <Td className="whitespace-nowrap text-right">
                <Button size="sm" variant="secondary" onClick={() => setEditing(s)}>
                  Edit
                </Button>{" "}
                <Button size="sm" variant="ghost" onClick={() => confirm(`Delete ${s.name}? Services with bookings are deactivated instead.`) && remove.mutate(s.id)}>
                  Delete
                </Button>
              </Td>
            </tr>
          ))}
        </Table>
      )}
      {editing !== undefined && <ServiceForm service={editing} onClose={() => setEditing(undefined)} />}
    </>
  );
}

// ---------------------------------------------------------------- locations

const TIMEZONES: string[] = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.("timeZone") ?? ["UTC"];

function LocationForm({ location, onClose }: { location: Location | null; onClose: () => void }) {
  const config = useConfig();
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    name: location?.name ?? "",
    address: location?.address ?? "",
    map_url: location?.map_url ?? "",
    timezone: location?.timezone ?? config.data?.default_timezone ?? "UTC",
    status: location?.status ?? "ACTIVE",
  });
  const body = () => ({ ...form, address: strOrNull(form.address), map_url: strOrNull(form.map_url.trim()) });
  const create = useMutation({
    mutationFn: () => api.post("/api/admin/locations", body()),
    onSuccess: () => {
      queryClient.invalidateQueries();
      onClose();
    },
  });
  const change = useScheduleChange(config.data?.default_timezone ?? "UTC", onClose);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: e.target.value });
  const submit = () =>
    location
      ? change.run((p) => api.put<ScheduleChange>(`/api/admin/locations/${location.id}`, body(), p))
      : create.mutate();
  return (
    <Modal
      open
      title={location ? `Edit ${location.name}` : "New location"}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button loading={create.isPending || change.busy} onClick={submit}>
            Save
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {(create.error || change.error) && <Alert>{create.error ? errorMessage(create.error) : change.error}</Alert>}
        <Field label="Name">
          <Input required value={form.name} onChange={set("name")} />
        </Field>
        <Field label="Address">
          <Textarea value={form.address} onChange={set("address")} />
        </Field>
        <Field label="Google Maps link (optional)" hint="Paste the place's Share link from Google Maps. Without one, customers get a Maps search for the address.">
          <Input type="url" inputMode="url" placeholder="https://maps.app.goo.gl/…" value={form.map_url} onChange={set("map_url")} />
        </Field>
        <Field label="Timezone" hint="All schedules at this location are interpreted in this timezone.">
          <Select value={form.timezone} onChange={set("timezone")}>
            {TIMEZONES.map((tz) => (
              <option key={tz} value={tz}>
                {tz}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Status">
          <Select value={form.status} onChange={set("status")}>
            <option value="ACTIVE">Active</option>
            <option value="INACTIVE">Inactive</option>
          </Select>
        </Field>
      </div>
      {change.modal}
    </Modal>
  );
}

export function AdminLocationsPage() {
  const queryClient = useQueryClient();
  const locations = useQuery({ queryKey: ["admin", "locations"], queryFn: () => api.get<Location[]>("/api/admin/locations") });
  const [editing, setEditing] = useState<Location | null | undefined>(undefined);
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/api/admin/locations/${id}`),
    onSuccess: () => queryClient.invalidateQueries(),
  });
  return (
    <>
      <PageHeader title="Locations" subtitle="Branches, each with its own timezone and operating hours." actions={<Button onClick={() => setEditing(null)}>New location</Button>} />
      {locations.isLoading ? (
        <Loading />
      ) : !locations.data?.length ? (
        <EmptyState title="No locations">Single-site businesses can skip this; resources then use the default timezone.</EmptyState>
      ) : (
        <Table head={["Name", "Address", "Timezone", "Status", ""]}>
          {locations.data.map((l) => (
            <tr key={l.id} className="hover:bg-subtle">
              <Td className="font-medium">{l.name}</Td>
              <Td>
                {l.address ?? "—"}
                <div className="mt-0.5">
                  <MapLink url={l.google_maps_url} className="text-xs">
                    Map
                  </MapLink>
                </div>
              </Td>
              <Td>{l.timezone}</Td>
              <Td>
                <StatusBadge status={l.status} />
              </Td>
              <Td className="whitespace-nowrap text-right">
                <Button size="sm" variant="secondary" onClick={() => setEditing(l)}>
                  Edit
                </Button>{" "}
                <Button size="sm" variant="ghost" onClick={() => confirm(`Delete ${l.name}? Locations with resources are deactivated instead.`) && remove.mutate(l.id)}>
                  Delete
                </Button>
              </Td>
            </tr>
          ))}
        </Table>
      )}
      {editing !== undefined && <LocationForm location={editing} onClose={() => setEditing(undefined)} />}
    </>
  );
}
