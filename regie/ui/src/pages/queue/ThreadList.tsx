import { type MouseEvent, type ReactNode, type RefObject } from "react";
import type { SearchHit } from "../../api";
import { Button } from "../../components/Button";
import { ProgressLine } from "../../components/ProgressLine";
import { RowSkeleton } from "../../components/Skeleton";
import type { Thread, ThreadSection } from "../../lib/threads";
import { EmptyState } from "./EmptyState";
import { HitRow } from "./HitRow";
import { ThreadRow, type QuickAction } from "./ThreadRow";

export function ThreadList({
  view,
  loading,
  sections,
  hits,
  searching,
  searchBusy,
  q,
  cat,
  selectedKey,
  selectedHitId,
  leaving,
  checked,
  labels,
  canMove,
  newCount,
  scanRunning,
  treatedToday,
  tools,
  scrollRef,
  onSelect,
  onSelectHit,
  onToggleCheck,
  onQuick,
  onShowNew,
}: {
  view: "queue" | "history";
  loading: boolean;
  sections: ThreadSection[];
  hits: SearchHit[] | null;
  searching: boolean;
  searchBusy: boolean;
  q: string;
  cat: string;
  selectedKey: string | null;
  selectedHitId: number | null;
  leaving: Set<number>;
  checked: Set<string>;
  labels: Record<string, string>;
  canMove: boolean;
  newCount: number;
  scanRunning: boolean;
  treatedToday: number;
  tools: ReactNode;
  scrollRef: RefObject<HTMLDivElement | null>;
  onSelect: (thread: Thread) => void;
  onSelectHit: (hit: SearchHit) => void;
  onToggleCheck: (thread: Thread, ev: MouseEvent) => void;
  onQuick: (action: QuickAction, thread: Thread) => void;
  onShowNew: () => void;
}) {
  const empty = !sections.some((section) => section.threads.length);
  return (
    <section className="list" aria-label={view === "history" ? "Historique" : "À trier"}>
      <div className="list-tools">
        {tools}
        <ProgressLine active={scanRunning || searchBusy} label={scanRunning ? "Relevé en cours" : "Recherche"} />
      </div>
      {newCount > 0 ? (
        <div className="new-pill-wrap">
          <Button variant="primary" icon="arrow-left" className="new-pill" onClick={onShowNew}>
            {newCount} nouveau{newCount > 1 ? "x" : ""}
          </Button>
        </div>
      ) : null}
      <div className="list-scroll" ref={scrollRef} role="listbox" aria-label={searching ? "Résultats" : "Conversations"}>
        {searching ? (
          hits === null ? (
            <RowSkeleton rows={4} />
          ) : hits.length === 0 ? (
            <EmptyState kind="search" q={q} />
          ) : (
            <div className="list-section">
              <div className="list-head">Tous les mails</div>
              {hits.map((hit, index) => (
                <HitRow key={hit.id} hit={hit} q={q} labels={labels} selected={selectedHitId === hit.id} index={index} onSelect={onSelectHit} />
              ))}
            </div>
          )
        ) : loading ? (
          <RowSkeleton rows={7} />
        ) : empty ? (
          <EmptyState kind={view === "history" ? "history" : cat ? "category" : "queue"} treated={treatedToday} />
        ) : (
          sections.map((section) => (
            <div key={section.id} className="list-section">
              <div className="list-head">{section.label}</div>
              {section.threads.map((thread) => (
                <ThreadRow
                  key={thread.key}
                  thread={thread}
                  labels={labels}
                  selected={selectedKey === thread.key}
                  leaving={thread.ids.some((id) => leaving.has(id))}
                  checked={checked.has(thread.key)}
                  selectionMode={checked.size > 0}
                  canMove={canMove}
                  history={view === "history"}
                  onSelect={onSelect}
                  onToggleCheck={onToggleCheck}
                  onQuick={onQuick}
                />
              ))}
            </div>
          ))
        )}
      </div>
    </section>
  );
}
