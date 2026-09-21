import { useCallback, useEffect, useState } from "react";
import { api, listJobs, recentActions, systemStatus, type ActionRow, type JobRow } from "../api/client";
import { Button } from "../components/Button";
import { Card, Empty, Page, Pill, Stat, fmtDate } from "../components/Page";
import { useToasts } from "../components/Toast";

type Status = {
  jobs: { lanes: Record<string, Record<string, number>>; running: JobRow[]; late: number; dead: number; worker: string };
  connectors: { system: string; label: string; configured: boolean; status: string; failures: number; last_ok_at: string | null; last_error: string; read_only: boolean }[];
  schedules: { id: number; kind: string; every_seconds: number; next_run_at: string; last_run_at: string | null; enabled: boolean }[];
  search_documents: number;
  media_available: boolean;
  sse_subscribers: number;
  modules: string[];
  metrics: { uptime_s: number; timings: Record<string, { count: number; p50: number; p95: number; max: number }> };
  last_backup_at: string | null;
  backup_age_hours: number | null;
};

export function SystemPage() {
  const toasts = useToasts();
  const [status, setStatus] = useState<Status | null>(null);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [actions, setActions] = useState<ActionRow[]>([]);
  const load = useCallback(() => {
    systemStatus().then((res) => setStatus(res as unknown as Status)).catch(() => undefined);
    listJobs(40).then((res) => setJobs(res.jobs)).catch(() => undefined);
    recentActions({ limit: 30 }).then((res) => setActions(res.actions)).catch(() => undefined);
  }, []);
  useEffect(() => {
    load();
    const timer = window.setInterval(load, 15_000);
    return () => window.clearInterval(timer);
  }, [load]);
  if (!status) return <Page title="État du système"><div className="skeleton-lines" /></Page>;
  const uptime = status.metrics.uptime_s;
  const slow = Object.entries(status.metrics.timings).filter(([key]) => key.startsWith("http_ms")).sort((a, b) => b[1].p95 - a[1].p95).slice(0, 8);

  return (
    <Page title="État du système" subtitle={`Régie tourne depuis ${Math.floor(uptime / 3600)} h ${Math.floor((uptime % 3600) / 60)} min · ${status.modules.length} modules · ${status.sse_subscribers} écran(s) connecté(s)`} wide actions={<Button icon="refresh" onClick={load}>Actualiser</Button>}>
      <div className="grid cols-4">
        <Stat label="jobs en retard" value={status.jobs.late} tone={status.jobs.late ? "warn" : "ok"} />
        <Stat label="jobs morts" value={status.jobs.dead} tone={status.jobs.dead ? "warn" : "ok"} />
        <Stat label="documents indexés" value={status.search_documents} />
        <Stat label="dernière sauvegarde" value={status.backup_age_hours === null ? "jamais" : `${Math.round(status.backup_age_hours)} h`} tone={status.backup_age_hours === null || status.backup_age_hours > 48 ? "warn" : "ok"} />
      </div>
      <div className="grid cols-2">
        <Card title="Connecteurs" icon="layers">
          <ul className="rows">
            {status.connectors.map((c) => (
              <li key={c.system}>
                <Pill tone={!c.configured ? "muted" : c.status === "ok" ? "ok" : c.status === "error" || c.status === "paused" ? "warn" : "muted"}>{c.status}</Pill>
                <span>{c.label}</span>
                {c.read_only ? <span className="small muted">lecture seule</span> : null}
                <span className="spacer" />
                {c.last_ok_at ? <span className="small muted">OK {fmtDate(c.last_ok_at)}</span> : <span className="small muted">{c.configured ? "jamais" : "non configuré"}</span>}
                {c.last_error ? <span className="small is-overdue-text">{c.last_error.slice(0, 120)}</span> : null}
                {c.status === "paused" || c.status === "error" ? <Button variant="quiet" icon="refresh" onClick={() => void api.post(`/api/v1/connectors/${c.system}/reset`).then(() => { toasts.push({ text: `${c.label} réactivé`, tone: "ok" }); load(); })}>Réactiver</Button> : null}
              </li>
            ))}
          </ul>
          <p className="small muted">Coupe-circuit : 5 échecs d’affilée mettent un connecteur en pause dix minutes. Chacun a un faux jumeau pour les tests.</p>
        </Card>
        <Card title="Planification" icon="clock">
          <ul className="rows">
            {status.schedules.map((s) => (
              <li key={s.id}><Pill tone={s.enabled ? "ok" : "muted"}>{s.enabled ? "actif" : "off"}</Pill><span>{s.kind}</span><span className="small muted">toutes les {s.every_seconds >= 3600 ? `${Math.round(s.every_seconds / 3600)} h` : `${Math.round(s.every_seconds / 60)} min`}</span><span className="spacer" /><span className="small muted">prochain {fmtDate(s.next_run_at)}</span></li>
            ))}
          </ul>
          <p className="small muted">Médiathèque : {status.media_available ? "montée" : "absente"}. Travailleur : {status.jobs.worker}.</p>
        </Card>
        <Card title="Jobs récents" icon="activity">
          {jobs.length ? (
            <table className="table">
              <thead><tr><th>#</th><th>Job</th><th>Voie</th><th>État</th><th>Quand</th><th></th></tr></thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id}>
                    <td className="muted">{j.id}</td>
                    <td>{j.kind}{j.progress ? <span className="small muted"> · {j.progress}</span> : null}{j.error ? <span className="small is-overdue-text"> · {j.error}</span> : null}</td>
                    <td className="muted">{j.lane}</td>
                    <td><Pill tone={j.status === "done" ? "ok" : j.status === "dead" || j.status === "failed" ? "warn" : j.status === "running" ? "live" : "muted"}>{j.status}</Pill></td>
                    <td className="small muted">{fmtDate(j.finished_at || j.created_at)}</td>
                    <td>{j.status === "dead" ? <Button variant="quiet" onClick={() => void api.post(`/api/v1/jobs/${j.id}/retry`).then(() => { toasts.push({ text: "Job relancé", tone: "ok" }); load(); })}>Relancer</Button> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty text="Aucun job." />
          )}
        </Card>
        <Card title="Journal des actions (tous modules)" icon="history">
          {actions.length ? <ul className="rows">{actions.map((a) => <li key={a.id}><span className="when">{fmtDate(a.created_at)}</span><Pill tone="muted">{a.module}</Pill><span>{a.label}</span><span className="spacer" /><Pill tone={a.status === "failed" ? "warn" : a.status === "undone" ? "muted" : "ok"}>{a.status}</Pill>{a.reversible ? <Button variant="quiet" icon="undo" onClick={() => void api.post(`/api/v1/actions/${a.id}/undo`).then(() => { toasts.push({ text: "Annulé", tone: "ok" }); load(); }).catch((err) => toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" }))}>Annuler</Button> : null}</li>)}</ul> : <Empty text="Aucune action." />}
        </Card>
      </div>
      <Card title="Temps de réponse (p95, ms)" icon="activity">
        {slow.length ? <ul className="rows">{slow.map(([key, stats]) => <li key={key}><span style={{ fontFamily: "monospace", fontSize: 12 }}>{key.replace(/^http_ms\{route="|"\}$/g, "")}</span><span className="spacer" /><span className={`small ${stats.p95 > 150 ? "is-overdue-text" : "muted"}`}>p50 {stats.p50} · p95 {stats.p95} · max {stats.max} · n={stats.count}</span></li>)}</ul> : <Empty text="Pas encore de mesures." />}
        <p className="small muted">Objectif : file des mails et recherche sous 150 ms. <a href="/api/v1/metrics" target="_blank" rel="noreferrer">Métriques brutes</a>.</p>
      </Card>
    </Page>
  );
}
