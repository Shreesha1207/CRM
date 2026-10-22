import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api, errorMessage } from "../../api/client";
import type { AuditLog, Page, Role, SettingDefinition, User, UserStatus } from "../../api/types";
import { ROLES } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { Alert, Button, Card, Checkbox, Field, Input, Loading, Modal, PageHeader, Pagination, Select, StatusBadge, Table, Td, Textarea } from "../../components/ui";
import { formatStamp, titleCase } from "../../lib/format";

// ---------------------------------------------------------------- users

function UserForm({ user, onClose }: { user: User | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  // Only a super admin decides who gets into the admin console.
  const { can, user: me } = useAuth();
  const grantsRoles = can("admins:manage");
  // The roles this person may hand out: a super admin every one below their own, an admin only "User".
  // Nobody is made a super admin from here.
  const assignable = grantsRoles ? ROLES.filter((r) => r !== "SUPER_ADMIN") : (["USER"] as Role[]);
  const roleLocked = !assignable.includes(user?.role ?? "USER");
  // An invited account becomes active when its owner sets it up, and nobody changes their own status.
  const isMe = !!user && user.id === me?.id;
  const statusLocked = isMe || user?.status === "INVITED";
  const [form, setForm] = useState({
    name: user?.name ?? "",
    email: user?.email ?? "",
    phone: user?.phone ?? "",
    role: user?.role ?? ("USER" as Role),
    status: user?.status ?? ("INVITED" as UserStatus),
  });
  const save = useMutation({
    mutationFn: () => {
      const body = { name: form.name, phone: form.phone || null, role: form.role };
      // New accounts are invited: the person chooses their own password from the e-mailed link.
      return user ? api.put(`/api/admin/users/${user.id}`, { ...body, status: form.status }) : api.post("/api/admin/users", { ...body, email: form.email });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      onClose();
    },
  });
  // The admin never sees or picks a password, only sends a link for the person to choose one.
  const sendLink = useMutation({ mutationFn: () => api.post<{ message: string }>(`/api/admin/users/${user!.id}/send-password-link`) });
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
            {user ? "Save" : "Create and send invitation"}
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
          <Field
            label="Role"
            hint={roleLocked ? "You cannot change this role." : grantsRoles ? undefined : "Only a super admin can give admin roles."}
          >
            <Select value={form.role} onChange={set("role")} disabled={roleLocked || assignable.length < 2}>
              {(roleLocked ? [form.role] : assignable).map((r) => (
                <option key={r} value={r}>
                  {titleCase(r)}
                </option>
              ))}
            </Select>
          </Field>
          {user && (
            <Field
              label="Status"
              hint={
                isMe
                  ? "You cannot change the status of your own account."
                  : user.status === "INVITED"
                    ? "Becomes Active when they accept the invitation."
                    : undefined
              }
            >
              <Select value={form.status} onChange={set("status")} disabled={statusLocked}>
                <option value="ACTIVE">Active</option>
                <option value="INACTIVE">Inactive</option>
                <option value="SUSPENDED">Suspended</option>
                {/* Only made by an invitation; shown so the current value reads right, never offered. */}
                {form.status === "INVITED" && <option value="INVITED">Invited (not set up yet)</option>}
              </Select>
            </Field>
          )}
        </div>
        {!user ? (
          <p className="text-sm text-muted">We'll e-mail them a link to choose their own password. The account becomes active once they do.</p>
        ) : (
          !isMe &&
          (user.status === "ACTIVE" || user.status === "INVITED") && (
            <div className="space-y-2 rounded-md border border-line p-3">
              <p className="text-sm font-medium text-fg">Password</p>
              <p className="text-xs text-muted">
                {user.status === "INVITED"
                  ? "They haven't set up their account yet. Send a new link if they lost the first one or it ran out."
                  : "Only they can choose their password. If they forgot it, send them a link to pick a new one."}
              </p>
              {sendLink.isSuccess && <Alert tone="success">{sendLink.data.message}</Alert>}
              {sendLink.error && <Alert>{errorMessage(sendLink.error)}</Alert>}
              <Button size="sm" variant="secondary" loading={sendLink.isPending} onClick={() => sendLink.mutate()}>
                {user.status === "INVITED" ? "Resend invitation" : "Send password reset link"}
              </Button>
            </div>
          )
        )}
      </div>
    </Modal>
  );
}

