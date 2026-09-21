export const CATEGORIES = ["todo", "waiting", "read", "newsletters", "spam"] as const;

export type Category = (typeof CATEGORIES)[number];

export type MailAttachment = {
  n: number;
  filename: string;
  content_type: string;
  size: number;
  kind: string;
  status: string;
  error: string;
  note: string;
  text: string;
};

export type MailItem = {
  id: number;
  imap_uid: string;
  uidvalidity: string;
  message_id?: string;
  in_reply_to?: string;
  subject: string;
  sender: string;
  sender_name: string;
  sender_email: string;
  excerpt: string;
  category: string;
  reason: string;
  error: string | null;
  draft: string;
  summary: string;
  needs_brief?: boolean;
  status: string;
  mailed_at: string;
  created_at: string;
  created_rel: string;
  notion_url?: string;
  attachments?: MailAttachment[];
  seen: boolean;
  flagged: boolean;
  folder: string;
};

export type IndexStats = { indexed?: number; inbox?: number; added?: number; running?: boolean };

export type QueuePayload = {
  items: MailItem[];
  selected_id: number | null;
  cat: string;
  status: "proposed" | "done";
  incident: string | null;
  imap_ok: boolean | null;
  imap_ready: boolean;
  llm_ready: boolean;
  last_scan_at: string | null;
  counts: Record<string, number>;
  cat_counts: Record<string, number>;
  unread: number;
  labels: Record<string, string>;
  categories: string[];
  token_missing: boolean;
  last_scan_stats?: Record<string, number>;
  last_scan_usage?: string;
  notion_ready?: boolean;
  index?: IndexStats;
  can_move?: boolean;
  mark_read_on_open?: boolean;
  density?: string;
  treated_today?: number;
  folders?: Record<string, string>;
  version?: string;
};

export type SessionInfo = {
  ok: boolean;
  connected?: boolean;
  folder_count?: number;
  inbox_messages?: number | null;
  inbox_unseen?: number | null;
  last_ok?: string | null;
  error?: string | null;
  capabilities?: string[];
  can_move?: boolean;
  folders?: string[];
};

export type WorkerStatus = {
  imap_ok: boolean | null;
  last_error: string | null;
  last_scan_at: string | null;
  last_scan_count: number;
  last_scan_stats: Record<string, number>;
  last_index: IndexStats;
  last_index_error: string | null;
  session: SessionInfo | null;
  jobs: { lanes: Record<string, { current: Job | null; queued: Job[]; queued_count: number }> };
};

export type SettingsPayload = QueuePayload & {
  extra_prompt: string;
  signature: string;
  mark_read_on_open: boolean;
  density: string;
  imap: SessionInfo | null;
  imap_user: string;
  imap_host: string;
  imap_port: number;
  notion_token_set?: boolean;
  notion_status?: {
    ok: boolean;
    taches?: boolean;
    projets?: boolean;
    radio_campus?: boolean;
    view_only?: boolean;
    detail?: string;
  } | null;
  anthropic_model?: string;
  anthropic_fast_model?: string;
  anthropic_effort?: string;
  feed_md?: string;
  feed_atom?: string;
  worker?: WorkerStatus;
  scan_days?: number;
};

export type SearchHit = {
  id: number;
  item_id?: number | null;
  subject: string;
  sender: string;
  sender_name: string;
  excerpt: string;
  hit: string;
  category: string;
  status: string;
  seen: boolean;
  flagged: boolean;
  folder: string;
  mailed_at: string;
  created_rel: string;
  item: MailItem | null;
};

export type SearchPayload = {
  q: string;
  hits: SearchHit[];
  indexed: number;
  inbox: number;
};

export type ActionKind = "seen" | "unseen" | "flag" | "unflag" | "archive" | "later" | "unlater" | "restore";

export type ActionRow = {
  id: number;
  kind: ActionKind | string;
  label: string;
  item_ids: number[];
  status: string;
  error: string;
  created_at: string;
  created_rel: string;
  done_at?: string | null;
  undone_at?: string | null;
  after?: Record<string, unknown>;
  reversible: boolean;
};

export type ActionResult = {
  ok: boolean;
  action: ActionRow;
  job_id: number | null;
  items: MailItem[];
  undone_id?: number;
};

export type Job = {
  id: number;
  kind: string;
  status: "queued" | "running" | "done" | "failed" | string;
  progress: string;
  error: string | null;
  priority: number;
  payload: { item_id?: number; item_ids?: number[]; n?: number; action_id?: number; manual?: boolean };
  result: Record<string, unknown>;
};

export class AuthError extends Error {
  constructor() {
    super("auth");
    this.name = "AuthError";
  }
}

