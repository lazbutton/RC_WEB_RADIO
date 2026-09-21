import { memo, type MouseEvent } from "react";
import type { ActionKind } from "../../api";
import { Checkbox } from "../../components/Checkbox";
import { Chip, catTone } from "../../components/Chip";
import { Icon } from "../../components/Icon";
import { absoluteStamp, catClass, initials, relativeShort } from "../../lib/format";
import { displayName, type Thread } from "../../lib/threads";

export type QuickAction = { kind: ActionKind; ids: number[] };

type Props = {
  thread: Thread;
  labels: Record<string, string>;
  selected: boolean;
  leaving: boolean;
  checked: boolean;
  selectionMode: boolean;
  canMove: boolean;
  history?: boolean;
  onSelect: (thread: Thread) => void;
  onToggleCheck: (thread: Thread, ev: MouseEvent) => void;
  onQuick: (action: QuickAction, thread: Thread) => void;
};

export const ThreadRow = memo(function ThreadRow({
  thread,
  labels,
  selected,
  leaving,
  checked,
  selectionMode,
  canMove,
  history,
  onSelect,
  onToggleCheck,
  onQuick,
}: Props) {
  const who = displayName(thread.latest.sender_name || "—");
  const quiet = thread.category === "newsletters" || thread.category === "spam";
  const pj = thread.pieces.length;
  const label = labels[thread.category] || thread.category;
  const classes = [
    "row-shell",
    leaving ? "is-leaving" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const rowClasses = [
    "thread-row",
    catClass(thread.category),
    quiet ? "is-quiet" : "",
    selected ? "is-on" : "",
    thread.unseen ? "is-unseen" : "",
    checked ? "is-checked" : "",
    selectionMode ? "is-selecting" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const status = thread.latest.status;
  return (
    <div className={classes}>
      <div className="row-inner">
        <div
          className={rowClasses}
          role="option"
          aria-selected={selected}
          aria-current={selected ? "true" : undefined}
          aria-label={`${who}, ${thread.title}, ${label}${thread.unseen ? ", non lu" : ""}`}
          tabIndex={selected ? 0 : -1}
          data-thread={thread.key}
          onClick={() => onSelect(thread)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter" || ev.key === " ") {
              ev.preventDefault();
              onSelect(thread);
            }
          }}
        >
          <span className="row-lead">
            <Checkbox checked={checked} onToggle={(ev) => onToggleCheck(thread, ev)} label={`Sélectionner ${thread.title}`} className="row-check" />
            <span className={`initials${thread.unseen ? " is-unseen" : ""}`} aria-hidden>
              {initials(who)}
            </span>
          </span>
          <span className="thread-copy">
            <span className="row-top">
              <span className="from">
                {thread.unseen ? <span className="unread-dot" aria-hidden /> : null}
                {who}
                {thread.items.length > 1 ? <span className="count">{thread.items.length}</span> : null}
              </span>
              <time className="when" title={absoluteStamp(thread.latest.created_rel)} dateTime={thread.latest.mailed_at || undefined}>
                {relativeShort(thread.latest.created_rel)}
              </time>
            </span>
            <span className="subject">
              <span className="subject-text">{thread.title}</span>
            </span>
            <span className="row-marks" aria-hidden>
              <Chip tone={catTone(thread.category)}>{label}</Chip>
              {history && status ? <Chip tone="quiet">{status === "moved" ? "Archivé" : "Plus tard"}</Chip> : null}
              {thread.flagged ? (
                <Chip tone="warn" icon="flag-filled">
                  Drapeau
                </Chip>
              ) : null}
              {pj ? <Chip tone="text" icon="paperclip">{pj} PJ</Chip> : null}
              {thread.notionUrl ? <Chip tone="ok" icon="notion">Notion</Chip> : null}
            </span>
            <span className="preview">{thread.recap}</span>
          </span>
          {!history ? (
            <span className="row-quick" aria-hidden>
              {canMove ? (
                <button type="button" className="quick" title="Archiver (e)" tabIndex={-1} onClick={(ev) => { ev.stopPropagation(); onQuick({ kind: "archive", ids: thread.ids }, thread); }}>
                  <Icon name="archive" />
                </button>
              ) : null}
              <button
                type="button"
                className="quick"
                title={thread.unseen ? "Marquer lu (u)" : "Marquer non lu (u)"}
                tabIndex={-1}
                onClick={(ev) => { ev.stopPropagation(); onQuick({ kind: thread.unseen ? "seen" : "unseen", ids: thread.ids }, thread); }}
              >
                <Icon name={thread.unseen ? "mail-open" : "mail"} />
              </button>
              <button
                type="button"
                className={`quick${thread.flagged ? " is-on" : ""}`}
                title={thread.flagged ? "Retirer le drapeau (s)" : "Drapeau (s)"}
                tabIndex={-1}
                onClick={(ev) => { ev.stopPropagation(); onQuick({ kind: thread.flagged ? "unflag" : "flag", ids: thread.ids }, thread); }}
              >
                <Icon name={thread.flagged ? "flag-filled" : "flag"} />
              </button>
              <button type="button" className="quick" title="Plus tard (l)" tabIndex={-1} onClick={(ev) => { ev.stopPropagation(); onQuick({ kind: "later", ids: thread.ids }, thread); }}>
                <Icon name="clock" />
              </button>
            </span>
          ) : null}
        </div>
      </div>
    </div>
  );
});
