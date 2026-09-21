import { Icon } from "../../components/Icon";

export function EmptyState({ kind, treated, q }: { kind: "queue" | "history" | "search" | "category"; treated?: number; q?: string }) {
  if (kind === "search") {
    return (
      <div className="empty">
        <p className="empty-title">Aucun mail pour « {q} ».</p>
        <p className="muted">Essaie un autre mot, un expéditeur, ou le nom d’une pièce jointe.</p>
      </div>
    );
  }
  if (kind === "history") {
    return (
      <div className="empty">
        <p className="empty-title">Rien de traité pour l’instant.</p>
        <p className="muted">Les mails archivés ou mis de côté apparaîtront ici, avec un bouton Annuler.</p>
      </div>
    );
  }
  if (kind === "category") {
    return (
      <div className="empty">
        <p className="empty-title">Rien dans cette catégorie.</p>
        <p className="muted">Tout est trié ici.</p>
      </div>
    );
  }
  return (
    <div className="empty is-zero">
      <span className="empty-check" aria-hidden>
        <Icon name="check" size={22} />
      </span>
      <p className="empty-title">Boîte à jour.</p>
      <p className="muted">{treated ? `${treated} mail${treated > 1 ? "s" : ""} traité${treated > 1 ? "s" : ""} aujourd’hui.` : "Rien à trier pour le moment."}</p>
    </div>
  );
}
