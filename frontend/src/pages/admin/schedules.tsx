import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import type { AvailabilityException, ExceptionType, OperatingHours, ScheduleChange } from "../../api/types";
import { EXCEPTION_TYPES } from "../../api/types";
import { useConfig } from "../../components/Layout";
import { Alert, Badge, Button, Card, Field, Input, Loading, PageHeader, Select, Table, Td, Textarea } from "../../components/ui";
import { formatDateTime, hhmm, titleCase } from "../../lib/format";
import { useCatalog } from "./bookings";
import { WeeklyRulesEditor } from "./catalog";
import { useScheduleChange } from "./scheduleChange";

function OperatingHoursEditor() {
  const config = useConfig();
  const { locations } = useCatalog();
  const [locationId, setLocationId] = useState("");
  const hours = useQuery({
    queryKey: ["admin", "operating-hours", locationId],
    queryFn: () => api.get<OperatingHours[]>("/api/admin/operating-hours", { location_id: locationId }),
  });
  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-semibold">Operating hours</h2>
          <p className="text-sm text-slate-500">Bookings can't normally happen outside these hours. Days without a period are closed.</p>
        </div>
        <Select value={locationId} onChange={(e) => setLocationId(e.target.value)} className="w-64" aria-label="Scope">
          <option value="">Default (all locations without their own)</option>
          {locations.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </Select>
      </div>
      {hours.isLoading ? (
        <Loading />
      ) : (
        <HoursForm
          key={`${locationId}-${hours.dataUpdatedAt}`}
          initial={hours.data ?? []}
          locationId={locationId}
          tz={config.data?.default_timezone ?? "UTC"}
        />
      )}
    </Card>
  );
}

