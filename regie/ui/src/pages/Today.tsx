import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Button } from "../components/Button";
import { Card, Empty, Page, Pill, Stat, fmtDate } from "../components/Page";
import { useRegistry } from "../lib/registry";
import { useToasts } from "../components/Toast";

type Today = {
  user: { id: string; name: string };
  tasks: { mine: TaskRow[]; overdue: TaskRow[]; due_today: TaskRow[]; doing: TaskRow[] };
  appointments: { id: number; title: string; starts_at: string; ends_at: string; all_day: boolean; account_label?: string; account_color?: string; location?: string }[];
  covered_events: { id: string; title: string; starts_at: string; venue_name: string; coverage: { kind: string; status: string }[]; is_radio_campus: boolean }[];
  mail: { proposed: number; unread: number };
  notifications: { id: number; text: string; url: string; created_at: string }[];
  changes: { topic: string; payload: Record<string, unknown>; created_at: string }[];
  jobs: { late: number; dead: number };
  connectors: { system: string; label: string; status: string; last_error: string }[];
  backup_age_hours: number | null;
};
type TaskRow = { id: number; title: string; status: string; due_at: string | null; overdue: boolean; checklist_done: number; checklist_total: number };

const TOPIC_LABELS: Record<string, string> = {
  "mail.received": "Mail reçu",
  "mail.archive": "Mails archivés",
  "mail.later": "Mails mis de côté",
  "task.created": "Tâche créée",
  "task.updated": "Tâche modifiée",
  "coverage.planned": "Couverture planifiée",
  "calendar_event.created": "Rendez-vous ajouté",
  "calendar_event.updated": "Rendez-vous modifié",
  "podcast.published": "Podcast publié",
  "podcast.exported": "Podcast exporté",
  "episode.detected": "Nouvel épisode détecté",
  "episode.transcribed": "Transcription terminée",
  "person.created": "Fiche créée",
  "events.refreshed": "Agenda Outlive rafraîchi",
  "guest.status": "Invité : étape",
};

