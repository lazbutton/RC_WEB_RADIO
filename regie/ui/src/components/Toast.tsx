import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Button } from "./Button";
import { Icon } from "./Icon";

export type Toast = {
  id: number;
  text: string;
  tone?: "neutral" | "ok" | "warn" | "live";
  duration?: number;
  action?: { label: string; onClick: () => void | Promise<void> };
  sticky?: boolean;
};

type ToastCtx = {
  push: (toast: Omit<Toast, "id">) => number;
  dismiss: (id: number) => void;
  update: (id: number, patch: Partial<Toast>) => void;
};

const Ctx = createContext<ToastCtx>({ push: () => 0, dismiss: () => undefined, update: () => undefined });

export function useToasts(): ToastCtx {
  return useContext(Ctx);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const dismiss = useCallback((id: number) => setToasts((prev) => prev.filter((toast) => toast.id !== id)), []);
  const push = useCallback((toast: Omit<Toast, "id">) => {
    const id = ++seq.current;
    setToasts((prev) => [...prev.slice(-3), { ...toast, id }]);
    return id;
  }, []);
  const update = useCallback((id: number, patch: Partial<Toast>) => {
    setToasts((prev) => prev.map((toast) => (toast.id === id ? { ...toast, ...patch } : toast)));
  }, []);
  const value = useMemo(() => ({ push, dismiss, update }), [push, dismiss, update]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="toaster" aria-live="polite" aria-atomic="false">
        {toasts.map((toast) => (
          <ToastView key={toast.id} toast={toast} onDismiss={() => dismiss(toast.id)} />
        ))}
      </div>
    </Ctx.Provider>
  );
}

function ToastView({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const [paused, setPaused] = useState(false);
  const duration = toast.sticky ? 0 : toast.duration ?? 8000;
  const remaining = useRef(duration);
  const started = useRef(0);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!duration) return;
    if (paused) {
      if (timer.current) window.clearTimeout(timer.current);
      remaining.current -= Date.now() - started.current;
      return;
    }
    started.current = Date.now();
    timer.current = window.setTimeout(onDismiss, Math.max(200, remaining.current));
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [paused, duration, onDismiss]);

  return (
    <div
      className={`toast is-${toast.tone || "neutral"}${paused ? " is-paused" : ""}`}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      role="status"
    >
      <span className="toast-text">{toast.text}</span>
      {toast.action ? (
        <button
          type="button"
          className="toast-action"
          onClick={() => {
            void toast.action?.onClick();
            onDismiss();
          }}
        >
          {toast.action.label}
        </button>
      ) : null}
      <Button variant="icon" icon="close" onClick={onDismiss} aria-label="Fermer">
        Fermer
      </Button>
      {duration ? <span className="toast-timer" style={{ animationDuration: `${duration}ms` }} aria-hidden /> : null}
      {toast.tone === "ok" ? <Icon name="check" className="toast-icon" /> : null}
    </div>
  );
}