function HoursForm({ initial, locationId, tz }: { initial: OperatingHours[]; locationId: string; tz: string }) {
  const [rules, setRules] = useState(initial.map((r) => ({ day_of_week: r.day_of_week, start_time: hhmm(r.start_time), end_time: hhmm(r.end_time) })));
  const [saved, setSaved] = useState(false);
  const change = useScheduleChange(tz, () => setSaved(true));
  return (
    <div className="space-y-4">
      {change.error && <Alert>{change.error}</Alert>}
      {saved && <Alert tone="success">Operating hours saved.</Alert>}
      {rules.length === 0 && <Alert tone="info">No hours set for this scope: bookings are only limited by each resource's own availability.</Alert>}
      <WeeklyRulesEditor
        rules={rules}
        onChange={(r) => {
          setRules(r);
          setSaved(false);
        }}
        blank={(d) => ({ day_of_week: d, start_time: "09:00", end_time: "18:00" })}
      />
      <Button
        loading={change.busy}
        onClick={() => change.run((p) => api.put<ScheduleChange>("/api/admin/operating-hours", rules, { ...p, location_id: locationId }))}
      >
        Save operating hours
      </Button>
      {change.modal}
    </div>
  );
}

function ExceptionForm({ tz }: { tz: string }) {
  const { resources, locations } = useCatalog();
  const [scope, setScope] = useState<"resource" | "location" | "all">("resource");
  const [form, setForm] = useState({ resource_id: "", location_id: "", start: "", end: "", type: "BLOCKED" as ExceptionType, reason: "" });
  const [done, setDone] = useState(false);
  const change = useScheduleChange(tz, () => {
    setDone(true);
    setForm({ ...form, start: "", end: "", reason: "" });
  });
  const valid = form.start && form.end && (scope !== "resource" || form.resource_id) && (scope !== "location" || form.location_id);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    setForm({ ...form, [k]: e.target.value });
    setDone(false);
  };
  return (
    <Card>
      <h2 className="font-semibold">Block time, holidays &amp; special hours</h2>
      <p className="mb-4 text-sm text-slate-500">
        Blocks override regular availability. <strong>Special hours</strong> replace the regular hours for the dates they cover. Times are local to the
        resource / location.
      </p>
      <div className="space-y-4">
        {change.error && <Alert>{change.error}</Alert>}
        {done && <Alert tone="success">Saved.</Alert>}
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Applies to">
            <Select value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}>
              <option value="resource">One resource</option>
              <option value="location">A whole location</option>
              <option value="all">Everything</option>
            </Select>
          </Field>
          {scope === "resource" && (
            <Field label="Resource">
              <Select value={form.resource_id} onChange={set("resource_id")}>
                <option value="">Choose…</option>
                {resources.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          {scope === "location" && (
            <Field label="Location">
              <Select value={form.location_id} onChange={set("location_id")}>
                <option value="">Choose…</option>
                {locations.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <Field label="Type">
            <Select value={form.type} onChange={set("type")}>
              {EXCEPTION_TYPES.map((t) => (
                <option key={t} value={t}>
                  {titleCase(t)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="From">
            <Input type="datetime-local" value={form.start} onChange={set("start")} />
          </Field>
          <Field label="Until">
            <Input type="datetime-local" value={form.end} onChange={set("end")} />
          </Field>
        </div>
        <Field label="Reason">
          <Textarea value={form.reason} onChange={set("reason")} placeholder="e.g. Maintenance, public holiday, private event" />
        </Field>
        <Button
          disabled={!valid}
          loading={change.busy}
          onClick={() =>
            change.run((p) =>
              api.post<ScheduleChange>(
                "/api/admin/exceptions",
                {
                  resource_id: scope === "resource" ? form.resource_id : null,
                  location_id: scope === "location" ? form.location_id : null,
                  start: form.start,
                  end: form.end,
                  type: form.type,
                  reason: form.reason || null,
                },
                p,
              ),
            )
          }
        >
          Save
        </Button>
      </div>
      {change.modal}
    </Card>
  );
}

function ExceptionList({ tz }: { tz: string }) {
  const { resources, locations } = useCatalog();
  const [from] = useState(() => new Date(Date.now() - 86_400_000).toISOString());
  const exceptions = useQuery({
    queryKey: ["admin", "exceptions", from],
    queryFn: () => api.get<AvailabilityException[]>("/api/admin/exceptions", { start: from }),
  });
  const remove = useScheduleChange(tz);
  const scopeName = (e: AvailabilityException) =>
    e.resource_id ? resources.find((r) => r.id === e.resource_id)?.name ?? "Resource" : e.location_id ? locations.find((l) => l.id === e.location_id)?.name ?? "Location" : "Everything";
  const tzFor = (e: AvailabilityException) =>
    (e.resource_id ? resources.find((r) => r.id === e.resource_id)?.timezone : locations.find((l) => l.id === e.location_id)?.timezone) ?? tz;
  return (
    <div>
      <h2 className="mb-3 font-semibold">Upcoming blocks &amp; exceptions</h2>
      {remove.error && <div className="mb-3"><Alert>{remove.error}</Alert></div>}
      {exceptions.isLoading ? (
        <Loading />
      ) : (
        <Table head={["Type", "Applies to", "From", "Until", "Reason", ""]}>
          {exceptions.data?.map((e) => (
            <tr key={e.id}>
              <Td>
                <Badge tone={e.type === "SPECIAL_HOURS" ? "ACTIVE" : "CONFLICTED"}>{titleCase(e.type)}</Badge>
              </Td>
              <Td>{scopeName(e)}</Td>
              <Td>{formatDateTime(e.start_datetime, tzFor(e))}</Td>
              <Td>{formatDateTime(e.end_datetime, tzFor(e))}</Td>
              <Td>{e.reason ?? "—"}</Td>
              <Td className="text-right">
                <Button size="sm" variant="ghost" onClick={() => remove.run((p) => api.del<ScheduleChange>(`/api/admin/exceptions/${e.id}`, p))}>
                  Remove
                </Button>
              </Td>
            </tr>
          ))}
          {exceptions.data?.length === 0 && (
            <tr>
              <Td className="py-6 text-slate-500">Nothing scheduled.</Td>
            </tr>
          )}
        </Table>
      )}
      {remove.modal}
    </div>
  );
}

export function AdminSchedulesPage() {
  const config = useConfig();
  const tz = config.data?.default_timezone ?? "UTC";
  return (
    <>
      <PageHeader title="Schedules" subtitle="Business hours, blocked periods, holidays and special hours. Each resource's own weekly hours are edited under Resources." />
      <div className="space-y-6">
        <OperatingHoursEditor />
        <ExceptionForm tz={tz} />
        <ExceptionList tz={tz} />
      </div>
    </>
  );
}
