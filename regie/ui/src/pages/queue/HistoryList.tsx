import type { ActionRow } from "../../api";
import { Button } from "../../components/Button";
import { Chip } from "../../components/Chip";
import { Icon } from "../../components/Icon";
import { dayHeading } from "../../lib/threads";

export function HistoryList({ actions, onUndo }: { actions: ActionRow[]; onUndo: (id: number) => void }) {
  const groups = new Map<string, ActionRow[]>();
  for (const row of actions) {
    const label = dayHeading(row.created_at);
    const list = groups.get(label) || [];
    list.push(row);
    groups.set(label, list);
  }
  return (
    <section className="card is-history is-tall">
      <header className="card-head">
        <h2 className="card-label">
          <Icon name="history" size={12} /> Actions récentes
        </h2>
      </header>
      {!actions.length ? <p className="card-hint">Aucune action pour l’instant.</p> : null}
      {Array.from(groups.entries()).map(([label, rows]) => (
        <div key={label} className="history-group">
          <p className="history-day">{label}</p>
          <ul className="history-list">
            {rows.map((row) => (
              <li key={row.id} className={`history-row is-${row.status}`}>
                <span className="history-label">
                  {row.label}
                  {row.after?.folders ? <span className="history-folder"> → {(row.after.folders as string[]).map((f) => f.split("/").pop()).join(", ")}</span> : null}
                </span>
                <span className="history-when">{row.created_rel.replace(/^.*\(|\)$/g, "")}</span>
                {row.status === "pending" ? <Chip tone="text">en cours</Chip> : null}
                {row.status === "failed" ? (
                  <Chip tone="live" title={row.error}>
                    échec
                  </Chip>
                ) : null}
                {row.status === "undone" ? <Chip tone="quiet">annulé</Chip> : null}
                {row.reversible ? (
                  <Button variant="quiet" icon="undo" onClick={() => onUndo(row.id)}>
                    Annuler
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
