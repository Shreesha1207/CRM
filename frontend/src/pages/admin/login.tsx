import { useMutation } from "@tanstack/react-query";
import type { FormEvent } from "react";
import { useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { errorMessage } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { useConfig } from "../../components/Layout";
import { Mark } from "../../components/icons";
import { Alert, Button, Field, Input, Loading, buttonClass } from "../../components/ui";

/** Sign-in for staff. Customers have their own at /login. */
export function AdminLoginPage() {
  const { login, logout, user, isStaff, loading } = useAuth();
  const config = useConfig();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const mutation = useMutation({
    mutationFn: async () => {
      const me = await login(email, password);
      if (!me.permissions.length) {
        await logout();
        throw new Error("This account doesn't have admin access.");
      }
      return me;
    },
  });
  const next = params.get("next");
  if (loading) return <Loading />;
  if (user && isStaff) return <Navigate to={next?.startsWith("/admin") ? next : "/admin"} replace />;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    mutation.mutate();
  };

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center bg-side px-4 py-12">
      <div className="mb-6 flex items-center gap-2 text-side-fg">
        <Mark className="h-6 w-6" />
        <span className="font-semibold tracking-tight">{config.data?.business_name ?? "Bookings"}</span>
        <span className="rounded bg-side-active px-1.5 py-0.5 text-[11px] font-medium text-side-muted">Admin</span>
      </div>
      <div className="w-full max-w-sm rounded-xl border border-line bg-surface p-6 shadow-lg sm:p-8">
        <h1 className="text-xl font-semibold tracking-tight text-fg">Admin console</h1>
        <p className="mt-1 text-sm text-muted">Sign in to manage bookings, schedules and users.</p>
        {user ? (
          // Signed in already, but as a customer.
          <div className="mt-6 space-y-4">
            <Alert tone="info">You're signed in as {user.email}, which is a customer account.</Alert>
            <div className="flex flex-wrap gap-2">
              <Link to="/dashboard" className={buttonClass("secondary")}>
                Go to my bookings
              </Link>
              <Button variant="ghost" onClick={() => logout()}>
                Sign out
              </Button>
            </div>
          </div>
        ) : (
          <form onSubmit={submit} className="mt-6 space-y-4">
            {mutation.error && <Alert>{errorMessage(mutation.error)}</Alert>}
            <Field label="E-mail">
              <Input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
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
        )}
      </div>
      <p className="mt-6 text-sm text-side-muted">
        Booking an appointment?{" "}
        <Link to="/login" className="font-medium text-side-accent underline-offset-2 hover:underline">
          Customer sign-in
        </Link>
      </p>
    </div>
  );
}
