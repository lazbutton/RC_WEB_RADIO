import { memo, type ReactNode } from "react";
import type { SearchHit } from "../../api";
import { Chip, catTone } from "../../components/Chip";
import { absoluteStamp, catClass, folderShort, initials, relativeShort } from "../../lib/format";
import { displayName } from "../../lib/threads";

export function highlight(text: string, q: string): ReactNode {
  const terms = (q || "").split(/\s+/).map((part) => part.trim()).filter((part) => part.length >= 2);
  if (!terms.length || !text) return text;
  const re = new RegExp(`(${terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  const parts = text.split(re);
  return parts.map((part, index) => (re.test(part) ? <mark key={index}>{part}</mark> : part));
}

export const HitRow = memo(function HitRow({
  hit,
  q,
  labels,
  selected,
  index,
  onSelect,
}: {
  hit: SearchHit;
  q: string;
  labels: Record<string, string>;
  selected: boolean;
  index: number;
  onSelect: (hit: SearchHit) => void;
}) {
  const who = displayName(hit.sender_name || "—");
  const archived = hit.folder && hit.folder !== "INBOX";
  return (
    <div className="row-shell is-hit" style={{ animationDelay: `${Math.min(index, 8) * 30}ms` }}>
      <div className="row-inner">
        <div
          className={`thread-row ${catClass(hit.category)}${selected ? " is-on" : ""}${hit.seen === false ? " is-unseen" : ""}`}
          role="option"
          aria-selected={selected}
          aria-current={selected ? "true" : undefined}
          aria-label={`${who}, ${hit.subject}`}
          tabIndex={selected ? 0 : -1}
          onClick={() => onSelect(hit)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter" || ev.key === " ") {
              ev.preventDefault();
              onSelect(hit);
            }
          }}
        >
          <span className="row-lead">
            <span className="initials" aria-hidden>
              {initials(who)}
            </span>
          </span>
          <span className="thread-copy">
            <span className="row-top">
              <span className="from">{who}</span>
              <time className="when" title={absoluteStamp(hit.created_rel)}>
                {relativeShort(hit.created_rel)}
              </time>
            </span>
            <span className="subject">
              <span className="subject-text">{highlight(hit.subject, q)}</span>
            </span>
            <span className="row-marks" aria-hidden>
              {hit.category ? <Chip tone={catTone(hit.category)}>{labels[hit.category] || hit.category}</Chip> : <Chip tone="quiet">Hors tri</Chip>}
              {archived ? <Chip tone="quiet" icon="archive">{folderShort(hit.folder)}</Chip> : null}
              {hit.status === "skipped" ? <Chip tone="quiet" icon="clock">Plus tard</Chip> : null}
              {hit.flagged ? <Chip tone="warn" icon="flag-filled">Drapeau</Chip> : null}
            </span>
            <span className="preview">{highlight(hit.hit || hit.excerpt, q)}</span>
          </span>
        </div>
      </div>
    </div>
  );
});
