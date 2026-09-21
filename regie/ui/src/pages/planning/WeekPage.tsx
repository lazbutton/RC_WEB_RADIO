import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, qs } from "../../api/client";
import { Button } from "../../components/Button";
import { Card, Empty, Field, Page, Pill } from "../../components/Page";
import { Sheet } from "../../components/Sheet";
import { useToasts } from "../../components/Toast";

type Appointment = { id: number; title: string; starts_at: string; ends_at: string; all_day: boolean; account_id: number; account_label?: string; account_color?: string; location?: string; external_id?: string; html_link?: string };
type Week = {
  start: string;
  end: string;
  appointments: Appointment[];
  events: { id: string; title: string; starts_at: string; venue_name: string; coverage: { kind: string }[]; is_radio_campus: boolean }[];
  tasks: { id: number; title: string; due_at: string }[];
  absences: { id: number; name: string; start_date: string; end_date: string; reason: string }[];
  accounts: { id: number; label: string; provider: string; color: string; shared: boolean; status: string; error: string; pending_bulk: number; has_token: boolean | null; enabled: boolean; last_sync_at: string | null }[];
};

function monday(date: Date): Date {
  const copy = new Date(date);
  copy.setHours(0, 0, 0, 0);
  const day = (copy.getDay() + 6) % 7;
  copy.setDate(copy.getDate() - day);
  return copy;
}

