import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, qs } from "../../api/client";
import { Button } from "../../components/Button";
import { Card, Empty, Field, Page, Pill, Tabs, fmtDate, fmtDuration } from "../../components/Page";
import { Sheet } from "../../components/Sheet";
import { useToasts } from "../../components/Toast";

type Tab = "guests" | "bookings" | "volunteers" | "partnerships" | "rundowns" | "listening";
type Guest = { id: number; name: string; topic: string; status: string; planned_on: string | null; authorization_status: string; attestation_path: string; notes: string; person_id: number | null; show_id: number | null };
const GUEST_LABEL: Record<string, string> = { idee: "Idée", contacte: "Contacté·e", confirme: "Confirmé·e", venu: "Venu·e", annule: "Annulé" };

export function RadioPage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "guests";
  return (
    <Page title="Vie de la radio" subtitle="Invités, studio, volontaires, partenariats, conducteurs, écoute." wide>
      <Tabs value={tab} onChange={(value) => setParams({ tab: value })} items={[{ id: "guests" as Tab, label: "Invités" }, { id: "bookings" as Tab, label: "Réservations" }, { id: "volunteers" as Tab, label: "Volontaires" }, { id: "partnerships" as Tab, label: "Partenariats" }, { id: "rundowns" as Tab, label: "Conducteurs" }, { id: "listening" as Tab, label: "Écoute" }]} />
      {tab === "guests" ? <Guests openId={params.get("id")} /> : null}
      {tab === "bookings" ? <Bookings /> : null}
      {tab === "volunteers" ? <Volunteers /> : null}
      {tab === "partnerships" ? <Partnerships /> : null}
      {tab === "rundowns" ? <Rundowns openId={params.get("id")} /> : null}
      {tab === "listening" ? <Listening /> : null}
    </Page>
  );
}

async function openPdf(url: string): Promise<void> {
  const res = await fetch(url, { method: "POST", credentials: "include", headers: { "X-Regie": "1" } });
  if (!res.ok) throw new Error("PDF impossible");
  const blob = await res.blob();
  window.open(URL.createObjectURL(blob), "_blank", "noopener");
}

function usePeople() {
  const [people, setPeople] = useState<{ id: number; display_name: string }[]>([]);
  useEffect(() => {
    api.get<{ items: { id: number; display_name: string }[] }>("/api/v1/contacts/person?limit=500").then((res) => setPeople(res.items)).catch(() => undefined);
  }, []);
  return people;
}

