import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { Button } from "../../components/Button";
import { Card, Empty, Page, Pill, fmtDate } from "../../components/Page";
import { useToasts } from "../../components/Toast";

type Status = {
  wordpress: { configured: boolean; state: { status: string; last_ok_at: string | null; last_error: string } };
  edge: { configured: boolean; provider: string; public_url: string; state: { status: string; last_ok_at: string | null; last_error: string }; last_at: string | null };
  snippets: Record<string, string>;
  publications: { id: number; entity_kind: string; entity_id: string; target: string; status: string; url: string; error: string; created_at: string }[];
};
const BLOCKS: Record<string, string> = { agenda: "Agenda (Outlive, événements couverts ou Radio Campus)", upcoming: "Prochainement à l’antenne", podcasts: "Derniers podcasts", playlist: "Playlist de la semaine", team: "L’équipe" };

export function PublishPage() {
  const toasts = useToasts();
  const [status, setStatus] = useState<Status | null>(null);
  const [preview, setPreview] = useState("podcasts");
  const load = useCallback(() => {
    api.get<Status>("/api/v1/publish/status").then(setStatus).catch(() => undefined);
  }, []);
  useEffect(() => {
    load();
    const onEvent = (event: Event) => {
      const type = (event as CustomEvent<{ type: string }>).detail?.type || "";
      if (type === "publish" || type === "publications" || type === "job") load();
    };
    window.addEventListener("regie-event", onEvent);
    return () => window.removeEventListener("regie-event", onEvent);
  }, [load]);
  if (!status) return <Page title="Publier"><div className="skeleton-lines" /></Page>;

  return (
    <Page
      title="Publier"
      subtitle="Nasgul ne s’expose jamais : Régie pousse RSS, JSON et blocs iframe vers le bord public, et crée les articles WordPress."
      wide
      actions={
        <>
          <Button icon="refresh" onClick={() => void api.post("/api/v1/publish/run").then(() => toasts.push({ text: "Publications en attente relancées", tone: "ok" }))}>Relancer les publications</Button>
          <Button variant="primary" icon="upload" disabled={!status.edge.configured} onClick={() => void api.post("/api/v1/publish/regenerate").then(() => toasts.push({ text: "Régénération complète du bord public lancée", tone: "ok" })).catch((err) => toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" }))}>Régénérer le bord public</Button>
        </>
      }
    >
      <div className="grid cols-2">
        <Card title="WordPress" icon="globe">
          <p className="chips"><Pill tone={status.wordpress.configured ? (status.wordpress.state.status === "error" ? "warn" : "ok") : "muted"}>{status.wordpress.configured ? status.wordpress.state.status || "prêt" : "non configuré"}</Pill>{status.wordpress.state.last_ok_at ? <span className="small muted">dernier envoi {fmtDate(status.wordpress.state.last_ok_at)}</span> : null}</p>
          {status.wordpress.state.last_error ? <p className="small is-overdue-text">{status.wordpress.state.last_error}</p> : null}
          <p className="small muted">Les podcasts publiés créent un article en brouillon (lecteur audio + lien RSS). Configuration : WORDPRESS_USER et WORDPRESS_APP_PASSWORD dans secrets.env.</p>
        </Card>
        <Card title="Bord public" icon="layers">
          <p className="chips"><Pill tone={status.edge.configured ? (status.edge.state.status === "error" ? "warn" : "ok") : "muted"}>{status.edge.configured ? `${status.edge.provider} · ${status.edge.state.status || "prêt"}` : "non configuré"}</Pill>{status.edge.last_at ? <span className="small muted">régénéré {fmtDate(status.edge.last_at)}</span> : null}</p>
          {status.edge.public_url ? <p className="small"><a href={status.edge.public_url} target="_blank" rel="noreferrer">{status.edge.public_url}</a></p> : null}
          {status.edge.state.last_error ? <p className="small is-overdue-text">{status.edge.state.last_error}</p> : null}
          <p className="small muted">EDGE_PROVIDER = vercel | cloudflare | dir. Une régénération complète répare toute perte du bord.</p>
        </Card>
      </div>
      <Card title="Blocs iframe" icon="layers" actions={<div className="tabs">{Object.keys(BLOCKS).map((b) => <button key={b} type="button" className={`tab${preview === b ? " is-on" : ""}`} onClick={() => setPreview(b)}>{b}</button>)}</div>}>
        <p className="small muted">{BLOCKS[preview]}. Même contrat pour tous : <code>?embed=1</code>, hauteur envoyée par <code>postMessage</code> (<code>regie:height</code>), liens <code>target=_top</code>, CSP stricte.</p>
        <iframe title={`aperçu ${preview}`} src={`/api/v1/publish/preview/${preview}?embed=1`} style={{ width: "100%", height: 360, border: "1px solid var(--hair)", borderRadius: 12 }} />
        <p className="small muted">Extrait à coller dans WordPress :</p>
        <pre className="pre" onClick={() => void navigator.clipboard.writeText(status.snippets[preview] || "").then(() => toasts.push({ text: "Extrait copié", tone: "ok" }))} title="Cliquer pour copier">{status.snippets[preview]}</pre>
      </Card>
      <Card title="Dernières publications" icon="send">
        {status.publications.length ? (
          <table className="table">
            <thead><tr><th>Quand</th><th>Objet</th><th>Cible</th><th>État</th><th>Lien</th></tr></thead>
            <tbody>
              {status.publications.map((p) => (
                <tr key={p.id}>
                  <td>{fmtDate(p.created_at)}</td>
                  <td><Link to={`/shows/podcasts/${p.entity_id}`}>{p.entity_kind} #{p.entity_id}</Link></td>
                  <td>{p.target}</td>
                  <td><Pill tone={p.status === "done" ? "ok" : p.status === "failed" ? "warn" : "muted"}>{p.status}</Pill>{p.error ? <span className="small is-overdue-text"> {p.error}</span> : null}</td>
                  <td>{p.url ? <a href={p.url} target="_blank" rel="noreferrer">ouvrir</a> : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty text="Rien de publié pour l’instant." icon="send" />
        )}
      </Card>
    </Page>
  );
}
