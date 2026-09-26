import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, errorMessage } from "../../api/client";
import type { AuditLog, Page, Role, SettingDefinition, User, UserStatus } from "../../api/types";
import { ROLES } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, Card, Checkbox, Field, Input, Loading, Modal, PageHeader, Pagination, Select, StatusBadge, Table, Td } from "../../components/ui";
import { titleCase } from "../../lib/format";

// ---------------------------------------------------------------- users

function UserForm({ user, onClose }: { user: User | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    name: user?.name ?? "",
    email: user?.email ?? "",
    phone: user?.phone ?? "",
    role: user?.role ?? ("USER" as Role),
    status: user?.status ?? ("ACTIVE" as UserStatus),
    password: "",
  });
  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { name: form.name, phone: form.phone || null, role: form.role, status: form.status };
      if (form.password) body.password = form.password;
      return user ? api.put(`/api/admin/users/${user.id}`, body) : api.post("/api/admin/users", { ...body, email: form.email, password: form.password });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      onClose();
    },
  });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });
  return (
    <Modal
      open
      title={user ? `Edit ${user.name}` : "New user"}
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
        <Field label="Name">
          <Input value={form.name} onChange={set("name")} />
        </Field>
        <Field label="E-mail">
          <Input type="email" value={form.email} onChange={set("email")} disabled={!!user} />
        </Field>
        <Field label="Phone">
          <Input value={form.phone} onChange={set("phone")} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Role">
            <Select value={form.role} onChange={set("role")}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {titleCase(r)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Status">
            <Select value={form.status} onChange={set("status")}>
              <option value="ACTIVE">Active</option>
              <option value="INACTIVE">Inactive</option>
              <option value="SUSPENDED">Suspended</option>
            </Select>
          </Field>
        </div>
        <Field label={user ? "Set a new password (optional)" : "Password"} hint="At least 8 characters. Changing it signs the user out everywhere.">
          <Input type="password" minLength={8} value={form.password} onChange={set("password")} autoComplete="new-password" />
        </Field>
      </div>
    </Modal>
  );
}

