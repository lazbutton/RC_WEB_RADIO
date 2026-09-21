import { useLayoutEffect, useRef, useState } from "react";

export type Segment = { id: string; label: string; count?: number; tone?: string };

export function SegmentedTabs({
  segments,
  value,
  onChange,
  ariaLabel,
  className = "",
}: {
  segments: Segment[];
  value: string;
  onChange: (id: string) => void;
  ariaLabel: string;
  className?: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [ink, setInk] = useState<{ left: number; width: number } | null>(null);

  useLayoutEffect(() => {
    const root = wrap.current;
    if (!root) return;
    const active = root.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!active) {
      setInk(null);
      return;
    }
    setInk({ left: active.offsetLeft, width: active.offsetWidth });
  }, [value, segments.map((segment) => `${segment.id}:${segment.count ?? ""}`).join("|")]);

  return (
    <div className={`segments ${className}`} role="tablist" aria-label={ariaLabel} ref={wrap}>
      {segments.map((segment) => (
        <button
          key={segment.id}
          type="button"
          role="tab"
          aria-selected={segment.id === value}
          className={`segment${segment.tone ? ` is-${segment.tone}` : ""}`}
          onClick={() => onChange(segment.id)}
        >
          <span>{segment.label}</span>
          {segment.count ? (
            <span key={segment.count} className="segment-count">
              {segment.count}
            </span>
          ) : null}
        </button>
      ))}
      {ink ? <span className="segment-ink" style={{ transform: `translateX(${ink.left}px)`, width: ink.width }} aria-hidden /> : null}
    </div>
  );
}