export function WeekPage() {
  const [params, setParams] = useSearchParams();
  const toasts = useToasts();
  const [start, setStart] = useState(() => monday(params.get("start") ? new Date(params.get("start") as string) : new Date()));
  const [week, setWeek] = useState<Week | null>(null);
  const [googleReady, setGoogleReady] = useState(false);
  const [creating, setCreating] = useState<string | null>(null);
  const openEvent = params.get("event");

  const load = useCallback(() => {
    api.get<Week>(`/api/v1/planning/week${qs({ start: start.toISOString() })}`).then(setWeek).catch(() => undefined);
    api.get<{ google_ready: boolean }>("/api/v1/calendar/accounts").then((res) => setGoogleReady(res.google_ready)).catch(() => undefined);
  }, [start]);

  useEffect(() => {
    load();
    const onEvent = (event: Event) => {
      const type = (event as CustomEvent<{ type: string }>).detail?.type || "";
      if (type === "calendar" || type.startsWith("task") || type === "events") load();
    };
    window.addEventListener("regie-event", onEvent);
    return () => window.removeEventListener("regie-event", onEvent);
  }, [load]);

  useEffect(() => {
    const flag = params.get("google");
    if (flag === "ok") toasts.push({ text: "Agenda Google connecté. Première synchro en cours.", tone: "ok" });
    if (flag === "refused") toasts.push({ text: "Connexion Google refusée.", tone: "warn" });
  }, [params, toasts]);

  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => { const d = new Date(start); d.setDate(d.getDate() + i); return d; }), [start]);
  const today = new Date().toDateString();
  const dayKey = (iso: string) => new Date(iso).toDateString();

  async function connectGoogle(shared: boolean) {
    try {
      const res = await api.get<{ url: string }>(`/api/v1/calendar/google/authorize${qs({ shared: shared ? 1 : 0 })}`);
      window.location.href = res.url;
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "Google non configuré", tone: "warn" });
    }
  }

  return (
    <Page
      title="Semaine"
      subtitle={`${days[0].toLocaleDateString("fr-FR", { day: "numeric", month: "long" })} → ${days[6].toLocaleDateString("fr-FR", { day: "numeric", month: "long" })}`}
      wide
      actions={
        <>
          <Button icon="arrow-left" onClick={() => setStart((s) => { const d = new Date(s); d.setDate(d.getDate() - 7); return d; })} aria-label="Semaine précédente" />
          <Button onClick={() => setStart(monday(new Date()))}>Aujourd’hui</Button>
          <Button icon="arrow-right" onClick={() => setStart((s) => { const d = new Date(s); d.setDate(d.getDate() + 7); return d; })} aria-label="Semaine suivante" />
          <Link to="/planning" className="btn btn-ghost">Tâches</Link>
        </>
      }
    >
      <div className="week">
        {days.map((day) => {
          const key = day.toDateString();
          const rdv = (week?.appointments || []).filter((a) => dayKey(a.starts_at) === key);
          const events = (week?.events || []).filter((e) => dayKey(e.starts_at) === key);
          const tasks = (week?.tasks || []).filter((t) => dayKey(t.due_at) === key);
          const absences = (week?.absences || []).filter((a) => new Date(a.start_date) <= day && new Date(a.end_date) >= day);
          return (
            <div key={key} className={`day${key === today ? " is-today" : ""}`}>
              <div className="day-head"><span>{day.toLocaleDateString("fr-FR", { weekday: "short" })}</span><span>{day.getDate()}</span></div>
              {absences.map((a) => <div key={`abs-${a.id}`} className="slot is-absence"><span className="t">{a.name} absent·e</span><span className="s">{a.reason}</span></div>)}
              {rdv.map((a) => (
                <button key={a.id} type="button" className="slot" style={{ borderLeftColor: a.account_color || undefined }} onClick={() => setParams({ event: String(a.id) })}>
                  <span className="t">{a.all_day ? "" : new Date(a.starts_at).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })} {a.title}</span>
                  <span className="s">{a.account_label}{a.location ? ` · ${a.location}` : ""}</span>
                </button>
              ))}
              {events.map((e) => (
                <Link key={e.id} to={`/events/${e.id}`} className="slot is-event">
                  <span className="t">{new Date(e.starts_at).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })} {e.title}</span>
                  <span className="s">{e.venue_name}{e.coverage.length ? ` · ${e.coverage.map((c) => c.kind).join(", ")}` : e.is_radio_campus ? " · Radio Campus" : ""}</span>
                </Link>
              ))}
              {tasks.map((t) => <Link key={`task-${t.id}`} to={`/planning?task=${t.id}`} className="slot is-task"><span className="t">☐ {t.title}</span></Link>)}
              <button type="button" className="top-link small" onClick={() => setCreating(day.toISOString())}>+ rendez-vous</button>
            </div>
          );
        })}
      </div>
      <Card title="Agendas" icon="calendar" actions={<><Button onClick={() => void connectGoogle(false)} disabled={!googleReady} title={googleReady ? "" : "GOOGLE_CLIENT_ID / SECRET manquants"}>Connecter mon Google</Button><Button onClick={() => void connectGoogle(true)} disabled={!googleReady}>Connecter l’agenda commun</Button><AddIcs onDone={load} /></>}>
        {week?.accounts.length ? (
          <ul className="rows">
            {week.accounts.map((acc) => (
              <li key={acc.id}>
                <span className="avatar" style={{ background: acc.color }} />
                <span>{acc.label}</span>
                <Pill tone="muted">{acc.provider}{acc.shared ? " · commun" : ""}</Pill>
                <Pill tone={acc.status === "ok" ? "ok" : acc.status === "error" || acc.status === "needs_confirmation" ? "warn" : "muted"}>{acc.status}</Pill>
                {acc.error ? <span className="small muted">{acc.error}</span> : null}
                <span className="spacer" />
                {acc.pending_bulk ? <Button variant="primary" onClick={() => void api.post(`/api/v1/calendar/accounts/${acc.id}/confirm-bulk`).then(() => toasts.push({ text: `${acc.pending_bulk} modifications envoyées à Google`, tone: "ok" }))}>Confirmer {acc.pending_bulk} envois</Button> : null}
                <Button variant="icon" icon="refresh" aria-label="Synchroniser" onClick={() => void api.post(`/api/v1/calendar/accounts/${acc.id}/sync`)} />
                <Button variant="icon" icon="trash" aria-label="Retirer" onClick={() => window.confirm("Retirer cet agenda de Régie ? (rien n’est supprimé chez Google)") && void api.delete(`/api/v1/calendar/accounts/${acc.id}`).then(load)} />
              </li>
            ))}
          </ul>
        ) : (
          <Empty text="Aucun agenda connecté. Connecte Google (bidirectionnel) ou ajoute un ICS (lecture seule)." icon="calendar" />
        )}
      </Card>
      <Sheet open={Boolean(openEvent)} title="Rendez-vous" onClose={() => setParams({})}>
        {openEvent ? <AppointmentDrawer id={Number(openEvent)} onChange={load} onClose={() => setParams({})} /> : null}
      </Sheet>
      <Sheet open={Boolean(creating)} title="Nouveau rendez-vous" onClose={() => setCreating(null)}>
        {creating ? <AppointmentForm day={creating} accounts={(week?.accounts || []).filter((a) => a.provider === "google" && a.enabled)} onDone={() => { setCreating(null); load(); }} /> : null}
      </Sheet>
    </Page>
  );
}

function AddIcs({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [shared, setShared] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Ajouter un ICS</Button>
      <Sheet open={open} title="Agenda ICS (lecture seule)" onClose={() => setOpen(false)}>
        <div className="grid">
          <Field label="URL privée .ics"><input className="field" value={url} onChange={(ev) => setUrl(ev.target.value)} placeholder="https://calendar.google.com/calendar/ical/…/basic.ics" /></Field>
          <Field label="Libellé"><input className="field" value={label} onChange={(ev) => setLabel(ev.target.value)} placeholder="Volontaire musique" /></Field>
          <label className="fld"><span className="fld-label">Partagé avec toute l’équipe</span><input type="checkbox" checked={shared} onChange={(ev) => setShared(ev.target.checked)} /></label>
          <div className="form-foot"><Button variant="primary" disabled={!url.startsWith("http")} onClick={() => void api.post("/api/v1/calendar/accounts/ics", { url, label, shared }).then(() => { setOpen(false); setUrl(""); onDone(); })}>Ajouter</Button></div>
        </div>
      </Sheet>
    </>
  );
}

