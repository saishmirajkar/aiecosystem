/** Typed API client. Session cookie (HttpOnly) + CSRF header; idempotency keys for postings. */
export interface ApiErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown> | null;
  request_id?: string;
}

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown> | null;
  requestId?: string;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.code = body.code;
    this.details = body.details ?? null;
    this.requestId = body.request_id;
  }

  /** Field errors from a 422 validation response, keyed by the last path segment. */
  fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    const errors = (this.details?.errors as { loc: string[]; msg: string }[] | undefined) ?? [];
    for (const e of errors) {
      const key = e.loc[e.loc.length - 1];
      if (key) out[key] = e.msg.replace(/^Value error, /, "");
    }
    return out;
  }
}

const BASE = "/api/v1";
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function qs(params?: Query): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function request<T>(method: string, path: string, body?: unknown, opts: { idempotencyKey?: string; query?: Query } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers["X-Requested-With"] = "above-web";
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(`${BASE}${path}${qs(opts.query)}`, { method, headers, body: payload, credentials: "same-origin" });
  if (res.status === 204) return undefined as T;
  const isJson = res.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    const err = new ApiError(res.status, (data as { error?: ApiErrorBody })?.error ?? { code: "http_error", message: res.statusText });
    if (res.status === 401 && !path.startsWith("/auth/login")) onUnauthorized?.();
    throw err;
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>("GET", path, undefined, { query }),
  post: <T>(path: string, body?: unknown, idempotencyKey?: string) => request<T>("POST", path, body, { idempotencyKey }),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, form),
  downloadUrl: (path: string, query?: Query) => `${BASE}${path}${qs(query)}`,
};

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}
