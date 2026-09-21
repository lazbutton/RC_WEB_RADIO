import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type ActionRow, type LinkRow } from "../../api/client";
import { Button } from "../../components/Button";
import { Card, Empty, Field, Page, Pill, fmtDate } from "../../components/Page";
import { useToasts } from "../../components/Toast";
import { EntityChip } from "../../lib/registry";
import { CommentsPanel } from "../planning/CommentsPanel";
import { TaskQuickAdd } from "../planning/TaskQuickAdd";

type View = {
  kind: string;
  entity: Record<string, unknown> & { id: number };
  links: LinkRow[];
  actions: ActionRow[];
  affiliations?: { id: number; role: string; organization_id: number; organization_name: string }[];
  members?: { id: number; role: string; person_id: number; display_name: string }[];
  interactions?: { id: number; kind: string; at: string; summary: string; ref_kind: string; ref_id: string }[];
  events?: { id: string; title: string; starts_at: string; venue_name?: string; organizer_name?: string }[];
  mails?: { id: number; subject: string; mailed_at: string; category: string; status: string }[];
  merged_into?: number;
};

const FIELDS: Record<string, { key: string; label: string; multi?: boolean; area?: boolean }[]> = {
  person: [
    { key: "display_name", label: "Nom affiché" },
    { key: "first_name", label: "Prénom" },
    { key: "last_name", label: "Nom" },
    { key: "job_title", label: "Fonction" },
    { key: "emails", label: "E-mails", multi: true },
    { key: "phones", label: "Téléphones", multi: true },
    { key: "tags", label: "Tags", multi: true },
    { key: "notes", label: "Notes", area: true },
  ],
  organization: [
    { key: "name", label: "Nom" },
    { key: "kind", label: "Type" },
    { key: "website", label: "Site" },
    { key: "emails", label: "E-mails", multi: true },
    { key: "domains", label: "Domaines", multi: true },
    { key: "address", label: "Adresse" },
    { key: "tags", label: "Tags", multi: true },
    { key: "notes", label: "Notes", area: true },
  ],
  place: [
    { key: "name", label: "Nom" },
    { key: "address", label: "Adresse" },
    { key: "city", label: "Ville" },
    { key: "capacity", label: "Capacité" },
    { key: "website", label: "Site" },
    { key: "notes", label: "Notes", area: true },
  ],
};

