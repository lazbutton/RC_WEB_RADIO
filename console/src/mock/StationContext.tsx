import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { itemsByKind, type CatalogItem, type ClockDef } from "./catalog";
import { buildPastQueue, buildQueue, defaultClock, type QueueItem } from "./buildQueue";

const MIN_AHEAD = 10;
const MIN_AHEAD_SEC = 30 * 60;

function aheadSec(items: QueueItem[]): number {
  return items.reduce((sum, row) => sum + row.item.durationSec, 0);
}

export type StationState = {
  queue: QueueItem[];
  elapsedSec: number;
  nowDurationSec: number;
  clock: ClockDef;
  nowItem: QueueItem | undefined;
  nextItem: QueueItem | undefined;
  remainingSec: number;
  nextCart: CatalogItem | undefined;
  nextJingle: CatalogItem | undefined;
  nextAutodj: CatalogItem | undefined;
  history: QueueItem[];
  conductor: QueueItem[];
  upcoming: QueueItem[];
  skip: () => void;
  insertNow: (item: CatalogItem) => void;
  insertSonThenJingle: (son: CatalogItem, jingle: CatalogItem) => void;
  moveInQueue: (fromUid: string, toUid: string) => void;
  removeFromQueue: (uid: string) => void;
};

const StationContext = createContext<StationState | null>(null);

function cuesFrom(queue: QueueItem[]) {
  const rest = queue.slice(1).map((q) => q.item);
  return {
    nextCart: rest.find((i) => i.kind === "son" || i.kind === "pub"),
    nextJingle: rest.find((i) => i.kind === "jingle"),
    nextAutodj: rest.find((i) => i.kind === "musique"),
  };
}

export function StationProvider({ children }: { children: ReactNode }) {
  const boot = useMemo(() => {
    const now = new Date();
    const clock = defaultClock(now);
    return {
      clock,
      queue: buildQueue(clock, now),
      history: buildPastQueue(clock),
    };
  }, []);

  const [clock] = useState<ClockDef>(boot.clock);
  const [queue, setQueue] = useState<QueueItem[]>(boot.queue);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [history, setHistory] = useState<QueueItem[]>(boot.history);
  const queueRef = useRef(queue);
  queueRef.current = queue;

  const refill = useCallback((current: QueueItem[], clk: ClockDef) => {
    if (current.length >= MIN_AHEAD && aheadSec(current) >= MIN_AHEAD_SEC) return current;
    const extra = buildQueue(clk, new Date());
    const next = [...current];
    for (const row of extra) {
      if (next.length >= MIN_AHEAD && aheadSec(next) >= MIN_AHEAD_SEC) break;
      next.push(row);
    }
    return next;
  }, []);

  const leaveCurrent = useCallback(() => {
    const leaving = queueRef.current[0];
    if (!leaving) return;
    setHistory((h) => [...h, leaving]);
  }, []);

  const skip = useCallback(() => {
    leaveCurrent();
    setQueue((prev) => {
      const next = refill(prev.slice(1), clock);
      return next.length ? next : prev;
    });
    setElapsedSec(0);
  }, [clock, leaveCurrent, refill]);

  const insertNow = useCallback(
    (item: CatalogItem) => {
      leaveCurrent();
      setQueue((prev) =>
        refill([{ uid: `cut-${item.id}-${Date.now()}`, item, role: "seq" }, ...prev.slice(1)], clock),
      );
      setElapsedSec(0);
    },
    [clock, leaveCurrent, refill],
  );

  const insertSonThenJingle = useCallback(
    (son: CatalogItem, jingle: CatalogItem) => {
      leaveCurrent();
      const t = Date.now();
      setQueue((prev) =>
        refill(
          [
            { uid: `cut-${son.id}-${t}`, item: son, role: "seq" },
            { uid: `cut-${jingle.id}-${t}`, item: jingle, role: "seq" },
            ...prev.slice(1),
          ],
          clock,
        ),
      );
      setElapsedSec(0);
    },
    [clock, leaveCurrent, refill],
  );

  const moveInQueue = useCallback((fromUid: string, toUid: string) => {
    setQueue((prev) => {
      const from = prev.findIndex((q) => q.uid === fromUid);
      if (from < 1 || fromUid === toUid) return prev;
      if (prev[from]?.role === "anchor") return prev;
      const next = [...prev];
      const [row] = next.splice(from, 1);
      if (!row) return prev;
      const insertAt = next.findIndex((q) => q.uid === toUid);
      if (insertAt < 1) return prev;
      next.splice(insertAt, 0, row);
      return next;
    });
  }, []);

  const removeFromQueue = useCallback(
    (uid: string) => {
      setQueue((prev) => {
        const i = prev.findIndex((q) => q.uid === uid);
        if (i < 1) return prev;
        if (prev[i]?.role === "anchor") return prev;
        return refill(
          prev.filter((q) => q.uid !== uid),
          clock,
        );
      });
    },
    [clock, refill],
  );

  const nowItem = queue[0];
  const nowDurationSec = nowItem?.item.durationSec ?? 180;

  useEffect(() => {
    const id = window.setInterval(() => setElapsedSec((sec) => sec + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!nowItem) return;
    if (elapsedSec < nowDurationSec) return;
    skip();
  }, [elapsedSec, nowDurationSec, nowItem, skip]);

  const { nextCart, nextJingle, nextAutodj } = useMemo(() => cuesFrom(queue), [queue]);
  const conductor = useMemo(() => [...history, ...queue], [history, queue]);

  const value: StationState = {
    queue,
    elapsedSec: Math.min(elapsedSec, nowDurationSec),
    nowDurationSec,
    clock,
    nowItem,
    nextItem: queue[1],
    remainingSec: Math.max(0, nowDurationSec - Math.min(elapsedSec, nowDurationSec)),
    nextCart,
    nextJingle,
    nextAutodj,
    history,
    conductor,
    upcoming: queue.slice(1, 11),
    skip,
    insertNow,
    insertSonThenJingle,
    moveInQueue,
    removeFromQueue,
  };

  return <StationContext.Provider value={value}>{children}</StationContext.Provider>;
}

export function useStation(): StationState {
  const ctx = useContext(StationContext);
  if (!ctx) throw new Error("useStation hors StationProvider");
  return ctx;
}

export function padItems(kind: CatalogItem["kind"], n = 6): CatalogItem[] {
  return itemsByKind(kind).slice(0, n);
}
