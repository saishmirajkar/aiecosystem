import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "@/hooks/auth";

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** Route-table fetch mock: first matching [method path-prefix] wins. */
export function mockFetch(routes: Record<string, (init?: RequestInit) => unknown | Response>) {
  const calls: { method: string; url: string; body?: unknown; headers: Record<string, string> }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined, headers: (init?.headers ?? {}) as Record<string, string> });
    const key = Object.keys(routes).find((k) => { const [m, p] = k.split(" "); return m === method && url.includes(p!); });
    if (!key) return jsonResponse({ error: { code: "not_found", message: `No mock for ${method} ${url}` } }, 404);
    const out = routes[key]!(init);
    return out instanceof Response ? out : jsonResponse(out);
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

export function renderApp(ui: ReactElement, { route = "/" } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MemoryRouter initialEntries={[route]}><AuthProvider>{ui}</AuthProvider></MemoryRouter></QueryClientProvider>,
  );
}

export const ME = {
  id: "u1", email: "admin@demo.example.com", full_name: "Asha Kulkarni", phone: null, job_title: null, is_active: true, last_login_at: null,
  created_at: "2026-11-18T00:00:00Z", roles: [{ id: "r1", code: "super_admin", name: "Super Administrator" }], departments: [],
  primary_department_id: null, warehouse_ids: [], permissions: ["inventory.view", "ai.access", "approvals.decide"],
  company: { id: "c1", name: "Demo Co", currency_code: "INR", timezone: "Asia/Kolkata", number_format: "en-IN" },
};