export function EntityPage() {
  const { kind = "person", id = "" } = useParams();
  const navigate = useNavigate();
  const toasts = useToasts();
  const [view, setView] = useState<View | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    api.get<View>(`/api/v1/contacts/${kind}/${id}`).then(setView).catch(() => setView(null));
  }, [kind, id]);
  useEffect(load, [load]);

  useEffect(() => {
    if (view?.merged_into) navigate(`/contacts/${kind}/${view.merged_into}`, { replace: true });
  }, [view, kind, navigate]);

  if (!view) return <Page title="Fiche"><Empty text="Fiche introuvable." /></Page>;
  const entity = view.entity;
  const title = String(entity.display_name || entity.name || "");

  function startEdit() {
    const next: Record<string, string> = {};
    for (const field of FIELDS[kind] || []) {
      const value = entity[field.key];
      next[field.key] = Array.isArray(value) ? value.join(", ") : value === null || value === undefined ? "" : String(value);
    }
    setDraft(next);
    setEditing(true);
  }

  async function save() {
    const payload: Record<string, unknown> = {};
    for (const field of FIELDS[kind] || []) {
      const raw = draft[field.key] ?? "";
      payload[field.key] = field.multi ? raw.split(/[,;]+/).map((s) => s.trim()).filter(Boolean) : field.key === "capacity" ? (raw ? Number(raw) : null) : raw;
    }
    try {
      await api.patch(`/api/v1/contacts/${kind}/${id}`, { data: payload });
      setEditing(false);
      load();
      toasts.push({ text: "Fiche enregistrée", tone: "ok" });
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }

  async function erase() {
    if (!window.confirm(kind === "person" ? "Effacer cette personne, ses interactions et ses liens ? (RGPD : irréversible)" : "Supprimer cette fiche ?")) return;
    await api.delete(`/api/v1/contacts/${kind}/${id}`);
    navigate(`/contacts?kind=${kind}`);
  }

  return (
    <Page
      title={title}
      subtitle={<span className="chips">{entity.job_title ? <span>{String(entity.job_title)}</span> : null}{entity.kind ? <Pill>{String(entity.kind)}</Pill> : null}{entity.source ? <Pill tone="muted">source : {String(entity.source)}</Pill> : null}{(entity.tags as string[] | undefined)?.map((t) => <Pill key={t}>{t}</Pill>)}</span>}
      actions={
        <>
          <Button icon="download" onClick={() => window.open(`/api/v1/contacts/${kind}/${id}`, "_blank")}>Exporter (JSON)</Button>
          <Button icon="trash" variant="danger" onClick={() => void erase()}>{kind === "person" ? "Effacer (RGPD)" : "Supprimer"}</Button>
          <Button variant="primary" onClick={editing ? () => void save() : startEdit}>{editing ? "Enregistrer" : "Modifier"}</Button>
        </>
      }
    >
      <div className="split">
        <div className="grid">
          <Card title="Fiche" icon={kind === "person" ? "user" : kind === "organization" ? "building" : "pin"}>
            {editing ? (
              <div className="grid">
                {(FIELDS[kind] || []).map((field) => (
                  <Field key={field.key} label={field.label} hint={field.multi ? "séparés par des virgules" : undefined}>
                    {field.area ? <textarea className="field" value={draft[field.key] || ""} onChange={(ev) => setDraft((p) => ({ ...p, [field.key]: ev.target.value }))} /> : <input className="field" value={draft[field.key] || ""} onChange={(ev) => setDraft((p) => ({ ...p, [field.key]: ev.target.value }))} />}
                  </Field>
                ))}
                <div className="form-foot"><Button onClick={() => setEditing(false)}>Annuler</Button></div>
              </div>
            ) : (
              <dl className="dl">
                {(FIELDS[kind] || []).map((field) => {
                  const value = entity[field.key];
                  const text = Array.isArray(value) ? value.join(", ") : value === null || value === undefined ? "" : String(value);
                  if (!text) return null;
                  return (
                    <div key={field.key} className="dl-row">
                      <dt>{field.label}</dt>
                      <dd style={{ whiteSpace: field.area ? "pre-wrap" : undefined }}>{field.key === "emails" ? text.split(", ").map((e) => <a key={e} href={`mailto:${e}`}>{e} </a>) : field.key === "website" ? <a href={text} target="_blank" rel="noreferrer">{text}</a> : text}</dd>
                    </div>
                  );
                })}
              </dl>
            )}
          </Card>
          {view.affiliations ? (
            <Card title="Structures" icon="building">
              {view.affiliations.length ? <ul className="rows">{view.affiliations.map((a) => <li key={a.id}><Link to={`/contacts/organization/${a.organization_id}`}>{a.organization_name}</Link><span className="muted small">{a.role}</span><span className="spacer" /><Button variant="icon" icon="close" aria-label="Retirer" onClick={() => void api.delete(`/api/v1/contacts/affiliations/${a.id}`).then(load)} /></li>)}</ul> : <Empty text="Aucune structure reliée." />}
              <AffiliationAdd personId={Number(id)} onDone={load} />
            </Card>
          ) : null}
          {view.members ? (
            <Card title="Membres" icon="users">
              {view.members.length ? <ul className="rows">{view.members.map((m) => <li key={m.id}><Link to={`/contacts/person/${m.person_id}`}>{m.display_name}</Link><span className="muted small">{m.role}</span></li>)}</ul> : <Empty text="Personne relié pour l’instant." />}
            </Card>
          ) : null}
          <Card title="Liens" icon="layers">
            {view.links.length ? <div className="chips">{view.links.map((link) => <span key={link.id} className="chips"><EntityChip entity={link.other} /><span className="small muted">{link.role}</span></span>)}</div> : <Empty text="Aucun lien." />}
          </Card>
        </div>
        <div className="grid">
          <Card title="Tâches" icon="check"><TaskQuickAdd kind={kind} id={id} /></Card>
          {view.mails ? (
            <Card title="Mails reçus" icon="mail">
              {view.mails.length ? <ul className="rows">{view.mails.map((m) => <li key={m.id}><span className="when">{fmtDate(m.mailed_at, false)}</span><Link to={`/mails?id=${m.id}`}>{m.subject}</Link><span className="spacer" /><Pill tone="muted">{m.category}</Pill></li>)}</ul> : <Empty text="Aucun mail relié." icon="mail" />}
            </Card>
          ) : null}
          {view.events ? (
            <Card title="Événements" icon="calendar">
              {view.events.length ? <ul className="rows">{view.events.map((e) => <li key={e.id}><span className="when">{fmtDate(e.starts_at)}</span><Link to={`/events/${e.id}`}>{e.title}</Link><span className="muted small">{e.venue_name || e.organizer_name}</span></li>)}</ul> : <Empty text="Aucun événement Outlive relié." icon="calendar" />}
            </Card>
          ) : null}
          {view.interactions ? (
            <Card title="Interactions" icon="activity">
              {view.interactions.length ? <ul className="rows">{view.interactions.map((i) => <li key={i.id}><span className="when">{fmtDate(i.at)}</span><Pill tone="muted">{i.kind}</Pill><span>{i.summary}</span></li>)}</ul> : <Empty text="Aucune interaction notée." />}
              <InteractionAdd kind={kind} id={Number(id)} onDone={load} />
            </Card>
          ) : null}
          <Card title="Commentaires" icon="list"><CommentsPanel kind={kind} id={id} /></Card>
          {view.actions.length ? <Card title="Historique" icon="history"><ul className="rows">{view.actions.map((a) => <li key={a.id}><span className="when">{fmtDate(a.created_at)}</span><span>{a.label}</span><Pill tone={a.status === "failed" ? "warn" : "muted"}>{a.status}</Pill></li>)}</ul></Card> : null}
        </div>
      </div>
    </Page>
  );
}

