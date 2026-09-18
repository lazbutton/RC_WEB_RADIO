import { useEffect, useRef } from "react";
import { motion, useReducedMotion } from "motion/react";
import { PAD_COLORS } from "../lib/padColor";
import type { PadColor } from "../types";

export function ColorMenu({
  x,
  y,
  current,
  onPick,
  onClose,
}: {
  x: number;
  y: number;
  current: PadColor;
  onPick: (color: PadColor) => void;
  onClose: () => void;
}) {
  const reduce = useReducedMotion();
  const root = useRef<HTMLDivElement>(null);
  const left = Math.min(Math.max(8, x), window.innerWidth - 188);
  const top = Math.min(Math.max(8, y), window.innerHeight - 118);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    const onPointer = (event: PointerEvent) => {
      if (root.current?.contains(event.target as Node)) return;
      onClose();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onPointer);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onPointer);
    };
  }, [onClose]);

  return (
    <motion.div
      ref={root}
      className="color-menu"
      role="menu"
      aria-label="Couleur du pad"
      style={{ left, top }}
      initial={reduce ? false : { opacity: 0, scale: 0.96, y: 4 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ duration: reduce ? 0 : 0.12, ease: [0.22, 1, 0.36, 1] }}
    >
      <p className="color-menu-kicker">Couleur</p>
      <div className="color-swatches">
        {PAD_COLORS.map((row) => (
          <button
            key={row.id}
            type="button"
            role="menuitemradio"
            aria-checked={row.id === current}
            aria-label={row.label}
            className={`color-swatch${row.id === current ? " is-current" : ""}`}
            style={{ background: row.hex }}
            onClick={() => onPick(row.id)}
          />
        ))}
      </div>
    </motion.div>
  );
}
