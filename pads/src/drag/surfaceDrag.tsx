import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import type { CategoryId, LibraryFolder, LibrarySound, PadAssign } from "../types";

export type DragSource =
  | { type: "lib"; sound: LibrarySound }
  | { type: "folder"; folder: LibraryFolder }
  | { type: "slot"; note: number; assign: PadAssign };

export type DropTarget = {
  note: number | null;
  categoryId: CategoryId | null;
};

type Session = {
  source: DragSource;
  x: number;
  y: number;
  overNote: number | null;
  overCategory: CategoryId | null;
};

type DragApi = {
  session: Session | null;
  dragging: boolean;
  begin: (source: DragSource, event: ReactPointerEvent<HTMLElement>) => void;
  blockClick: () => boolean;
};

const DragContext = createContext<DragApi | null>(null);
const THRESHOLD = 7;

function hitPad(x: number, y: number): number | null {
  const node = document.elementFromPoint(x, y);
  const cell = node instanceof Element ? node.closest("[data-pad-note]") : null;
  if (!cell) return null;
  const note = Number(cell.getAttribute("data-pad-note"));
  return Number.isInteger(note) && note >= 0 && note < 64 ? note : null;
}

function hitCategory(x: number, y: number): CategoryId | null {
  const node = document.elementFromPoint(x, y);
  const cart = node instanceof Element ? node.closest("[data-category-id]") : null;
  const id = cart?.getAttribute("data-category-id");
  return id || null;
}

function hitTarget(x: number, y: number): DropTarget {
  const note = hitPad(x, y);
  if (note != null) return { note, categoryId: null };
  return { note: null, categoryId: hitCategory(x, y) };
}

function overlayOf(source: DragSource): { title: string; color: PadAssign["color"] } {
  if (source.type === "slot") return source.assign;
  if (source.type === "folder") return source.folder;
  return source.sound;
}

export function SurfaceDrag({
  onDrop,
  locked = false,
  children,
}: {
  onDrop: (source: DragSource, target: DropTarget) => void;
  locked?: boolean;
  children: ReactNode;
}) {
  const [session, setSession] = useState<Session | null>(null);
  const pending = useRef<{
    source: DragSource;
    x: number;
    y: number;
    pointerId: number;
    target: HTMLElement;
  } | null>(null);
  const live = useRef<Session | null>(null);
  const ateClick = useRef(false);
  const onDropRef = useRef(onDrop);
  onDropRef.current = onDrop;
  const lockedRef = useRef(locked);
  lockedRef.current = locked;

  const finish = useCallback((target: DropTarget) => {
    const current = live.current;
    ateClick.current = current != null;
    pending.current = null;
    live.current = null;
    setSession(null);
    if (current) onDropRef.current(current.source, target);
  }, []);

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const start = pending.current;
      if (start && !live.current) {
        const dx = event.clientX - start.x;
        const dy = event.clientY - start.y;
        if (dx * dx + dy * dy < THRESHOLD * THRESHOLD) return;
        try {
          start.target.setPointerCapture(start.pointerId);
        } catch {
          /* déjà capturé */
        }
        const hit = hitTarget(event.clientX, event.clientY);
        const next: Session = {
          source: start.source,
          x: event.clientX,
          y: event.clientY,
          overNote: hit.note,
          overCategory: hit.categoryId,
        };
        live.current = next;
        setSession(next);
        return;
      }
      if (!live.current) return;
      const hit = hitTarget(event.clientX, event.clientY);
      const next: Session = {
        ...live.current,
        x: event.clientX,
        y: event.clientY,
        overNote: hit.note,
        overCategory: hit.categoryId,
      };
      live.current = next;
      setSession(next);
    };

    const up = (event: PointerEvent) => {
      if (live.current) {
        finish(hitTarget(event.clientX, event.clientY));
        return;
      }
      pending.current = null;
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
  }, [finish]);

  const begin = useCallback((source: DragSource, event: ReactPointerEvent<HTMLElement>) => {
    if (lockedRef.current || event.button !== 0) return;
    event.preventDefault();
    ateClick.current = false;
    pending.current = {
      source,
      x: event.clientX,
      y: event.clientY,
      pointerId: event.pointerId,
      target: event.currentTarget,
    };
  }, []);

  const blockClick = useCallback(() => {
    if (!ateClick.current) return false;
    ateClick.current = false;
    return true;
  }, []);

  const overlay = session ? overlayOf(session.source) : null;
  const dragging = session != null;

  return (
    <DragContext.Provider value={{ session, dragging, begin, blockClick }}>
      {children}
      {session && overlay ? (
        <div
          className="overlay"
          data-color={overlay.color}
          style={{ transform: `translate3d(${session.x + 12}px, ${session.y + 12}px, 0)` }}
        >
          <span>{overlay.title}</span>
        </div>
      ) : null}
    </DragContext.Provider>
  );
}

export function useSurfaceDrag(): DragApi {
  const ctx = useContext(DragContext);
  if (!ctx) throw new Error("useSurfaceDrag hors SurfaceDrag");
  return ctx;
}
