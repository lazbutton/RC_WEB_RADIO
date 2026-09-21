import { useEffect, useState } from "react";
import type { ActionRow, Job } from "../../api";
import { Button } from "../../components/Button";
import { Chip } from "../../components/Chip";
import { Icon } from "../../components/Icon";
import { Skeleton } from "../../components/Skeleton";
import { formatSize } from "../../lib/format";
import { recapWithLinks } from "../../lib/links";
import { notionPieces, type Piece, type Thread } from "../../lib/threads";

export function RecapCard({
  thread,
  job,
  briefing,
  onBrief,
  history,
}: {
  thread: Thread;
  job: Job | null;
  briefing: boolean;
  onBrief: () => void;
  history: boolean;
}) {
  const running = Boolean(job) || briefing;
  const recap = thread.recap;
  return (
    <section className="card is-recap" aria-busy={running} aria-live="polite">
      <header className="card-head">
        <h2 className="card-label">
          <Icon name="sparkle" size={12} /> Récap
        </h2>
        {!history ? (
          <Button variant="quiet" icon="refresh" busy={running} onClick={onBrief} className="card-action">
            {running ? "Récap…" : "Régénérer"}
          </Button>
        ) : null}
      </header>
      {running && !recap ? (
        <Skeleton lines={3} />
      ) : (
        <p className={`card-text${running ? " is-dim" : ""}`}>{recapWithLinks(recap || "Pas encore de récap.")}</p>
      )}
      {job?.progress ? <p className="card-hint">{job.progress}</p> : null}
      {thread.latest.error ? <p className="card-hint is-live">{thread.latest.error}</p> : null}
    </section>
  );
}

export function DraftCard({ draft, copied, onCopy }: { draft: string; copied: boolean; onCopy: () => void }) {
  return (
    <section className="card is-draft">
      <header className="card-head">
        <h2 className="card-label">
          <Icon name="copy" size={12} /> Exemple de réponse
        </h2>
        <Button variant="quiet" icon={copied ? "check" : "copy"} className={`card-action${copied ? " is-done" : ""}`} shortcut="c" onClick={onCopy}>
          {copied ? "Copié" : "Copier"}
        </Button>
      </header>
      <p className="card-text draft-body">{draft}</p>
      <p className="card-hint">À coller dans Roundcube. Rien n’est envoyé d’ici.</p>
    </section>
  );
}

export function PiecesCard({
  pieces,
  busyKey,
  audio,
  onOpen,
}: {
  pieces: Piece[];
  busyKey: string | null;
  audio: { key: string; url: string } | null;
  onOpen: (piece: Piece) => void;
}) {
  if (!pieces.length) return null;
  return (
    <section className="card is-pieces">
      <header className="card-head">
        <h2 className="card-label">
          <Icon name="paperclip" size={12} /> Pièces jointes · {pieces.length}
        </h2>
      </header>
      <ul className="piece-list">
        {pieces.map((piece) => {
          const key = `${piece.itemId}-${piece.n}`;
          const busy = busyKey === key;
          const action = piece.kind === "audio" ? "Écouter" : piece.kind === "other" ? "" : "Ouvrir";
          return (
            <li key={key} className="piece-row">
              <div className="piece-copy">
                <span className="piece-name">{piece.filename}</span>
                <p className="piece-meta">
                  <span className={`kind-pill is-${piece.kind}`}>{piece.kind}</span>
                  {formatSize(piece.size) ? ` · ${formatSize(piece.size)}` : ""}
                  {piece.status === "refus" && piece.error ? ` · ${piece.error}` : ""}
                  {piece.note ? ` · ${piece.note}` : ""}
                </p>
                {piece.status === "ok" && piece.text ? <p className="piece-excerpt">{piece.text}</p> : null}
                {piece.kind === "audio" && audio?.key === key ? <audio className="piece-audio" controls autoPlay src={audio.url} /> : null}
              </div>
              {action ? (
                <Button variant="quiet" icon={piece.kind === "audio" ? "play" : "external"} busy={busy} onClick={() => onOpen(piece)}>
                  {action}
                </Button>
              ) : (
                <span className="muted small">ignorée</span>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function NotionCard({
  thread,
  ready,
  job,
  note,
  onNote,
  onAdd,
}: {
  thread: Thread;
  ready: boolean;
  job: Job | null;
  note: string;
  onNote: (value: string) => void;
  onAdd: () => void;
}) {
  const url = thread.notionUrl;
  const pj = notionPieces(thread.pieces).length;
  const [justDone, setJustDone] = useState(false);
  useEffect(() => {
    if (url) {
      setJustDone(true);
      const timer = window.setTimeout(() => setJustDone(false), 1200);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [url]);
  return (
    <section className={`card is-notion${url ? " is-done" : ""}`} aria-live="polite">
      <header className="card-head">
        <h2 className="card-label">
          <Icon name="notion" size={12} /> Notion
        </h2>
        {url ? (
          <a className={`btn btn-quiet card-action${justDone ? " is-pop" : ""}`} href={url} target="_blank" rel="noreferrer">
            <Icon name="external" />
            <span className="btn-label">Ouvrir</span>
          </a>
        ) : null}
      </header>
      {url ? (
        <p className="card-text">
          Tâche créée · Programmé · Radio Campus{pj ? ` · ${pj} PJ` : ""}
        </p>
      ) : (
        <>
          <textarea
            className="field notion-note"
            value={note}
            onChange={(ev) => onNote(ev.target.value)}
            rows={3}
            placeholder="Note pour l’IA (facultatif) — deadline, contexte, ce que tu veux en sortir…"
          />
          <p className="card-hint">
            {pj > 1 ? `${pj} pièces jointes seront ajoutées à la tâche.` : pj === 1 ? "1 pièce jointe sera ajoutée à la tâche." : thread.pieces.length ? "Les pièces ignorées (signatures, etc.) ne partent pas dans Notion." : "Tâche dans Tâches, État Programmé, projet Radio Campus."}
          </p>
          <div className="card-actions">
            <Button variant="primary" icon="notion" shortcut="n" busy={Boolean(job)} onClick={onAdd} disabled={!ready}>
              {job ? job.progress || "Ajout…" : pj ? `Ajouter à Notion · ${pj} PJ` : "Ajouter à Notion"}
            </Button>
            {!ready ? <span className="card-hint">Jeton dans Réglages</span> : null}
          </div>
        </>
      )}
    </section>
  );
}

export function ThreadActions({ actions, ids, onUndo }: { actions: ActionRow[]; ids: number[]; onUndo: (id: number) => void }) {
  const rows = actions.filter((row) => row.item_ids.some((id) => ids.includes(id))).slice(0, 6);
  if (!rows.length) return null;
  return (
    <section className="card is-history">
      <header className="card-head">
        <h2 className="card-label">
          <Icon name="history" size={12} /> Historique du fil
        </h2>
      </header>
      <ul className="history-list">
        {rows.map((row) => (
          <li key={row.id} className={`history-row is-${row.status}`}>
            <span className="history-label">{row.label}</span>
            <span className="history-when">{row.created_rel.replace(/^.*\(|\)$/g, "")}</span>
            {row.status === "failed" ? <Chip tone="live">échec</Chip> : null}
            {row.status === "undone" ? <Chip tone="quiet">annulé</Chip> : null}
            {row.reversible ? (
              <Button variant="quiet" icon="undo" onClick={() => onUndo(row.id)}>
                Annuler
              </Button>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