function AffiliationAdd({ personId, onDone }: { personId: number; onDone: () => void }) {
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [hits, setHits] = useState<{ id: number; name: string }[]>([]);
  useEffect(() => {
    if (q.trim().length < 2) return setHits([]);
    const timer = window.setTimeout(() => api.get<{ items: { id: number; name: string }[] }>(`/api/v1/contacts/organization?q=${encodeURIComponent(q)}&limit=6`).then((r) => setHits(r.items)).catch(() => undefined), 150);
    return () => window.clearTimeout(timer);
  }, [q]);
  return (
    <div className="form-row">
      <Field label="Ajouter une structure"><input className="field" value={q} onChange={(ev) => setQ(ev.target.value)} placeholder="Nom de la structure" /></Field>
      <Field label="Rôle"><input className="field" value={role} onChange={(ev) => setRole(ev.target.value)} placeholder="programmateur, attaché de presse…" /></Field>
      {hits.length ? <div className="chips">{hits.map((h) => <Button key={h.id} onClick={() => void api.post("/api/v1/contacts/affiliations", { person_id: personId, organization_id: h.id, role }).then(() => { setQ(""); onDone(); })}>{h.name}</Button>)}</div> : null}
    </div>
  );
}

function InteractionAdd({ kind, id, onDone }: { kind: string; id: number; onDone: () => void }) {
  const [summary, setSummary] = useState("");
  const [type, setType] = useState("note");
  async function add() {
    if (!summary.trim()) return;
    await api.post("/api/v1/contacts/interactions", { kind: type, summary, [kind === "person" ? "person_id" : "organization_id"]: id });
    setSummary("");
    onDone();
  }
  return (
    <div className="form-row">
      <Field label="Type"><select className="field" value={type} onChange={(ev) => setType(ev.target.value)}>{["note", "call", "meeting", "visit", "mail"].map((t) => <option key={t}>{t}</option>)}</select></Field>
      <Field label="Noter une interaction"><input className="field" value={summary} onChange={(ev) => setSummary(ev.target.value)} onKeyDown={(ev) => ev.key === "Enter" && void add()} placeholder="Appel : d’accord pour l’interview le 12" /></Field>
      <Button onClick={() => void add()} disabled={!summary.trim()}>Ajouter</Button>
    </div>
  );
}
