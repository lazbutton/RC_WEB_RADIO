import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  AuthError,
  EVENTS_URL,
  addToNotion,
  brief,
  getQueue,
  listActions,
  performAction,
  scan as apiScan,
  undoAction,
  type ActionKind,
  type ActionRow,
  type IndexStats,
  type Job,
  type MailItem,
  type QueuePayload,
} from "../api";
import { useAuth } from "../auth";
import { useToasts } from "../components/Toast";

export type View = "queue" | "history";

const ACTION_TOAST: Record<string, string> = {
  archive: "Archivé",
  later: "Mis de côté pour plus tard",
  restore: "Remis dans la boîte",
  unlater: "Remis à trier",
  seen: "Marqué lu",
  unseen: "Marqué non lu",
  flag: "Drapeau posé",
  unflag: "Drapeau retiré",
};

export const LEAVE_MS = 240;

type Store = {
  queue: QueuePayload | null;
  history: QueuePayload | null;
  loading: boolean;
  items: Record<number, MailItem>;
  leaving: Set<number>;
  jobs: Record<number, Job>;
  actions: ActionRow[];
  scanRunning: boolean;
  index: IndexStats;
  newIds: number[];
  connected: boolean;
  incident: string | null;
  refresh: (view?: View, reason?: string) => Promise<void>;
  act: (kind: ActionKind, ids: number[], opts?: { silent?: boolean; folder?: string; label?: string }) => Promise<boolean>;
  undo: (actionId: number) => Promise<void>;
  undoLast: () => Promise<void>;
  requestBrief: (id: number) => Promise<void>;
  requestNotion: (id: number, note: string) => Promise<void>;
  startScan: () => Promise<void>;
  clearNew: () => void;
  patchItem: (item: MailItem) => void;
  itemJob: (id: number, kind: string) => Job | null;
  setIncident: (value: string | null) => void;
};

const Ctx = createContext<Store | null>(null);

export function useStore(): Store {
  const store = useContext(Ctx);
  if (!store) throw new Error("StoreProvider manquant");
  return store;
}

