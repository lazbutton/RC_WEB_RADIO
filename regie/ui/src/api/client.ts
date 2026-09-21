/** Client HTTP du noyau : cookie de session, en-tête anti-CSRF, erreurs lisibles. */

import { AuthError } from "../api";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function parse<T>(res: Response): Promise<T> {
  if (res.status === 401) throw new AuthError();
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as { detail?: { error?: string } | string; error?: string }) ?? {};
    const message =
      (typeof detail.detail === "object" && detail.detail?.error) || (typeof detail.detail === "string" ? detail.detail : undefined) || detail.error || res.statusText;
    throw new ApiError(String(message || "erreur"), res.status);
  }
  return data as T;
}

function init(method: string, body?: unknown): RequestInit {
  const headers: Record<string, string> = { "X-Regie": "1" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  return { method, credentials: "include", headers, body: body === undefined ? undefined : JSON.stringify(body) };
}

export const api = {
  get: <T>(url: string, signal?: AbortSignal) => fetch(url, { credentials: "include", signal }).then((res) => parse<T>(res)),
  post: <T>(url: string, body?: unknown) => fetch(url, init("POST", body ?? {})).then((res) => parse<T>(res)),
  put: <T>(url: string, body?: unknown) => fetch(url, init("PUT", body ?? {})).then((res) => parse<T>(res)),
  patch: <T>(url: string, body?: unknown) => fetch(url, init("PATCH", body ?? {})).then((res) => parse<T>(res)),
  delete: <T>(url: string, body?: unknown) => fetch(url, init("DELETE", body)).then((res) => parse<T>(res)),
};

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export type Me = {
  user: { id: string; email: string; name: string; role: string; prefs: Record<string, unknown> };
  permissions: Record<string, string>;
  unread_notifications: number;
};

export type EntitySummary = { kind: string; id: string; title: string; subtitle?: string; icon?: string; label?: string; url?: string; missing?: boolean; status?: string };
export type LinkRow = { id: number; other_kind: string; other_id: string; role: string; outgoing: boolean; other?: EntitySummary | null; created_at: string };
export type ActionRow = { id: number; module: string; kind: string; label: string; status: string; error: string; created_at: string; reversible: boolean; item_ids: string[]; actor_id?: string | null };
export type JobRow = { id: number; kind: string; lane: string; status: string; progress: string; error: string | null; created_at: string; finished_at: string; attempts: number };
export type Notification = { id: number; kind: string; text: string; url: string; created_at: string; read_at: string | null; entity_kind: string; entity_id: string };
export type RegistryKind = { kind: string; module: string; label: string; label_plural: string; icon: string; actions: string[] };
export type SearchHitRow = { kind: string; id: string; title: string; subtitle: string; url: string; hit: string; entity?: EntitySummary | null };

export const me = () => api.get<Me>("/api/v1/me");
export const registry = () => api.get<{ kinds: RegistryKind[]; actions: { kind: string; label: string; module: string }[]; modules: string[] }>("/api/v1/registry");
export const globalSearch = (q: string, signal?: AbortSignal) => api.get<{ hits: SearchHitRow[]; indexed: number }>(`/api/v1/search${qs({ q })}`, signal);
export const linksOf = (kind: string, id: string | number) => api.get<{ links: LinkRow[] }>(`/api/v1/links/${kind}/${id}`);
export const addLink = (body: { src_kind: string; src_id: string; dst_kind: string; dst_id: string; role?: string }) => api.post("/api/v1/links", body);
export const removeLink = (body: { src_kind: string; src_id: string; dst_kind: string; dst_id: string; role?: string }) => api.delete("/api/v1/links", body);
export const undo = (actionId: number) => api.post<{ ok: boolean; job_id?: number }>(`/api/v1/actions/${actionId}/undo`);
export const recentActions = (params: Record<string, string | number | undefined> = {}) => api.get<{ actions: ActionRow[] }>(`/api/v1/actions${qs(params)}`);
export const notifications = (unread = false) => api.get<{ notifications: Notification[]; unread: number }>(`/api/v1/notifications${qs({ unread: unread ? 1 : undefined })}`);
export const markRead = (ids?: number[]) => api.post("/api/v1/notifications/read", { ids });
export const systemStatus = () => api.get<Record<string, unknown>>("/api/v1/status");
export const listJobs = (limit = 40) => api.get<{ jobs: JobRow[] }>(`/api/v1/jobs${qs({ limit })}`);
export const kernelSettings = () => api.get<{ settings: Record<string, string>; modules: Record<string, Record<string, unknown>> }>("/api/v1/settings");
export const saveKernelSettings = (values: Record<string, string>) => api.patch("/api/v1/settings", { values });
export const users = () => api.get<{ users: { id: string; email: string; name: string; role: string; disabled: boolean; last_login_at: string }[] }>("/api/v1/users");
export const createUser = (body: { email: string; name: string; password: string; role: string }) => api.post("/api/v1/users", body);
export const patchUser = (id: string, body: Record<string, unknown>) => api.patch(`/api/v1/users/${id}`, body);
export const permissions = () => api.get<{ matrix: Record<string, Record<string, string>> }>("/api/v1/permissions");
export const setPermission = (body: { role: string; module: string; level: string }) => api.put("/api/v1/permissions", body);
export const patchMe = (body: Record<string, unknown>) => api.patch<{ user: Me["user"] }>("/api/v1/me", body);
export const filesList = (path = "") => api.get<{ path: string; writable: boolean; dirs: { name: string; path: string; writable: boolean }[]; files: { name: string; path: string; size: number; kind: string; mime: string; modified_at: string }[] }>(`/api/v1/files${qs({ path })}`);
