import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createContext, useCallback, useContext, useMemo } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { Me } from "../api/types";
import { Loading } from "../components/ui";

interface AuthState {
  user: Me | null;
  loading: boolean;
  isStaff: boolean;
  can: (permission: string) => boolean;
  login: (email: string, password: string) => Promise<Me>;
  register: (data: { name: string; email: string; phone?: string; password: string }) => Promise<Me>;
  logout: () => Promise<void>;
  refresh: () => Promise<unknown>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return (await api.get<{ user: Me | null }>("/api/auth/session")).user;
      } catch (e) {
        // An expired / revoked token is simply "signed out".
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
  });

  const setUser = useCallback(
    (user: Me | null) => {
      queryClient.setQueryData(["me"], user);
      // Drop anything cached for the previous user (bookings, notifications...).
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
    },
    [queryClient],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await api.post<{ user: Me }>("/api/auth/login", { email, password });
      setUser(res.user);
      return res.user;
    },
    [setUser],
  );

  const register = useCallback(
    async (data: { name: string; email: string; phone?: string; password: string }) => {
      const res = await api.post<{ user: Me }>("/api/auth/register", data);
      setUser(res.user);
      return res.user;
    },
    [setUser],
  );

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout");
    } finally {
      setUser(null);
    }
  }, [setUser]);

  const value = useMemo<AuthState>(() => {
    const user = me.data ?? null;
    const permissions = new Set(user?.permissions ?? []);
    return {
      user,
      loading: me.isLoading,
      // The UI only hides what the server would refuse anyway.
      isStaff: permissions.size > 0,
      can: (p: string) => permissions.has(p),
      login,
      register,
      logout,
      refresh: me.refetch,
    };
  }, [me.data, me.isLoading, me.refetch, login, register, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

export function RequireAuth({ children, staff }: { children: ReactNode; staff?: boolean }) {
  const { user, loading, isStaff } = useAuth();
  const location = useLocation();
  if (loading) return <Loading />;
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  if (staff && !isStaff) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}