export function TodayPage() {
  const reg = useRegistry();
  const toasts = useToasts();
  const [data, setData] = useState<Today | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .get<Today>("/api/v1/planning/today")
        .then((res) => alive && setData(res))
        .catch((err) => alive && setError(err instanceof Error ? err.message : "erreur"));
    void load();
    const onEvent = () => void load();
    window.addEventListener("regie-event", onEvent);
    const timer = window.setInterval(load, 60_000);
    return () => {
      alive = false;
      window.removeEventListener("regie-event", onEvent);
      window.clearInterval(timer);
    };
  }, []);

  async function done(task: TaskRow) {
    try {
      await api.post(`/api/v1/planning/tasks/${task.id}/done`);
      toasts.push({ text: `« ${task.title} » terminée`, tone: "ok" });
      setData((prev) => (prev ? { ...prev, tasks: { ...prev.tasks, mine: prev.tasks.mine.filter((t) => t.id !== task.id) } } : prev));
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "live" });
    }
  }

  if (error) return <Page title="Aujourd’hui"><Empty text={error} icon="warning" /></Page>;
  if (!data) return <Page title="Aujourd’hui"><div className="skeleton-lines" aria-busy="true" /></Page>;

  const badConnectors = data.connectors.filter((c) => c.status === "error" || c.status === "paused");
  const backupWarn = data.backup_age_hours !== null && data.backup_age_hours > 48;

  return (
    <Page title={`Bonjour ${data.user.name || ""}`.trim()} subtitle={new Date().toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" })}>
      <div className="grid cols-4">
        <Link to="/mails" className="stat-link"><Stat label="mails à trier" value={data.mail.proposed} tone={data.mail.proposed ? undefined : "muted"} /></Link>
        <Stat label="tâches en retard" value={data.tasks.overdue.length} tone={data.tasks.overdue.length ? "warn" : "ok"} />
        <Stat label="rendez-vous aujourd’hui" value={data.appointments.length} />
        <Stat label="jobs en retard / morts" value={`${data.jobs.late} / ${data.jobs.dead}`} tone={data.jobs.late || data.jobs.dead ? "warn" : "muted"} />
      </div>
      {badConnectors.length || backupWarn ? (
        <Card icon="warning" title="À regarder">
          <ul className="rows">
            {badConnectors.map((c) => (
              <li key={c.system}><Pill tone="warn">{c.label}</Pill> <span className="small muted">{c.last_error || c.status}</span></li>
            ))}
            {backupWarn ? <li><Pill tone="warn">Sauvegarde</Pill> <span className="small muted">dernière sauvegarde il y a {Math.round(data.backup_age_hours || 0)} h</span></li> : null}
          </ul>
        </Card>
      ) : null}
      <div className="grid cols-2">
        <Card icon="check" title="Mes tâches" actions={<Link to="/planning" className="top-link">Tout le planning</Link>}>
          {data.tasks.mine.length ? (
            <ul className="rows">
              {data.tasks.mine.slice(0, 10).map((task) => (
                <li key={task.id}>
                  <Button variant="icon" icon="check" onClick={() => void done(task)} aria-label="Terminer" tip="Terminer" />
                  <Link to={`/planning?task=${task.id}`} className={task.overdue ? "is-overdue-text" : ""}>{task.title}</Link>
                  <span className="spacer" />
                  {task.checklist_total ? <span className="small muted">{task.checklist_done}/{task.checklist_total}</span> : null}
                  {task.due_at ? <span className={`small ${task.overdue ? "is-overdue-text" : "muted"}`}>{fmtDate(task.due_at, false)}</span> : null}
                </li>
              ))}
            </ul>
          ) : (
            <Empty text="Rien d’assigné. Profite." icon="check-circle" />
          )}
        </Card>
        <Card icon="clock" title="Aujourd’hui à l’agenda" actions={<Link to="/planning/week" className="top-link">Semaine</Link>}>
          {data.appointments.length ? (
            <ul className="rows">
              {data.appointments.map((rdv) => (
                <li key={rdv.id}>
                  <span className="when">{rdv.all_day ? "journée" : fmtDate(rdv.starts_at).split(" ").pop()}</span>
                  <span>{rdv.title}</span>
                  {rdv.account_label ? <Pill tone="muted">{rdv.account_label}</Pill> : null}
                </li>
              ))}
            </ul>
          ) : (
            <Empty text="Aucun rendez-vous." icon="clock" />
          )}
        </Card>
        <Card icon="calendar" title="Cette semaine à couvrir" actions={<Link to="/events" className="top-link">Événements</Link>}>
          {data.covered_events.length ? (
            <ul className="rows">
              {data.covered_events.map((event) => (
                <li key={event.id}>
                  <span className="when">{fmtDate(event.starts_at)}</span>
                  <Link to={`/events/${event.id}`}>{event.title}</Link>
                  <span className="spacer" />
                  {event.is_radio_campus ? <Pill tone="live">Radio Campus</Pill> : null}
                  {event.coverage.map((c) => <Pill key={c.kind}>{c.kind}</Pill>)}
                </li>
              ))}
            </ul>
          ) : (
            <Empty text="Rien de couvert cette semaine." icon="calendar" />
          )}
        </Card>
        <Card icon="activity" title="Ce qui a changé (24 h)">
          {data.changes.length ? (
            <ul className="rows">
              {data.changes.slice(0, 14).map((change, index) => (
                <li key={`${change.topic}-${index}`}>
                  <span className="when">{fmtDate(change.created_at).split(" ").pop()}</span>
                  <span>{TOPIC_LABELS[change.topic] || change.topic}</span>
                  <span className="small muted">{summarize(change.payload)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty text="Calme plat." />
          )}
        </Card>
      </div>
      {reg.can("mail") && data.mail.unread ? <p className="muted small">{data.mail.unread} mail{data.mail.unread > 1 ? "s" : ""} non lu{data.mail.unread > 1 ? "s" : ""} dans la file.</p> : null}
    </Page>
  );
}

function summarize(payload: Record<string, unknown>): string {
  const keys = ["subject", "title", "sender", "created", "upserted", "status"];
  for (const key of keys) {
    if (payload[key] !== undefined && payload[key] !== null && payload[key] !== "") return String(payload[key]).slice(0, 60);
  }
  if (Array.isArray(payload.ids)) return `${payload.ids.length} élément(s)`;
  return "";
}