async function parse(res: Response): Promise<unknown> {
  if (res.status === 401) throw new AuthError();
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = typeof data === "object" && data && "error" in data ? String((data as { error: unknown }).error) : res.statusText;
    throw new Error(err || "erreur");
  }
  return data;
}

function json(method: string, body: unknown): RequestInit {
  return { method, credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export async function login(token: string): Promise<void> {
  await parse(await fetch("/api/login", json("POST", { token })));
}

export async function logout(): Promise<void> {
  await fetch("/api/logout", { method: "POST", credentials: "include" });
}

export type QueueResponse = { payload: QueuePayload | null; etag: string | null; unchanged: boolean };

export async function getQueue(
  id?: string | null,
  cat?: string | null,
  status?: string | null,
  etag?: string | null,
  signal?: AbortSignal,
): Promise<QueueResponse> {
  const q = new URLSearchParams();
  if (id) q.set("id", id);
  if (cat) q.set("cat", cat);
  if (status) q.set("status", status);
  const suffix = q.toString() ? `?${q}` : "";
  const headers: Record<string, string> = {};
  if (etag) headers["If-None-Match"] = etag;
  const res = await fetch(`/api/queue${suffix}`, { credentials: "include", headers, signal });
  if (res.status === 304) return { payload: null, etag: etag ?? null, unchanged: true };
  const payload = (await parse(res)) as QueuePayload;
  return { payload, etag: res.headers.get("etag"), unchanged: false };
}

export async function getItem(id: number): Promise<MailItem | null> {
  const data = (await parse(await fetch(`/api/items/${id}`, { credentials: "include" }))) as { item?: MailItem | null };
  return data.item ?? null;
}

export async function searchMails(q: string, signal?: AbortSignal): Promise<SearchPayload> {
  const params = new URLSearchParams({ q });
  return (await parse(await fetch(`/api/search?${params}`, { credentials: "include", signal }))) as SearchPayload;
}

export async function getMail(indexId: number): Promise<{ hit: SearchHit; body: string }> {
  return (await parse(await fetch(`/api/mails/${indexId}`, { credentials: "include" }))) as { hit: SearchHit; body: string };
}

export async function adoptMail(indexId: number, queue: boolean): Promise<MailItem | null> {
  const data = (await parse(await fetch(`/api/mails/${indexId}/adopt`, json("POST", { queue })))) as { item?: MailItem | null };
  return data.item ?? null;
}

export async function scan(): Promise<{ job_id?: number }> {
  return (await parse(await fetch("/api/scan", { method: "POST", credentials: "include" }))) as { job_id?: number };
}

export async function performAction(kind: ActionKind, ids: number[], folder?: string): Promise<ActionResult> {
  return (await parse(await fetch("/api/actions", json("POST", { ids, kind, folder })))) as ActionResult;
}

export async function undoAction(actionId: number): Promise<ActionResult> {
  return (await parse(await fetch(`/api/actions/${actionId}/undo`, { method: "POST", credentials: "include" }))) as ActionResult;
}

export async function listActions(limit = 40): Promise<ActionRow[]> {
  const data = (await parse(await fetch(`/api/actions?limit=${limit}`, { credentials: "include" }))) as { actions?: ActionRow[] };
  return data.actions ?? [];
}

export async function brief(id: number): Promise<{ job_id?: number; item?: MailItem | null }> {
  return (await parse(await fetch(`/api/items/${id}/brief`, { method: "POST", credentials: "include" }))) as {
    job_id?: number;
    item?: MailItem | null;
  };
}

export async function addToNotion(id: number, note: string): Promise<{ job_id?: number; url?: string; item?: MailItem | null }> {
  return (await parse(await fetch(`/api/items/${id}/notion`, json("POST", { note })))) as {
    job_id?: number;
    url?: string;
    item?: MailItem | null;
  };
}

export async function getJob(id: number): Promise<Job | null> {
  const data = (await parse(await fetch(`/api/jobs/${id}`, { credentials: "include" }))) as { job?: Job | null };
  return data.job ?? null;
}

export async function openAttachment(itemId: number, n: number): Promise<Blob> {
  const res = await fetch(`/api/items/${itemId}/attachments/${n}`, { credentials: "include" });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const err =
      typeof data === "object" && data && "error" in data ? String((data as { error: unknown }).error) : res.statusText;
    throw new Error(err || "pièce refusée");
  }
  return res.blob();
}

export async function getSettings(probe = false): Promise<SettingsPayload> {
  return (await parse(await fetch(`/api/settings${probe ? "?probe=1" : ""}`, { credentials: "include" }))) as SettingsPayload;
}

export async function saveSettings(body: {
  extra_prompt: string;
  signature: string;
  notion_token?: string;
  mark_read_on_open?: boolean;
  density?: string;
}): Promise<void> {
  await parse(await fetch("/api/settings", json("POST", body)));
}

export const EVENTS_URL = "/api/events";
