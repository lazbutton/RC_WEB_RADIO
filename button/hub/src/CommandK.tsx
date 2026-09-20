import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ATELIER_LINK, openAtelier, type Atelier } from "./lib/urls";

type Props = {
  open: boolean;
  ateliers: Atelier[];
  onClose: () => void;
};

export function CommandK({ open, ateliers, onClose }: Props) {
  const reduce = useReducedMotion();
  const fade = reduce ? { duration: 0 } : { duration: 0.18, ease: [0.22, 1, 0.36, 1] as const };
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return ateliers;
    return ateliers.filter((item) => item.title.toLowerCase().includes(needle));
  }, [ateliers, query]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    const id = window.setTimeout(() => inputRef.current?.focus(), 20);
    return () => window.clearTimeout(id);
  }, [open]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive((i) => Math.min(filtered.length - 1, i + 1));
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive((i) => Math.max(0, i - 1));
      } else if (event.key === "Enter") {
        const hit = filtered[active];
        if (hit) {
          event.preventDefault();
          openAtelier(hit.href);
          onClose();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, filtered, onClose, open]);

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="dock"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={fade}
          onClick={onClose}
        >
          <motion.div
            className="dock-panel"
            role="dialog"
            aria-modal="true"
            aria-label="Ateliers"
            initial={reduce ? false : { opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: 6, scale: 0.98 }}
            transition={fade}
            onClick={(event) => event.stopPropagation()}
          >
            <input
              ref={inputRef}
              className="dock-input"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Ateliers"
              aria-label="Filtrer les ateliers"
            />
            <ul className="dock-list">
              {filtered.map((item, index) => (
                <li key={item.href}>
                  <a
                    className={index === active ? "is-active" : ""}
                    href={item.href}
                    {...ATELIER_LINK}
                    onClick={onClose}
                  >
                    {item.title}
                  </a>
                </li>
              ))}
            </ul>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