export function AdminUsersPage() {
  const { can, user: me } = useAuth();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<User | null | undefined>(undefined);
  const users = useQuery({
    queryKey: ["admin", "users", q, role, offset],
    queryFn: () => api.get<Page<User>>("/api/admin/users", { q, role, limit: 25, offset }),
  });
  const manage = can("users:manage");
  return (
    <>
      <PageHeader title="Users" actions={manage && <Button onClick={() => setEditing(null)}>New user</Button>} />
      <div className="mb-4 grid gap-2 sm:grid-cols-3">
        <Input placeholder="Search name, e-mail or phone" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
        <Select value={role} onChange={(e) => { setRole(e.target.value); setOffset(0); }} aria-label="Role">
          <option value="">All roles</option>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {titleCase(r)}
            </option>
          ))}
        </Select>
      </div>
      {users.isLoading ? (
        <Loading />
      ) : (
        <>
          <Table head={["Name", "E-mail", "Role", "Status", "Joined", ""]}>
            {users.data?.items.map((u) => (
              <tr key={u.id} className="hover:bg-subtle">
                <Td className="font-medium">
                  {u.name}
                  {u.id === me?.id && <span className="ml-1 text-xs text-faint">(you)</span>}
                </Td>
                <Td>{u.email}</Td>
                <Td>{titleCase(u.role)}</Td>
                <Td>
                  <StatusBadge status={u.status} />
                </Td>
                <Td>{new Date(u.created_at).toLocaleDateString()}</Td>
                <Td className="text-right">
                  {manage && (
                    <Button size="sm" variant="secondary" onClick={() => setEditing(u)}>
                      Edit
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </Table>
          <Pagination total={users.data?.total ?? 0} limit={25} offset={offset} onChange={setOffset} />
        </>
      )}
      {editing !== undefined && <UserForm user={editing} onClose={() => setEditing(undefined)} />}
    </>
  );
}

// ---------------------------------------------------------------- settings

export function AdminSettingsPage() {
  const queryClient = useQueryClient();
  const settings = useQuery({
    queryKey: ["admin", "settings"],
    queryFn: () => api.get<{ values: Record<string, unknown>; definitions: SettingDefinition[] }>("/api/admin/settings"),
  });
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const values = draft ?? settings.data?.values ?? {};
  const save = useMutation({
    mutationFn: () => api.put("/api/admin/settings", { values: draft }),
    onSuccess: () => {
      setDraft(null);
      queryClient.invalidateQueries();
    },
  });
  if (settings.isLoading) return <Loading />;
  const set = (key: string, value: unknown) => setDraft({ ...values, [key]: value });
  return (
    <>
      <PageHeader title="Settings" subtitle="Booking rules. These make the same engine fit a clinic, a gym or a meeting-room system." />
      <Card>
        <div className="space-y-5">
          {save.isSuccess && !draft && <Alert tone="success">Settings saved.</Alert>}
          {save.error && <Alert>{errorMessage(save.error)}</Alert>}
          {settings.data?.definitions.map((d) => {
            const value = values[d.key];
            if (typeof d.default === "boolean") {
              return (
                <div key={d.key}>
                  <Checkbox label={<span className="font-medium">{titleCase(d.key)}</span>} checked={!!value} onChange={(e) => set(d.key, e.target.checked)} />
                  <p className="ml-6 text-xs text-muted">{d.description}</p>
                </div>
              );
            }
            if (Array.isArray(d.default)) {
              return (
                <Field key={d.key} label={titleCase(d.key)} hint={`${d.description} Comma-separated.`}>
                  <Input
                    value={(value as number[]).join(", ")}
                    onChange={(e) =>
                      set(
                        d.key,
                        e.target.value
                          .split(",")
                          .map((x) => Number(x.trim()))
                          .filter((x) => x > 0),
                      )
                    }
                  />
                </Field>
              );
            }
            return (
              <Field key={d.key} label={titleCase(d.key)} hint={d.description}>
                <Input
                  type={typeof d.default === "number" ? "number" : "text"}
                  min={0}
                  value={String(value ?? "")}
                  onChange={(e) => set(d.key, typeof d.default === "number" ? Number(e.target.value) : e.target.value)}
                  className="max-w-sm"
                />
              </Field>
            );
          })}
          <div className="flex gap-2">
            <Button disabled={!draft} loading={save.isPending} onClick={() => save.mutate()}>
              Save settings
            </Button>
            {draft && (
              <Button variant="secondary" onClick={() => setDraft(null)}>
                Discard changes
              </Button>
            )}
          </div>
        </div>
      </Card>
    </>
  );
}

// ---------------------------------------------------------------- audit

export function AdminAuditPage() {
  const [action, setAction] = useState("");
  const [offset, setOffset] = useState(0);
  const logs = useQuery({
    queryKey: ["admin", "audit", action, offset],
    queryFn: () => api.get<Page<AuditLog>>("/api/admin/audit-logs", { action: action.trim().toUpperCase(), limit: 50, offset }),
  });
  return (
    <>
      <PageHeader title="Audit log" subtitle="Every booking change, schedule change and administrative override." />
      <div className="mb-4 max-w-xs">
        <Input placeholder="Filter by action, e.g. ADMIN_OVERRIDE" value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }} />
      </div>
      {logs.isLoading ? (
        <Loading />
      ) : (
        <>
          <Table head={["When", "Who", "Action", "Entity", "Details"]}>
            {logs.data?.items.map((l) => (
              <tr key={l.id}>
                <Td className="whitespace-nowrap">{new Date(l.created_at).toLocaleString()}</Td>
                <Td>{l.actor_name ?? "system"}</Td>
                <Td className="font-medium">{titleCase(l.action)}</Td>
                <Td>
                  {l.entity_type}
                  {l.entity_id && <div className="font-mono text-[11px] text-faint">{l.entity_id.slice(0, 8)}</div>}
                </Td>
                <Td>
                  {(l.old_value || l.new_value) && (
                    <details>
                      <summary className="cursor-pointer text-xs text-accent-text">View</summary>
                      <pre className="mt-1 max-w-md overflow-x-auto rounded bg-subtle p-2 text-[11px]">
                        {JSON.stringify({ before: l.old_value, after: l.new_value }, null, 2)}
                      </pre>
                    </details>
                  )}
                </Td>
              </tr>
            ))}
          </Table>
          <Pagination total={logs.data?.total ?? 0} limit={50} offset={offset} onChange={setOffset} />
        </>
      )}
    </>
  );
}
