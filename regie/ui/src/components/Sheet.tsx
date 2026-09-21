import { useEffect, useRef, type ReactNode } from "react";
import { Button } from "./Button";

export function Sheet({
  open,
  title,
  onClose,
  children,
  wide,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const previous = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previous.current = document.activeElement as HTMLElement | null;
    const first = panel.current?.querySelector<HTMLElement>("input, button, [tabindex]");
    first?.focus();
    function onKey(ev: KeyboardEvent) {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        onClose();
      }
    }
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      previous.current?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="sheet-backdrop" onMouseDown={(ev) => ev.target === ev.currentTarget && onClose()}>
      <div className={`sheet${wide ? " is-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} ref={panel}>
        <div className="sheet-head">
          <p className="sheet-title">{title}</p>
          <Button variant="icon" icon="close" onClick={onClose} aria-label="Fermer">
            Fermer
          </Button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  );
}
