import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, qs, type ActionRow, type LinkRow } from "../../api/client";
import { Button } from "../../components/Button";
import { Checkbox } from "../../components/Checkbox";
import { Card, Empty, Field, Page, Pill, fmtDate } from "../../components/Page";
import { Sheet } from "../../components/Sheet";
import { useToasts } from "../../components/Toast";
import { EntityChip, useRegistry } from "../../lib/registry";
import { CommentsPanel } from "../planning/CommentsPanel";
import { TaskQuickAdd } from "../planning/TaskQuickAdd";

type Coverage = { id: number; event_id: string; kind: string; status: string; assignee_id: string | null; notes: string };
type EventRow = { id: string; title: string; description?: string; starts_at: string; ends_at: string | null; venue_name: string; organizer_name: string; price: string; url: string; image_url: string; category: string; is_radio_campus: boolean; coverage: Coverage[]; warnings?: string[] };
const STATUS_LABEL: Record<string, string> = { idea: "idée", planned: "prévu", done: "fait", skipped: "passé" };

export function EventsPage() {
  const toasts = useToasts();
  const [rows, setRows] = useState<EventRow[]>([]);
  const [kinds, setKinds] = useState<string[]>([]);
  const [connector, setConnector] = useState<{ status: string; last_ok_at: string | null; last_error: string } | null>(null);
  const [q, setQ] = useState("");
  const [radio, setRadio] = useState(false);
  const [covered, setCovered] = useState(false);
  const [covering, setCovering] = useState<EventRow | null>(null);

  const load = useCallback(() => {
    const start = new Date();
    start.setDate(start.getDate() - 1);
    const end = new Date();
    end.setDate(end.getDate() + 90);
    api.get<{ events: EventRow[]; connector: typeof connector; coverage_kinds: string[] }>(`/api/v1/events${qs({ start: start.toISOString(), end: end.toISOString(), q, radio: radio ? 1 : 0 })}`).then((res) => {
      setRows(res.events);
      setConnector(res.connector);
      setKinds(res.coverage_kinds);
    }).catch(() => undefined);
  }, [q, radio]);

  useEffect(() => {
    const timer = window.setTimeout(load, q ? 150 : 0);
    const onEvent = (event: Event) => {
      const type = (event as CustomEvent<{ type: string }>).detail?.type || "";
      if (type === "events" || type.startsWith("coverage")) load();
    };
    window.addEventListener("regie-event", onEvent);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("regie-event", onEvent);
    };
  }, [load, q]);

  const visible = covered ? rows.filter((r) => r.coverage.length) : rows;
  const byDay = new Map<string, EventRow[]>();
  for (const row of visible) {
    const key = new Date(row.starts_at).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
    byDay.set(key, [...(byDay.get(key) || []), row]);
  }

  return (
    <Page
      title="Événements"
      subtitle={connector ? <span className="chips"><Pill tone={connector.status === "ok" ? "ok" : connector.status === "idle" ? "muted" : "warn"}>Outlive · {connector.status}</Pill>{connector.last_ok_at ? <span className="small muted">rafraîchi {fmtDate(connector.last_ok_at)}</span> : null}{connector.last_error ? <span className="small muted">{connector.last_error}</span> : null}</span> : undefined}
      wide
      actions={<Button icon="refresh" onClick={() => void api.post("/api/v1/events/refresh").then(() => toasts.push({ text: "Rafraîchissement Outlive lancé", tone: "ok" })).catch((err) => toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" }))}>Rafraîchir Outlive</Button>}
    >
      <div className="toolbar">
        <input className="field" placeholder="Titre, lieu, organisateur…" value={q} onChange={(ev) => setQ(ev.target.value)} style={{ maxWidth: 320 }} />
        <Checkbox checked={radio} onToggle={() => setRadio((v) => !v)} label="Radio Campus organise" />
        <Checkbox checked={covered} onToggle={() => setCovered((v) => !v)} label="Couverts seulement" />
        <span className="spacer" />
        <Link to="/events/coverage" className="btn btn-ghost">Tableau des couvertures</Link>
      </div>
      {visible.length ? (
        Array.from(byDay.entries()).map(([day, events]) => (
          <Card key={day} title={day}>
            <ul className="rows">
              {events.map((event) => (
                <li key={event.id}>
                  <span className="when">{new Date(event.starts_at).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}</span>
                  <Link to={`/events/${event.id}`} style={{ fontWeight: 600 }}>{event.title}</Link>
                  <span className="muted small">{[event.venue_name, event.organizer_name, event.price].filter(Boolean).join(" · ")}</span>
                  <span className="spacer" />
                  {event.is_radio_campus ? <Pill tone="live">Radio Campus</Pill> : null}
                  {event.coverage.map((c) => <Pill key={c.id} tone={c.status === "done" ? "ok" : c.status === "planned" ? "live" : "muted"}>{c.kind} · {STATUS_LABEL[c.status] || c.status}</Pill>)}
                  <Button variant="quiet" icon="mic" onClick={() => setCovering(event)}>Couvrir</Button>
                </li>
              ))}
            </ul>
          </Card>
        ))
      ) : (
        <Empty text={connector?.status === "idle" ? "Outlive n’a pas encore été lu : configure la clé (Réglages) ou lance un rafraîchissement." : "Aucun événement dans la fenêtre."} icon="calendar" />
      )}
      <Sheet open={Boolean(covering)} title={covering ? `Couvrir : ${covering.title}` : ""} onClose={() => setCovering(null)}>
        {covering ? <CoverForm event={covering} kinds={kinds} onDone={() => { setCovering(null); load(); }} /> : null}
      </Sheet>
    </Page>
  );
}

export function CoverForm({ event, kinds, mailId, onDone }: { event: { id: string; title: string }; kinds: string[]; mailId?: number; onDone: () => void }) {
  const toasts = useToasts();
  const [kind, setKind] = useState(kinds[0] || "annonce");
  const [notes, setNotes] = useState("");
  async function submit() {
    try {
      await api.post(`/api/v1/events/${event.id}/cover`, { kind, notes, mail_id: mailId });
      toasts.push({ text: `Couverture « ${kind} » planifiée, tâche créée`, tone: "ok" });
      onDone();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  return (
    <div className="grid">
      <Field label="Type de couverture"><select className="field" value={kind} onChange={(ev) => setKind(ev.target.value)}>{kinds.map((k) => <option key={k}>{k}</option>)}</select></Field>
      <Field label="Notes"><textarea className="field" value={notes} onChange={(ev) => setNotes(ev.target.value)} placeholder="Angle, contact, matériel…" /></Field>
      <div className="form-foot"><Button variant="primary" onClick={() => void submit()}>Planifier</Button></div>
    </div>
  );
}

export function EventPage() {
  const { id = "" } = useParams();
  const toasts = useToasts();
  const reg = useRegistry();
  const [data, setData] = useState<{ event: EventRow; coverage: Coverage[]; links: LinkRow[]; actions: ActionRow[] } | null>(null);
  const [kinds, setKinds] = useState<string[]>([]);
  const [covering, setCovering] = useState(false);
  const load = useCallback(() => {
    api.get<{ event: EventRow; coverage: Coverage[]; links: LinkRow[]; actions: ActionRow[] }>(`/api/v1/events/${id}`).then(setData).catch(() => setData(null));
    api.get<{ coverage_kinds: string[] }>("/api/v1/events?limit=1").then((res) => setKinds(res.coverage_kinds)).catch(() => undefined);
  }, [id]);
  useEffect(load, [load]);
  if (!data) return <Page title="Événement"><Empty text="Événement introuvable (ou pas encore en cache)." /></Page>;
  const event = data.event;

  async function setStatus(cov: Coverage, status: string) {
    try {
      await api.patch(`/api/v1/events/coverage/${cov.id}`, { status });
      load();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }

  return (
    <Page
      title={event.title}
      subtitle={<span className="chips"><span>{fmtDate(event.starts_at)}{event.ends_at ? ` → ${new Date(event.ends_at).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}` : ""}</span>{event.venue_name ? <Pill>{event.venue_name}</Pill> : null}{event.organizer_name ? <Pill tone="muted">{event.organizer_name}</Pill> : null}{event.price ? <Pill tone="muted">{event.price}</Pill> : null}{event.is_radio_campus ? <Pill tone="live">Radio Campus</Pill> : null}</span>}
      actions={<>{event.url ? <a className="btn btn-ghost" href={event.url} target="_blank" rel="noreferrer">Page de l’événement</a> : null}<Button variant="primary" icon="mic" onClick={() => setCovering(true)}>Couvrir</Button></>}
    >
      <div className="split">
        <div className="grid">
          {event.image_url ? <img src={event.image_url} alt="" style={{ width: "100%", borderRadius: 12, border: "1px solid var(--hair)" }} /> : null}
          {event.description ? <Card title="Description"><p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{event.description}</p></Card> : null}
          {event.warnings?.length ? <Card title="Mappage Outlive" icon="warning"><ul className="rows">{event.warnings.map((w) => <li key={w} className="small muted">{w}</li>)}</ul></Card> : null}
          <Card title="Liens" icon="layers">
            {data.links.length ? <div className="chips">{data.links.map((l) => <span key={l.id} className="chips"><EntityChip entity={l.other} /><span className="small muted">{l.role}</span></span>)}</div> : <Empty text="Aucune fiche reliée (structure, lieu, mail…)." />}
          </Card>
        </div>
        <div className="grid">
          <Card title="Couverture" icon="mic">
            {data.coverage.length ? (
              <ul className="rows">
                {data.coverage.map((cov) => (
                  <li key={cov.id}>
                    <Pill>{cov.kind}</Pill>
                    <select className="field" style={{ maxWidth: 140 }} value={cov.status} onChange={(ev) => void setStatus(cov, ev.target.value)}>
                      {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                    <span className="small muted">{cov.notes}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty text="Pas encore couvert." icon="mic" />
            )}
          </Card>
          <Card title="Tâches" icon="check"><TaskQuickAdd kind="event" id={id} /></Card>
          <Card title="Commentaires" icon="list"><CommentsPanel kind="event" id={id} /></Card>
          {reg.can("radio") ? <Card title="Valorisation" icon="star"><Promotions kind="event" id={id} /></Card> : null}
        </div>
      </div>
      <Sheet open={covering} title={`Couvrir : ${event.title}`} onClose={() => setCovering(false)}>
        <CoverForm event={event} kinds={kinds} onDone={() => { setCovering(false); load(); }} />
      </Sheet>
    </Page>
  );
}

export function Promotions({ kind, id }: { kind: string; id: string | number }) {
  const [rows, setRows] = useState<{ id: number; channel: string; text_proposal: string; done: boolean }[]>([]);
  const load = useCallback(() => {
    api.get<{ promotions: { id: number; channel: string; text_proposal: string; done: boolean }[] }>(`/api/v1/radio/promotions${qs({ entity_kind: kind, entity_id: String(id) })}`).then((res) => setRows(res.promotions)).catch(() => undefined);
  }, [kind, id]);
  useEffect(load, [load]);
  return (
    <ul className="checklist">
      {rows.map((row) => (
        <li key={row.id} className={row.done ? "is-done" : ""} style={{ alignItems: "flex-start" }}>
          <Checkbox checked={row.done} onToggle={() => void api.post(`/api/v1/radio/promotions/${row.id}`, { done: !row.done }).then(load)} label="" />
          <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <strong className="small">{row.channel}</strong>
            <span className="small" style={{ cursor: "copy" }} title="Cliquer pour copier" onClick={() => void navigator.clipboard.writeText(row.text_proposal)}>{row.text_proposal}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

export function CoverageBoard() {
  const [rows, setRows] = useState<(Coverage & { event: EventRow | null })[]>([]);
  useEffect(() => {
    api.get<{ coverage: (Coverage & { event: EventRow | null })[] }>("/api/v1/events/coverage").then((res) => setRows(res.coverage)).catch(() => undefined);
  }, []);
  const groups: Record<string, typeof rows> = { idea: [], planned: [], done: [], skipped: [] };
  for (const row of rows) (groups[row.status] ??= []).push(row);
  return (
    <Page title="Couvertures" subtitle="Ce que la radio prévoit de couvrir, par état." wide>
      <div className="kanban" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }}>
        {Object.entries(groups).map(([status, list]) => (
          <div key={status} className="kanban-col">
            <div className="kanban-head"><span>{STATUS_LABEL[status]}</span><span>{list.length}</span></div>
            {list.map((row) => (
              <Link key={row.id} to={`/events/${row.event_id}`} className="task-card">
                <span className="task-title">{row.event?.title || row.event_id}</span>
                <span className="task-meta"><Pill>{row.kind}</Pill>{row.event ? <span>{fmtDate(row.event.starts_at)}</span> : null}</span>
              </Link>
            ))}
          </div>
        ))}
      </div>
    </Page>
  );
}