function Guests({ openId }: { openId: string | null }) {
  const toasts = useToasts();
  const [pipeline, setPipeline] = useState<Record<string, Guest[]>>({});
  const [flow, setFlow] = useState<Record<string, string[]>>({});
  const [creating, setCreating] = useState(false);
  const people = usePeople();
  const load = useCallback(() => {
    api.get<{ pipeline: Record<string, Guest[]>; flow: Record<string, string[]> }>("/api/v1/radio/guests").then((res) => { setPipeline(res.pipeline); setFlow(res.flow); }).catch(() => undefined);
  }, []);
  useEffect(load, [load]);

  async function move(guest: Guest, status: string) {
    try {
      const res = await api.post<{ action: { id: number } }>(`/api/v1/radio/guests/${guest.id}/status`, { status });
      toasts.push({ text: `${guest.name} → ${GUEST_LABEL[status]}`, tone: "ok", action: { label: "Annuler", onClick: () => api.post(`/api/v1/actions/${res.action.id}/undo`).then(load) } });
      load();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn", duration: 7000 });
    }
  }

  return (
    <>
      <div className="toolbar"><span className="muted small">Pipeline « idée invitée » : une étape à la fois ; « Venu·e » exige l’autorisation signée.</span><span className="spacer" /><Button variant="primary" icon="plus" onClick={() => setCreating(true)}>Nouvelle idée d’invité</Button></div>
      <div className="pipeline">
        {Object.keys(GUEST_LABEL).map((status) => (
          <div key={status} className="kanban-col">
            <div className="kanban-head"><span>{GUEST_LABEL[status]}</span><span>{pipeline[status]?.length || 0}</span></div>
            {(pipeline[status] || []).map((guest) => (
              <div key={guest.id} className={`task-card${String(guest.id) === openId ? " is-on" : ""}`} style={{ cursor: "default" }}>
                <span className="task-title">{guest.person_id ? <Link to={`/contacts/person/${guest.person_id}`}>{guest.name}</Link> : guest.name}</span>
                <span className="small muted">{guest.topic}</span>
                <span className="task-meta">
                  {guest.planned_on ? <span>{guest.planned_on}</span> : null}
                  <Pill tone={guest.authorization_status === "signed" ? "ok" : guest.authorization_status === "none" ? "muted" : "live"}>autorisation : {guest.authorization_status}</Pill>
                </span>
                <span className="toolbar">
                  {(flow[status] || []).map((next) => <Button key={next} variant="quiet" onClick={() => void move(guest, next)}>{GUEST_LABEL[next]}</Button>)}
                </span>
                <span className="toolbar">
                  <Button variant="quiet" icon="file" onClick={() => void openPdf(`/api/v1/radio/guests/${guest.id}/authorization.pdf`).then(load)}>PDF d’autorisation</Button>
                  {guest.authorization_status !== "signed" ? <Button variant="quiet" icon="check" onClick={() => void api.patch(`/api/v1/radio/guests/${guest.id}`, { data: { authorization_status: "signed" } }).then(load)}>Signée</Button> : null}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
      <Sheet open={creating} title="Nouvelle idée d’invité" onClose={() => setCreating(false)}>
        <GuestForm people={people} onDone={() => { setCreating(false); load(); }} />
      </Sheet>
    </>
  );
}

function GuestForm({ people, onDone }: { people: { id: number; display_name: string }[]; onDone: () => void }) {
  const toasts = useToasts();
  const [data, setData] = useState<Record<string, string>>({});
  const set = (key: string) => (ev: { target: { value: string } }) => setData((p) => ({ ...p, [key]: ev.target.value }));
  async function submit() {
    try {
      await api.post("/api/v1/radio/guests", { data: { name: data.name, topic: data.topic, planned_on: data.planned_on || null, notes: data.notes, person_id: data.person_id ? Number(data.person_id) : null } });
      onDone();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  return (
    <div className="grid">
      <Field label="Personne connue"><select className="field" value={data.person_id || ""} onChange={set("person_id")}><option value="">— nouvelle personne —</option>{people.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}</select></Field>
      {!data.person_id ? <Field label="Nom"><input className="field" value={data.name || ""} onChange={set("name")} /></Field> : null}
      <Field label="Sujet"><input className="field" value={data.topic || ""} onChange={set("topic")} placeholder="Nouvel album, festival, actu locale…" /></Field>
      <Field label="Date envisagée"><input className="field" type="date" value={data.planned_on || ""} onChange={set("planned_on")} /></Field>
      <Field label="Notes"><textarea className="field" value={data.notes || ""} onChange={set("notes")} /></Field>
      <div className="form-foot"><Button variant="primary" disabled={!data.name && !data.person_id} onClick={() => void submit()}>Créer</Button></div>
    </div>
  );
}

function Bookings() {
  const toasts = useToasts();
  const [resources, setResources] = useState<{ id: number; name: string; kind: string }[]>([]);
  const [rows, setRows] = useState<{ id: number; title: string; resource_name: string; user_name: string; starts_at: string; ends_at: string }[]>([]);
  const [form, setForm] = useState<Record<string, string>>({});
  const [newResource, setNewResource] = useState("");
  const load = useCallback(() => {
    const start = new Date();
    start.setDate(start.getDate() - 1);
    const end = new Date();
    end.setDate(end.getDate() + 30);
    api.get<{ resources: { id: number; name: string; kind: string }[] }>("/api/v1/radio/resources").then((res) => setResources(res.resources)).catch(() => undefined);
    api.get<{ bookings: typeof rows }>(`/api/v1/radio/bookings${qs({ start: start.toISOString(), end: end.toISOString() })}`).then((res) => setRows(res.bookings)).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  async function book() {
    try {
      await api.post("/api/v1/radio/bookings", { data: { resource_id: Number(form.resource_id || resources[0]?.id), title: form.title, starts_at: new Date(form.starts_at).toISOString(), ends_at: new Date(form.ends_at).toISOString() } });
      toasts.push({ text: "Créneau réservé", tone: "ok" });
      setForm({});
      load();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn", duration: 7000 });
    }
  }
  return (
    <div className="grid cols-2">
      <Card title="Réserver" icon="key">
        {resources.length ? (
          <>
            <Field label="Studio / matériel"><select className="field" value={form.resource_id || resources[0]?.id} onChange={(ev) => setForm((p) => ({ ...p, resource_id: ev.target.value }))}>{resources.map((r) => <option key={r.id} value={r.id}>{r.name} ({r.kind})</option>)}</select></Field>
            <Field label="Pour quoi"><input className="field" value={form.title || ""} onChange={(ev) => setForm((p) => ({ ...p, title: ev.target.value }))} placeholder="Enregistrement HPH" /></Field>
            <div className="form-row">
              <Field label="Début"><input className="field" type="datetime-local" value={form.starts_at || ""} onChange={(ev) => setForm((p) => ({ ...p, starts_at: ev.target.value }))} /></Field>
              <Field label="Fin"><input className="field" type="datetime-local" value={form.ends_at || ""} onChange={(ev) => setForm((p) => ({ ...p, ends_at: ev.target.value }))} /></Field>
            </div>
            <div className="form-foot"><Button variant="primary" disabled={!form.starts_at || !form.ends_at} onClick={() => void book()}>Réserver</Button></div>
          </>
        ) : (
          <Empty text="Aucune ressource déclarée." icon="key" />
        )}
        <div className="toolbar"><input className="field" placeholder="Ajouter une ressource (Studio A, Zoom H6…)" value={newResource} onChange={(ev) => setNewResource(ev.target.value)} /><Button onClick={() => void api.post("/api/v1/radio/resources", { data: { name: newResource, kind: /studio/i.test(newResource) ? "studio" : "materiel" } }).then(() => { setNewResource(""); load(); }).catch((err) => toasts.push({ text: err instanceof Error ? err.message : "réservé aux admins", tone: "warn" }))} disabled={!newResource.trim()}>Ajouter</Button></div>
      </Card>
      <Card title="Prochaines réservations" icon="clock">
        {rows.length ? <ul className="rows">{rows.map((b) => <li key={b.id}><span className="when">{fmtDate(b.starts_at)}</span><span>{b.title}</span><Pill tone="muted">{b.resource_name}</Pill><span className="small muted">{b.user_name}</span><span className="spacer" /><Button variant="icon" icon="close" aria-label="Annuler" onClick={() => void api.delete(`/api/v1/radio/bookings/${b.id}`).then(load).catch((err) => toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" }))} /></li>)}</ul> : <Empty text="Rien de réservé." icon="clock" />}
      </Card>
    </div>
  );
}

function Volunteers() {
  const toasts = useToasts();
  const people = usePeople();
  const [rows, setRows] = useState<{ id: number; kind: string; mission: string; status: string; start_date: string | null; end_date: string | null; hours_per_week: number | null; attestation_path: string; person: { id: number; display_name: string } | null }[]>([]);
  const [form, setForm] = useState<Record<string, string>>({ kind: "benevole" });
  const load = useCallback(() => {
    api.get<{ volunteers: typeof rows }>("/api/v1/radio/volunteers").then((res) => setRows(res.volunteers)).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  async function add() {
    try {
      await api.post("/api/v1/radio/volunteers", { data: { person_id: Number(form.person_id), kind: form.kind, mission: form.mission, start_date: form.start_date || null, end_date: form.end_date || null, hours_per_week: form.hours ? Number(form.hours) : null } });
      setForm({ kind: "benevole" });
      load();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  return (
    <div className="grid cols-2">
      <Card title="Volontaires, services civiques, stagiaires" icon="users">
        {rows.length ? (
          <table className="table">
            <thead><tr><th>Personne</th><th>Statut</th><th>Mission</th><th>Période</th><th></th></tr></thead>
            <tbody>
              {rows.map((v) => (
                <tr key={v.id}>
                  <td>{v.person ? <Link to={`/contacts/person/${v.person.id}`}>{v.person.display_name}</Link> : "?"}</td>
                  <td><Pill>{v.kind.replace("_", " ")}</Pill> <Pill tone={v.status === "actif" ? "ok" : "muted"}>{v.status}</Pill></td>
                  <td>{v.mission}{v.hours_per_week ? <span className="small muted"> · {v.hours_per_week} h/sem</span> : null}</td>
                  <td className="small muted">{v.start_date || "?"} → {v.end_date || "…"}</td>
                  <td><Button variant="quiet" icon="file" onClick={() => void openPdf(`/api/v1/radio/volunteers/${v.id}/attestation.pdf`).then(load)}>Attestation</Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty text="Personne pour l’instant." icon="users" />
        )}
      </Card>
      <Card title="Ajouter" icon="plus">
        <Field label="Personne (fiche contact)"><select className="field" value={form.person_id || ""} onChange={(ev) => setForm((p) => ({ ...p, person_id: ev.target.value }))}><option value="">—</option>{people.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}</select></Field>
        <div className="form-row">
          <Field label="Statut"><select className="field" value={form.kind} onChange={(ev) => setForm((p) => ({ ...p, kind: ev.target.value }))}><option value="benevole">bénévole</option><option value="service_civique">service civique</option><option value="stagiaire">stagiaire</option><option value="salarie">salarié·e</option></select></Field>
          <Field label="Heures / semaine"><input className="field" type="number" value={form.hours || ""} onChange={(ev) => setForm((p) => ({ ...p, hours: ev.target.value }))} /></Field>
        </div>
        <Field label="Mission"><input className="field" value={form.mission || ""} onChange={(ev) => setForm((p) => ({ ...p, mission: ev.target.value }))} /></Field>
        <div className="form-row">
          <Field label="Début"><input className="field" type="date" value={form.start_date || ""} onChange={(ev) => setForm((p) => ({ ...p, start_date: ev.target.value }))} /></Field>
          <Field label="Fin"><input className="field" type="date" value={form.end_date || ""} onChange={(ev) => setForm((p) => ({ ...p, end_date: ev.target.value }))} /></Field>
        </div>
        <div className="form-foot"><Button variant="primary" disabled={!form.person_id} onClick={() => void add()}>Ajouter</Button></div>
      </Card>
    </div>
  );
}

function Partnerships() {
  const toasts = useToasts();
  const [rows, setRows] = useState<{ id: number; title: string; kind: string; status: string; terms: string; starts_on: string | null; ends_on: string | null; organization: { id: number; name: string } | null }[]>([]);
  const [orgs, setOrgs] = useState<{ id: number; name: string }[]>([]);
  const [form, setForm] = useState<Record<string, string>>({ kind: "echange" });
  const load = useCallback(() => {
    api.get<{ partnerships: typeof rows }>("/api/v1/radio/partnerships").then((res) => setRows(res.partnerships)).catch(() => undefined);
    api.get<{ items: { id: number; name: string }[] }>("/api/v1/contacts/organization?limit=500").then((res) => setOrgs(res.items)).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  async function add() {
    try {
      await api.post("/api/v1/radio/partnerships", { data: { organization_id: Number(form.organization_id), title: form.title, kind: form.kind, terms: form.terms, starts_on: form.starts_on || null, ends_on: form.ends_on || null } });
      setForm({ kind: "echange" });
      load();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  return (
    <div className="grid cols-2">
      <Card title="Partenariats" icon="handshake">
        {rows.length ? <ul className="rows">{rows.map((p) => <li key={p.id} style={{ flexWrap: "wrap" }}><strong>{p.title}</strong>{p.organization ? <Link to={`/contacts/organization/${p.organization.id}`}>{p.organization.name}</Link> : null}<Pill>{p.kind}</Pill><select className="field" style={{ maxWidth: 130 }} value={p.status} onChange={(ev) => void api.patch(`/api/v1/radio/partnerships/${p.id}`, { data: { status: ev.target.value } }).then(load)}>{["discussion", "signe", "actif", "termine"].map((s) => <option key={s}>{s}</option>)}</select><span className="small muted">{p.starts_on || ""} {p.ends_on ? `→ ${p.ends_on}` : ""}</span>{p.terms ? <span className="small muted" style={{ flexBasis: "100%" }}>{p.terms}</span> : null}</li>)}</ul> : <Empty text="Aucun partenariat." icon="handshake" />}
      </Card>
      <Card title="Nouveau partenariat" icon="plus">
        <Field label="Structure"><select className="field" value={form.organization_id || ""} onChange={(ev) => setForm((p) => ({ ...p, organization_id: ev.target.value }))}><option value="">—</option>{orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select></Field>
        <Field label="Titre"><input className="field" value={form.title || ""} onChange={(ev) => setForm((p) => ({ ...p, title: ev.target.value }))} /></Field>
        <Field label="Type"><select className="field" value={form.kind} onChange={(ev) => setForm((p) => ({ ...p, kind: ev.target.value }))}>{["echange", "soutien", "coproduction", "media", "subvention"].map((k) => <option key={k}>{k}</option>)}</select></Field>
        <Field label="Termes"><textarea className="field" value={form.terms || ""} onChange={(ev) => setForm((p) => ({ ...p, terms: ev.target.value }))} /></Field>
        <div className="form-row">
          <Field label="Début"><input className="field" type="date" value={form.starts_on || ""} onChange={(ev) => setForm((p) => ({ ...p, starts_on: ev.target.value }))} /></Field>
          <Field label="Fin"><input className="field" type="date" value={form.ends_on || ""} onChange={(ev) => setForm((p) => ({ ...p, ends_on: ev.target.value }))} /></Field>
        </div>
        <div className="form-foot"><Button variant="primary" disabled={!form.organization_id || !form.title} onClick={() => void add()}>Créer</Button></div>
      </Card>
    </div>
  );
}

type RundownItem = { kind: string; title: string; duration_s: number; notes?: string; at_s?: number; done?: boolean };
type Rundown = { id: number; title: string; aired_on: string | null; status: string; items: RundownItem[]; total_s?: number };

function Rundowns({ openId }: { openId: string | null }) {
  const toasts = useToasts();
  const [rows, setRows] = useState<Rundown[]>([]);
  const [current, setCurrent] = useState<Rundown | null>(null);
  const load = useCallback(() => {
    api.get<{ rundowns: Rundown[] }>("/api/v1/radio/rundowns").then((res) => {
      setRows(res.rundowns);
      if (openId) setCurrent(res.rundowns.find((r) => String(r.id) === openId) || null);
    }).catch(() => undefined);
  }, [openId]);
  useEffect(load, [load]);

  async function save(rundown: Rundown) {
    try {
      const payload = { title: rundown.title, aired_on: rundown.aired_on, status: rundown.status, items: rundown.items };
      const res = rundown.id ? await api.put<{ rundown: Rundown }>(`/api/v1/radio/rundowns/${rundown.id}`, { data: payload }) : await api.post<{ rundown: Rundown }>("/api/v1/radio/rundowns", { data: payload });
      setCurrent(res.rundown);
      load();
      toasts.push({ text: "Conducteur enregistré", tone: "ok" });
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  const total = (items: RundownItem[]) => items.reduce((sum, i) => sum + (Number(i.duration_s) || 0), 0);

  return (
    <div className="split">
      <Card title="Conducteurs" icon="list" actions={<Button icon="plus" onClick={() => setCurrent({ id: 0, title: "", aired_on: new Date().toISOString().slice(0, 10), status: "brouillon", items: [{ kind: "jingle", title: "Ouverture", duration_s: 30 }, { kind: "parole", title: "Intro", duration_s: 120 }] })}>Nouveau</Button>}>
        {rows.length ? <ul className="rows">{rows.map((r) => <li key={r.id}><span className="when">{r.aired_on}</span><button type="button" className="top-link" onClick={() => setCurrent(r)}>{r.title || "(sans titre)"}</button><Pill tone={r.status === "pret" ? "ok" : "muted"}>{r.status}</Pill><span className="small muted">{fmtDuration(total(r.items))}</span></li>)}</ul> : <Empty text="Aucun conducteur." icon="list" />}
      </Card>
      {current ? (
        <Card title={current.id ? "Conducteur" : "Nouveau conducteur"} icon="list" actions={<><select className="field" value={current.status} onChange={(ev) => setCurrent({ ...current, status: ev.target.value })}>{["brouillon", "pret", "diffuse"].map((s) => <option key={s}>{s}</option>)}</select><Button variant="primary" onClick={() => void save(current)}>Enregistrer</Button></>}>
          <div className="form-row">
            <Field label="Titre"><input className="field" value={current.title} onChange={(ev) => setCurrent({ ...current, title: ev.target.value })} /></Field>
            <Field label="Date"><input className="field" type="date" value={current.aired_on || ""} onChange={(ev) => setCurrent({ ...current, aired_on: ev.target.value })} /></Field>
          </div>
          <table className="table">
            <thead><tr><th>À</th><th>Type</th><th>Titre</th><th>Durée (s)</th><th></th></tr></thead>
            <tbody>
              {current.items.map((item, index) => {
                const at = current.items.slice(0, index).reduce((s, i) => s + (Number(i.duration_s) || 0), 0);
                return (
                  <tr key={index}>
                    <td className="muted">{fmtDuration(at)}</td>
                    <td><select className="field" value={item.kind} onChange={(ev) => setCurrent({ ...current, items: current.items.map((i, j) => (j === index ? { ...i, kind: ev.target.value } : i)) })}>{["jingle", "parole", "musique", "itw", "chronique", "pub", "live", "autre"].map((k) => <option key={k}>{k}</option>)}</select></td>
                    <td><input className="field" value={item.title} onChange={(ev) => setCurrent({ ...current, items: current.items.map((i, j) => (j === index ? { ...i, title: ev.target.value } : i)) })} /></td>
                    <td><input className="field" type="number" style={{ width: 90 }} value={item.duration_s} onChange={(ev) => setCurrent({ ...current, items: current.items.map((i, j) => (j === index ? { ...i, duration_s: Number(ev.target.value) } : i)) })} /></td>
                    <td><Button variant="icon" icon="close" aria-label="Retirer" onClick={() => setCurrent({ ...current, items: current.items.filter((_, j) => j !== index) })} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="toolbar"><Button icon="plus" onClick={() => setCurrent({ ...current, items: [...current.items, { kind: "parole", title: "", duration_s: 180 }] })}>Ajouter une ligne</Button><span className="spacer" /><strong>Total {fmtDuration(total(current.items))}</strong></div>
        </Card>
      ) : (
        <Empty text="Choisis un conducteur ou crée-en un." icon="list" />
      )}
    </div>
  );
}

function Listening() {
  const [data, setData] = useState<{ latest: { mount: string; listeners: number; peak: number; title: string; at: string }[]; hours: { mount: string; hour: string; avg_listeners: number; max_listeners: number }[]; configured: boolean } | null>(null);
  useEffect(() => {
    api.get<typeof data>("/api/v1/radio/listening").then(setData).catch(() => undefined);
  }, []);
  if (!data) return null;
  const max = Math.max(1, ...data.hours.map((h) => Number(h.max_listeners)));
  return (
    <div className="grid">
      {!data.configured ? <p className="muted small">Icecast non configuré : renseigne `radio.icecast_url` dans les réglages (ex. http://icecast.local:8000). Régie relève les auditeurs toutes les 5 minutes.</p> : null}
      <div className="grid cols-3">{data.latest.map((row) => <div key={row.mount} className="stat"><span className="stat-value">{row.listeners}</span><span className="stat-label">{row.mount} · pic {row.peak}{row.title ? ` · ${row.title}` : ""}</span></div>)}</div>
      {data.hours.length ? (
        <Card title="7 derniers jours (moyenne horaire)" icon="activity">
          <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 120 }}>
            {data.hours.slice(-168).map((h, i) => <span key={i} title={`${h.hour} · ${Number(h.avg_listeners).toFixed(1)} auditeurs`} style={{ flex: 1, background: "var(--ink)", height: `${(Number(h.avg_listeners) / max) * 100}%`, minHeight: 1 }} />)}
          </div>
        </Card>
      ) : (
        <Empty text="Pas encore de mesures." icon="activity" />
      )}
    </div>
  );
}