function AppointmentForm({ day, accounts, onDone }: { day: string; accounts: Week["accounts"]; onDone: () => void }) {
  const toasts = useToasts();
  const base = new Date(day);
  const pad = (n: number) => String(n).padStart(2, "0");
  const local = (h: number) => `${base.getFullYear()}-${pad(base.getMonth() + 1)}-${pad(base.getDate())}T${pad(h)}:00`;
  const [title, setTitle] = useState("");
  const [starts, setStarts] = useState(local(10));
  const [ends, setEnds] = useState(local(11));
  const [location, setLocation] = useState("");
  const [account, setAccount] = useState<number>(accounts[0]?.id || 0);
  async function submit() {
    try {
      await api.post("/api/v1/calendar/events", { account_id: account, title, starts_at: new Date(starts).toISOString(), ends_at: new Date(ends).toISOString(), location });
      toasts.push({ text: "Rendez-vous créé, envoi vers Google dans la minute", tone: "ok" });
      onDone();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  if (!accounts.length) return <Empty text="Aucun agenda Google connecté : un ICS est en lecture seule." icon="calendar" />;
  return (
    <div className="grid">
      <Field label="Agenda"><select className="field" value={account} onChange={(ev) => setAccount(Number(ev.target.value))}>{accounts.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}</select></Field>
      <Field label="Titre"><input className="field" autoFocus value={title} onChange={(ev) => setTitle(ev.target.value)} /></Field>
      <div className="form-row">
        <Field label="Début"><input className="field" type="datetime-local" value={starts} onChange={(ev) => setStarts(ev.target.value)} /></Field>
        <Field label="Fin"><input className="field" type="datetime-local" value={ends} onChange={(ev) => setEnds(ev.target.value)} /></Field>
      </div>
      <Field label="Lieu"><input className="field" value={location} onChange={(ev) => setLocation(ev.target.value)} /></Field>
      <div className="form-foot"><Button variant="primary" disabled={!title.trim()} onClick={() => void submit()}>Créer</Button></div>
    </div>
  );
}

function AppointmentDrawer({ id, onChange, onClose }: { id: number; onChange: () => void; onClose: () => void }) {
  const [data, setData] = useState<{ event: Appointment & { description?: string }; history: { at: string; origin: string; title: string; starts_at: string }[] } | null>(null);
  const toasts = useToasts();
  useEffect(() => {
    api.get<{ event: Appointment & { description?: string }; history: { at: string; origin: string; title: string; starts_at: string }[] }>(`/api/v1/calendar/events/${id}`).then(setData).catch(() => setData(null));
  }, [id]);
  if (!data) return <Empty text="Rendez-vous introuvable." />;
  const event = data.event;
  async function patch(body: Record<string, unknown>) {
    try {
      const res = await api.patch<{ event: Appointment & { description?: string } }>(`/api/v1/calendar/events/${id}`, body);
      setData((prev) => (prev ? { ...prev, event: res.event } : prev));
      onChange();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  async function remove() {
    const res = await api.delete<{ action: { id: number } }>(`/api/v1/calendar/events/${id}`);
    toasts.push({ text: "Rendez-vous supprimé", tone: "ok", action: { label: "Annuler", onClick: () => api.post(`/api/v1/actions/${res.action.id}/undo`).then(onChange) } });
    onChange();
    onClose();
  }
  return (
    <div className="grid">
      <Field label="Titre"><input className="field" defaultValue={event.title} onBlur={(ev) => ev.target.value !== event.title && void patch({ title: ev.target.value })} /></Field>
      <div className="form-row">
        <Field label="Début"><input className="field" type="datetime-local" defaultValue={toLocal(event.starts_at)} onBlur={(ev) => void patch({ starts_at: new Date(ev.target.value).toISOString() })} /></Field>
        <Field label="Fin"><input className="field" type="datetime-local" defaultValue={toLocal(event.ends_at)} onBlur={(ev) => void patch({ ends_at: new Date(ev.target.value).toISOString() })} /></Field>
      </div>
      <Field label="Lieu"><input className="field" defaultValue={event.location || ""} onBlur={(ev) => ev.target.value !== (event.location || "") && void patch({ location: ev.target.value })} /></Field>
      <p className="small muted">{event.account_label}{event.html_link ? <> · <a href={event.html_link} target="_blank" rel="noreferrer">ouvrir dans Google</a></> : null}</p>
      {data.history.length ? (
        <Field label="Versions précédentes (conflits conservés)">
          <ul className="rows">{data.history.slice().reverse().map((h, i) => <li key={i}><span className="when">{h.at.slice(0, 16).replace("T", " ")}</span><Pill tone="muted">{h.origin}</Pill><span>{h.title}</span></li>)}</ul>
        </Field>
      ) : null}
      <div className="form-foot"><Button variant="danger" icon="trash" onClick={() => void remove()}>Supprimer</Button></div>
    </div>
  );
}

function toLocal(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