function pluralMails(n: number): string {
  return n > 1 ? `${n} mails` : "1 mail";
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const { onLost } = useAuth();
  const toasts = useToasts();
  const [queue, setQueue] = useState<QueuePayload | null>(null);
  const [history, setHistory] = useState<QueuePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [items, setItems] = useState<Record<number, MailItem>>({});
  const [leaving, setLeaving] = useState<Set<number>>(() => new Set());
  const [jobs, setJobs] = useState<Record<number, Job>>({});
  const [actions, setActions] = useState<ActionRow[]>([]);
  const [scanRunning, setScanRunning] = useState(false);
  const [index, setIndex] = useState<IndexStats>({});
  const [newIds, setNewIds] = useState<number[]>([]);
  const [connected, setConnected] = useState(false);
  const [incident, setIncident] = useState<string | null>(null);
  const etags = useRef<Record<View, string | null>>({ queue: null, history: null });
  const knownIds = useRef<Set<number>>(new Set());
  const refreshTimer = useRef<number | null>(null);
  const inflight = useRef<Record<View, AbortController | null>>({ queue: null, history: null });

  const mergeItems = useCallback((rows: MailItem[]) => {
    if (!rows.length) return;
    setItems((prev) => {
      const next = { ...prev };
      for (const row of rows) next[row.id] = row;
      return next;
    });
  }, []);

  const patchItem = useCallback((item: MailItem) => mergeItems([item]), [mergeItems]);

  const refresh = useCallback(
    async (view: View = "queue", reason = "") => {
      inflight.current[view]?.abort();
      const ac = new AbortController();
      inflight.current[view] = ac;
      try {
        const res = await getQueue(null, null, view === "history" ? "done" : null, etags.current[view], ac.signal);
        if (res.unchanged || !res.payload) return;
        etags.current[view] = res.etag;
        const payload = res.payload;
        mergeItems(payload.items);
        setIncident(payload.incident);
        if (payload.index) setIndex((prev) => ({ ...prev, ...payload.index }));
        if (view === "history") {
          setHistory(payload);
        } else {
          const list = document.querySelector<HTMLElement>(".list-scroll");
          const scrolled = (list?.scrollTop || 0) > 140;
          const fresh = payload.items.filter((row) => !knownIds.current.has(row.id)).map((row) => row.id);
          if (reason === "scan" && scrolled && fresh.length && knownIds.current.size) {
            setNewIds((prev) => Array.from(new Set([...prev, ...fresh])));
          }
          knownIds.current = new Set(payload.items.map((row) => row.id));
          setQueue(payload);
          setLeaving((prev) => {
            if (!prev.size) return prev;
            const still = new Set<number>();
            for (const id of prev) if (payload.items.some((row) => row.id === id && row.status === "proposed")) still.add(id);
            return still.size === prev.size ? prev : still;
          });
        }
      } catch (err) {
        if (err instanceof AuthError) onLost();
        else if (!(err instanceof DOMException && err.name === "AbortError")) {
          setIncident(err instanceof Error ? err.message : "chargement impossible");
        }
      } finally {
        if (view === "queue") setLoading(false);
      }
    },
    [mergeItems, onLost],
  );

  const refreshSoon = useCallback(
    (reason: string) => {
      if (refreshTimer.current) window.clearTimeout(refreshTimer.current);
      refreshTimer.current = window.setTimeout(() => {
        void refresh("queue", reason);
        void refresh("history", reason);
      }, 250);
    },
    [refresh],
  );

  const loadActions = useCallback(async () => {
    try {
      setActions(await listActions(40));
    } catch (err) {
      if (err instanceof AuthError) onLost();
    }
  }, [onLost]);

  useEffect(() => {
    void refresh("queue");
    void loadActions();
  }, [refresh, loadActions]);

  useEffect(() => {
    let source: EventSource | null = null;
    let closed = false;
    function connect() {
      if (closed) return;
      source = new EventSource(EVENTS_URL, { withCredentials: true });
      source.onopen = () => setConnected(true);
      source.onerror = () => setConnected(false);
      source.addEventListener("item", (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent).data) as { item?: MailItem };
          if (data.item) patchItem(data.item);
        } catch {
          /* ignore */
        }
      });
      source.addEventListener("queue", (ev) => {
        let reason = "";
        try {
          reason = String((JSON.parse((ev as MessageEvent).data) as { reason?: string }).reason || "");
        } catch {
          /* ignore */
        }
        refreshSoon(reason);
      });
      source.addEventListener("job", (ev) => {
        try {
          const job = JSON.parse((ev as MessageEvent).data) as Job;
          setJobs((prev) => {
            const next = { ...prev, [job.id]: job };
            if (job.status === "done" || job.status === "failed") {
              window.setTimeout(() => setJobs((later) => {
                const copy = { ...later };
                delete copy[job.id];
                return copy;
              }), 1500);
            }
            return next;
          });
          if (job.kind === "scan") setScanRunning(job.status === "running" || job.status === "queued");
          if (job.kind === "index" && job.status === "done" && job.result) setIndex((prev) => ({ ...prev, ...(job.result as IndexStats) }));
          if (job.status === "failed" && job.error && ["brief", "notion", "action", "scan"].includes(job.kind)) {
            toasts.push({ text: job.error, tone: "live", duration: 7000 });
          }
        } catch {
          /* ignore */
        }
      });
      source.addEventListener("scan", (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent).data) as { status?: string; created?: number; manual?: boolean };
          setScanRunning(data.status === "running");
          if (data.status === "done" && data.manual) {
            toasts.push({ text: data.created ? `${pluralMails(data.created)} de plus à trier` : "Rien de nouveau", tone: "ok", duration: 3500 });
          }
        } catch {
          /* ignore */
        }
      });
      source.addEventListener("index", (ev) => {
        try {
          setIndex((prev) => ({ ...prev, ...(JSON.parse((ev as MessageEvent).data) as IndexStats) }));
        } catch {
          /* ignore */
        }
      });
      source.addEventListener("action", (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent).data) as { action?: ActionRow; status?: string };
          if (data.action) {
            const row = data.action;
            setActions((prev) => {
              const rest = prev.filter((entry) => entry.id !== row.id);
              return [row, ...rest].sort((a, b) => b.id - a.id).slice(0, 60);
            });
          }
        } catch {
          /* ignore */
        }
      });
    }
    connect();
    return () => {
      closed = true;
      source?.close();
    };
  }, [patchItem, refreshSoon, toasts]);

  const undo = useCallback(
    async (actionId: number) => {
      try {
        const result = await undoAction(actionId);
        mergeItems(result.items);
        setLeaving(new Set());
        await Promise.all([refresh("queue"), refresh("history")]);
        toasts.push({ text: `Annulé · ${ACTION_TOAST[result.action.kind] || result.action.label}`, tone: "ok", duration: 3000 });
      } catch (err) {
        if (err instanceof AuthError) onLost();
        else toasts.push({ text: err instanceof Error ? err.message : "annulation impossible", tone: "live" });
      }
    },
    [mergeItems, onLost, refresh, toasts],
  );

  const act = useCallback(
    async (kind: ActionKind, ids: number[], opts: { silent?: boolean; folder?: string; label?: string } = {}) => {
      const clean = Array.from(new Set(ids.filter(Boolean)));
      if (!clean.length) return false;
      const removes = kind === "archive" || kind === "later";
      setItems((prev) => {
        const next = { ...prev };
        for (const id of clean) {
          const row = next[id];
          if (!row) continue;
          if (kind === "seen" || kind === "unseen") next[id] = { ...row, seen: kind === "seen" };
          if (kind === "flag" || kind === "unflag") next[id] = { ...row, flagged: kind === "flag" };
          if (kind === "archive") next[id] = { ...row, status: "moved" };
          if (kind === "later") next[id] = { ...row, status: "skipped" };
          if (kind === "restore" || kind === "unlater") next[id] = { ...row, status: "proposed" };
        }
        return next;
      });
      if (removes) {
        setLeaving((prev) => new Set([...prev, ...clean]));
        window.setTimeout(() => {
          setQueue((prev) => (prev ? { ...prev, items: prev.items.filter((row) => !clean.includes(row.id)) } : prev));
          setLeaving((prev) => {
            const next = new Set(prev);
            for (const id of clean) next.delete(id);
            return next;
          });
        }, LEAVE_MS);
      }
      try {
        const result = await performAction(kind, clean, opts.folder);
        mergeItems(result.items);
        setActions((prev) => [result.action, ...prev.filter((entry) => entry.id !== result.action.id)].slice(0, 60));
        if (!opts.silent && (removes || kind === "restore" || kind === "unlater")) {
          const folder = kind === "archive" ? ((result.action.after?.folder as string) || "") : "";
          const label = opts.label || ACTION_TOAST[kind] || result.action.label;
          const detail = clean.length > 1 ? ` · ${pluralMails(clean.length)}` : folder ? ` · ${folder.split("/").pop()}` : "";
          toasts.push({
            text: `${label}${detail}`,
            tone: "neutral",
            duration: 8000,
            action: result.action.reversible ? { label: "Annuler", onClick: () => undo(result.action.id) } : undefined,
          });
        }
        if (!removes) refreshSoon("action");
        return true;
      } catch (err) {
        if (err instanceof AuthError) {
          onLost();
          return false;
        }
        toasts.push({ text: err instanceof Error ? err.message : "action impossible", tone: "live" });
        setLeaving(new Set());
        etags.current.queue = null;
        await refresh("queue");
        return false;
      }
    },
    [mergeItems, onLost, refresh, refreshSoon, toasts, undo],
  );

  const undoLast = useCallback(async () => {
    const last = actions.find((row) => row.reversible && (row.status === "done" || row.status === "pending"));
    if (!last) {
      toasts.push({ text: "Rien à annuler", tone: "neutral", duration: 2000 });
      return;
    }
    await undo(last.id);
  }, [actions, undo, toasts]);

  const requestBrief = useCallback(
    async (id: number) => {
      try {
        const res = await brief(id);
        if (res.item) patchItem(res.item);
      } catch (err) {
        if (err instanceof AuthError) onLost();
        else toasts.push({ text: err instanceof Error ? err.message : "récap impossible", tone: "live" });
      }
    },
    [onLost, patchItem, toasts],
  );

  const requestNotion = useCallback(
    async (id: number, note: string) => {
      try {
        const res = await addToNotion(id, note);
        if (res.item) patchItem(res.item);
      } catch (err) {
        if (err instanceof AuthError) onLost();
        else toasts.push({ text: err instanceof Error ? err.message : "Notion impossible", tone: "live" });
      }
    },
    [onLost, patchItem, toasts],
  );

  const startScan = useCallback(async () => {
    try {
      setScanRunning(true);
      await apiScan();
    } catch (err) {
      setScanRunning(false);
      if (err instanceof AuthError) onLost();
      else toasts.push({ text: err instanceof Error ? err.message : "relevé impossible", tone: "live" });
    }
  }, [onLost, toasts]);

  const clearNew = useCallback(() => setNewIds([]), []);

  const itemJob = useCallback(
    (id: number, kind: string): Job | null => {
      for (const job of Object.values(jobs)) {
        if (job.kind === kind && job.payload?.item_id === id && (job.status === "running" || job.status === "queued")) return job;
      }
      return null;
    },
    [jobs],
  );

  const value = useMemo<Store>(
    () => ({
      queue,
      history,
      loading,
      items,
      leaving,
      jobs,
      actions,
      scanRunning,
      index,
      newIds,
      connected,
      incident,
      refresh,
      act,
      undo,
      undoLast,
      requestBrief,
      requestNotion,
      startScan,
      clearNew,
      patchItem,
      itemJob,
      setIncident,
    }),
    [queue, history, loading, items, leaving, jobs, actions, scanRunning, index, newIds, connected, incident, refresh, act, undo, undoLast, requestBrief, requestNotion, startScan, clearNew, patchItem, itemJob],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