function DeleteUserModal({ user, onClose }: { user: User; onClose: () => void }) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.del(`/api/admin/users/${user.id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      onClose();
    },
  });
  return (
    <Modal
      open
      title={`Delete ${user.name}?`}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
            Delete for good
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm">
        {remove.error && <Alert>{errorMessage(remove.error)}</Alert>}
        <p>
          This deletes <span className="font-medium">{user.email}</span> ({titleCase(user.role)}) and their past bookings. It cannot be undone.
        </p>
        <p className="text-muted">To keep their history, set the account to Inactive instead.</p>
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
  const [deleting, setDeleting] = useState<User | null>(null);
  const users = useQuery({
    queryKey: ["admin", "users", q, role, offset],
    queryFn: () => api.get<Page<User>>("/api/admin/users", { q, role, limit: 25, offset }),
  });
  const manage = can("users:manage");
  const superAdmin = can("admins:manage");
  // Admin accounts are the super admin's to change; everyone can edit their own.
  // The hierarchy: only accounts below you, and a super admin's only by its owner.
  const below = (u: User) => u.role !== "SUPER_ADMIN" && (superAdmin || u.role === "USER");
  const canEdit = (u: User) => manage && (u.id === me?.id || below(u));
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
                <Td className="space-x-2 whitespace-nowrap text-right">
                  {canEdit(u) && (
                    <Button size="sm" variant="secondary" onClick={() => setEditing(u)}>
                      Edit
                    </Button>
                  )}
                  {can("users:delete") && u.id !== me?.id && below(u) && (
                    <Button size="sm" variant="danger" onClick={() => setDeleting(u)}>
                      Delete
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
      {deleting && <DeleteUserModal user={deleting} onClose={() => setDeleting(null)} />}
    </>
  );
}

// ---------------------------------------------------------------- settings

export function AdminSettingsPage() {
  const queryClient = useQueryClient();
  const settings = useQuery({
    queryKey: ["admin", "settings"],
    queryFn: () =>
      api.get<{ values: Record<string, unknown>; versions: Record<string, string | null>; definitions: SettingDefinition[] }>("/api/admin/settings"),
  });
  // Only the settings changed here: saving them leaves everything another admin changed alone.
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const values = { ...settings.data?.values, ...draft };
  const save = useMutation({
    mutationFn: () => {
      const keys = Object.keys(draft ?? {});
      // The versions these were loaded at: if someone saved one since, the server refuses instead of overwriting it.
      const versions = Object.fromEntries(keys.map((k) => [k, settings.data?.versions[k] ?? null]));
      return api.put("/api/admin/settings", { values: draft, versions });
    },
    onSuccess: () => {
      setDraft(null);
      queryClient.invalidateQueries();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "SETTINGS_CHANGED") {
        // Show the other admin's values for those settings; keep the rest of this draft.
        const stale = (error.details as { keys?: string[] } | undefined)?.keys ?? [];
        setDraft((d) => {
          const rest = Object.fromEntries(Object.entries(d ?? {}).filter(([k]) => !stale.includes(k)));
          return Object.keys(rest).length ? rest : null;
        });
        queryClient.invalidateQueries({ queryKey: ["admin", "settings"] });
      }
    },
  });
  if (settings.isLoading) return <Loading />;
  const set = (key: string, value: unknown) => setDraft({ ...draft, [key]: value });
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
            if (d.key === "assistant_knowledge") {
              return (
                <Field key={d.key} label="Booking assistant FAQ" hint={`${d.description} The assistant answers only from this text and its own lookups.`}>
                  <Textarea rows={6} maxLength={20000} value={String(value ?? "")} onChange={(e) => set(d.key, e.target.value)} />
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
                <Td className="whitespace-nowrap">{formatStamp(l.created_at)}</Td>
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
