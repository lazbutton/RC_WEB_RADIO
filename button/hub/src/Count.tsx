import { animate, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";

type Props = {
  value: number | null;
  ready: boolean;
  className?: string;
};

export function Count({ value, ready, className }: Props) {
  const reduce = useReducedMotion();
  const [shown, setShown] = useState(0);
  const shownRef = useRef(0);
  shownRef.current = shown;

  useEffect(() => {
    if (!ready || value == null) return;
    if (reduce) {
      setShown(value);
      return;
    }
    const controls = animate(shownRef.current, value, {
      duration: 0.22,
      ease: [0.22, 1, 0.36, 1],
      onUpdate: (next) => setShown(Math.round(next)),
    });
    return () => controls.stop();
  }, [ready, reduce, value]);

  if (!ready || value == null) return <span className={`skel skel-num ${className || ""}`} />;
  return <span className={className}>{shown}</span>;
}
