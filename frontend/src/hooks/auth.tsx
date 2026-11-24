import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";
import { api, setUnauthorizedHandler } from "@/lib/api";
import { configureFormatting } from "@/lib/format";
import type { Me } from "@/types/api";

interface AuthState {
  user: Me | null;
  loading: boolean;
  can: (permission: string) => boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<unknown>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: async () => (await api.get<{ authenticated: boolean; user: Me | null }>("/auth/session")).user,
    staleTime: 5 * 60_000,
    retry: false,
  });

  useEffect(() => {
    setUnauthorizedHandler(() => qc.setQueryData<Me | null>(["me"], null));
  }, [qc]);

  useEffect(() => {
    const c = me.data?.company;
    if (c) configureFormatting({ locale: c.number_format, currency: c.currency_code, timeZone: c.timezone });
  }, [me.data]);

  // Drop every cached query except the session itself: clearing "me" too would detach the observer above, and the new
  // user would only appear after some unrelated re-render.
  const dropUserData = useCallback(() => qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" }), [qc]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await api.post<{ user: Me }>("/auth/login", { email, password });
    dropUserData();
    qc.setQueryData(["me"], res.user);
  }, [qc, dropUserData]);

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout"); } finally { qc.setQueryData<Me | null>(["me"], null); dropUserData(); }
  }, [qc, dropUserData]);

  const value = useMemo<AuthState>(() => {
    const perms = new Set(me.data?.permissions ?? []);
    return {
      user: me.data ?? null,
      loading: me.isLoading,
      can: (p) => perms.has(p),
      login, logout, refresh: me.refetch,
    };
  }, [me.data, me.isLoading, me.refetch, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

/** Render children only when the user holds the permission (UI convenience — the backend enforces). */
export function Can({ permission, children, fallback = null }: { permission: string; children: ReactNode; fallback?: ReactNode }) {
  return <>{useAuth().can(permission) ? children : fallback}</>;
}
