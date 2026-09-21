import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../api/client";
import { Button } from "../../components/Button";
import { Card, Empty, Field, Page, Pill, Tabs, fmtDuration } from "../../components/Page";
import { Sheet } from "../../components/Sheet";
import { useToasts } from "../../components/Toast";
import { Promotions } from "../events/EventsPage";

export type Show = { id: number; slug: string; name: string; description: string; folder: string; schedule: string; duration_min: number; color: string; rss_enabled: boolean; active: boolean; episodes?: number; handles: { in: number; out: number } };
export type Episode = { id: number; show_id: number; title: string; aired_on: string | null; master_path: string; duration_s: number; transcript_status: string; transcript_error: string; bornes_path: string; status: string; notes: string; show?: Show | null };
export type Podcast = { id: number; show_id: number; episode_id: number | null; segment_id: number | null; slug: string; title: string; description: string; file_path: string; duration_s: number; loudness_i: number | null; rights_status: string; rights_notes: string; status: string; export_error: string; published_at: string | null; publishable?: boolean; blocked_reason?: string };
const EPISODE_STATUS: Record<string, string> = { detected: "détecté", transcribed: "transcrit", bounded: "borné", edited: "monté", published: "publié" };

export function ShowsPage() {
  const toasts = useToasts();
  const navigate = useNavigate();
  const [shows, setShows] = useState<Show[]>([]);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [podcasts, setPodcasts] = useState<Podcast[]>([]);
  const [media, setMedia] = useState(true);
  const [tab, setTab] = useState<"episodes" | "podcasts" | "shows">("episodes");
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    api.get<{ shows: Show[]; media_available: boolean }>("/api/v1/shows").then((res) => { setShows(res.shows); setMedia(res.media_available); }).catch(() => undefined);
    api.get<{ episodes: Episode[] }>("/api/v1/shows/episodes?limit=60").then((res) => setEpisodes(res.episodes)).catch(() => undefined);
    api.get<{ podcasts: Podcast[] }>("/api/v1/shows/podcasts").then((res) => setPodcasts(res.podcasts)).catch(() => undefined);
  }, []);
  useEffect(() => {
    load();
    const onEvent = (event: Event) => {
      const type = (event as CustomEvent<{ type: string }>).detail?.type || "";
      if (type === "episode" || type === "podcast" || type === "job") load();
    };
    window.addEventListener("regie-event", onEvent);
    return () => window.removeEventListener("regie-event", onEvent);
  }, [load]);

  return (
    <Page
      title="Émissions"
      subtitle={media ? `${shows.length} émissions · ${episodes.length} épisodes récents · ${podcasts.length} podcasts` : "Médiathèque non montée : les masters ne peuvent pas être lus."}
      wide
      actions={<><Button icon="refresh" disabled={!media} onClick={() => void api.post("/api/v1/shows/detect").then(() => toasts.push({ text: "Détection des masters lancée", tone: "ok" }))}>Détecter les masters</Button><Button variant="primary" icon="plus" onClick={() => setCreating(true)}>Nouvelle émission</Button></>}
    >
      <Tabs value={tab} onChange={setTab} items={[{ id: "episodes", label: "Épisodes", count: episodes.length }, { id: "podcasts", label: "Podcasts", count: podcasts.length }, { id: "shows", label: "Grille", count: shows.length }]} />
      {tab === "episodes" ? (
        episodes.length ? (
          <table className="table">
            <thead><tr><th>Date</th><th>Émission</th><th>Master</th><th>Durée</th><th>Transcription</th><th>État</th></tr></thead>
            <tbody>
              {episodes.map((ep) => (
                <tr key={ep.id} className="is-click" onClick={() => navigate(`/shows/episodes/${ep.id}`)}>
                  <td>{ep.aired_on || "—"}</td>
                  <td>{ep.show?.name || ep.show_id}</td>
                  <td className="muted small">{ep.master_path.split("/").pop()}</td>
                  <td>{fmtDuration(ep.duration_s)}</td>
                  <td><Pill tone={ep.transcript_status === "done" ? "ok" : ep.transcript_status === "failed" ? "warn" : ep.transcript_status === "none" ? "muted" : "live"}>{ep.transcript_status}</Pill></td>
                  <td><Pill>{EPISODE_STATUS[ep.status] || ep.status}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty text="Aucun épisode. Dépose un master dans 40-emissions/{Émission}/Entière/ puis « Détecter »." icon="disc" />
        )
      ) : null}
      {tab === "podcasts" ? (
        podcasts.length ? (
          <table className="table">
            <thead><tr><th>Titre</th><th>Émission</th><th>Durée</th><th>Droits</th><th>État</th></tr></thead>
            <tbody>
              {podcasts.map((p) => (
                <tr key={p.id} className="is-click" onClick={() => navigate(`/shows/podcasts/${p.id}`)}>
                  <td>{p.title}</td>
                  <td>{shows.find((s) => s.id === p.show_id)?.name}</td>
                  <td>{fmtDuration(p.duration_s)}</td>
                  <td><Pill tone={p.rights_status === "ok" ? "ok" : p.rights_status === "blocked" ? "warn" : "muted"}>{p.rights_status}</Pill></td>
                  <td><Pill tone={p.status === "published" ? "ok" : p.status === "failed" ? "warn" : "muted"}>{p.status}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty text="Aucun podcast. Ils naissent d’une séquence validée dans un épisode." icon="headphones" />
        )
      ) : null}
      {tab === "shows" ? (
        <div className="grid cols-3">
          {shows.map((show) => (
            <Card key={show.id} title={show.name} actions={<Pill tone={show.active ? "ok" : "muted"}>{show.active ? "active" : "arrêtée"}</Pill>}>
              <p className="muted small" style={{ margin: 0 }}>{show.schedule || "horaire à définir"} · {show.duration_min} min · dossier {show.folder}</p>
              {show.description ? <p style={{ margin: 0 }}>{show.description}</p> : null}
              <p className="small muted" style={{ margin: 0 }}>{show.episodes || 0} épisode(s) · marges {show.handles?.in}s / {show.handles?.out}s · RSS {show.rss_enabled ? "activé" : "désactivé"}</p>
              <ShowEdit show={show} onDone={load} />
            </Card>
          ))}
          {!shows.length ? <Empty text="Aucune émission déclarée." icon="radio" /> : null}
        </div>
      ) : null}
      <Sheet open={creating} title="Nouvelle émission" onClose={() => setCreating(false)}>
        <ShowForm onDone={() => { setCreating(false); load(); }} />
      </Sheet>
    </Page>
  );
}

function ShowForm({ show, onDone }: { show?: Show; onDone: () => void }) {
  const toasts = useToasts();
  const [data, setData] = useState<Record<string, string>>({ name: show?.name || "", schedule: show?.schedule || "", description: show?.description || "", folder: show?.folder || "", duration_min: String(show?.duration_min || 60), handles_in: String(show?.handles?.in ?? 0.5), handles_out: String(show?.handles?.out ?? 0.8) });
  const set = (key: string) => (ev: { target: { value: string } }) => setData((p) => ({ ...p, [key]: ev.target.value }));
  async function submit() {
    const payload = { name: data.name, schedule: data.schedule, description: data.description, folder: data.folder || data.name, duration_min: Number(data.duration_min) || 60, handles: { in: Number(data.handles_in) || 0, out: Number(data.handles_out) || 0 } };
    try {
      if (show) await api.patch(`/api/v1/shows/${show.id}`, { data: payload });
      else await api.post("/api/v1/shows", { data: payload });
      onDone();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  return (
    <div className="grid">
      <Field label="Nom"><input className="field" value={data.name} onChange={set("name")} /></Field>
      <div className="form-row">
        <Field label="Créneau"><input className="field" value={data.schedule} onChange={set("schedule")} placeholder="Vendredi 18h" /></Field>
        <Field label="Durée (min)"><input className="field" type="number" value={data.duration_min} onChange={set("duration_min")} /></Field>
      </div>
      <Field label="Dossier NAS" hint="40-emissions/{dossier}/Entière/"><input className="field" value={data.folder} onChange={set("folder")} /></Field>
      <div className="form-row">
        <Field label="Marge entrée (s)"><input className="field" type="number" step="0.1" value={data.handles_in} onChange={set("handles_in")} /></Field>
        <Field label="Marge sortie (s)"><input className="field" type="number" step="0.1" value={data.handles_out} onChange={set("handles_out")} /></Field>
      </div>
      <Field label="Description"><textarea className="field" value={data.description} onChange={set("description")} /></Field>
      <div className="form-foot"><Button variant="primary" disabled={!data.name.trim()} onClick={() => void submit()}>{show ? "Enregistrer" : "Créer"}</Button></div>
    </div>
  );
}

function ShowEdit({ show, onDone }: { show: Show; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Modifier</Button>
      <Sheet open={open} title={show.name} onClose={() => setOpen(false)}><ShowForm show={show} onDone={() => { setOpen(false); onDone(); }} /></Sheet>
    </>
  );
}

export function PodcastPage() {
  const { id = "" } = useParams();
  const toasts = useToasts();
  const [data, setData] = useState<{ podcast: Podcast; publications: { id: number; target: string; status: string; url: string; error: string }[] } | null>(null);
  const [publishStatus, setPublishStatus] = useState<{ wordpress: { configured: boolean }; edge: { configured: boolean } } | null>(null);
  const load = useCallback(() => {
    api.get<{ podcast: Podcast; publications: { id: number; target: string; status: string; url: string; error: string }[] }>(`/api/v1/shows/podcasts/${id}`).then(setData).catch(() => setData(null));
    api.get<{ wordpress: { configured: boolean }; edge: { configured: boolean } }>("/api/v1/publish/status").then(setPublishStatus).catch(() => undefined);
  }, [id]);
  useEffect(() => {
    load();
    const onEvent = () => load();
    window.addEventListener("regie-event", onEvent);
    return () => window.removeEventListener("regie-event", onEvent);
  }, [load]);
  if (!data) return <Page title="Podcast"><Empty text="Podcast introuvable." /></Page>;
  const p = data.podcast;
  async function patch(body: Record<string, unknown>) {
    try {
      await api.patch(`/api/v1/shows/podcasts/${id}`, body);
      load();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }
  async function publish(wpStatus: string) {
    try {
      await api.post(`/api/v1/publish/podcasts/${id}`, { wp_status: wpStatus });
      toasts.push({ text: "Publication lancée", tone: "ok" });
      load();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn", duration: 8000 });
    }
  }
  return (
    <Page
      title={p.title}
      subtitle={<span className="chips"><Pill tone={p.status === "published" ? "ok" : p.status === "failed" ? "warn" : "muted"}>{p.status}</Pill>{p.duration_s ? <span>{fmtDuration(p.duration_s)}</span> : null}{p.loudness_i != null ? <span className="small muted">{p.loudness_i.toFixed(1)} LUFS</span> : null}{p.episode_id ? <Link to={`/shows/episodes/${p.episode_id}`} className="small">épisode source</Link> : null}</span>}
      actions={
        <>
          <Button icon="download" onClick={() => void api.post(`/api/v1/shows/podcasts/${id}/export`, { format: "mp3" }).then(() => toasts.push({ text: "Export ffmpeg lancé", tone: "ok" }))}>{p.file_path ? "Ré-exporter" : "Exporter (mp3)"}</Button>
          <Button variant="primary" icon="send" disabled={!p.publishable} title={p.blocked_reason || ""} onClick={() => void publish("draft")}>Publier (brouillon WordPress)</Button>
          <Button icon="send" disabled={!p.publishable} onClick={() => void publish("publish")}>Publier en ligne</Button>
        </>
      }
    >
      {!p.publishable ? <p className="muted small">Publication bloquée : {p.blocked_reason}</p> : null}
      {p.export_error ? <p className="is-overdue-text small">Export : {p.export_error}</p> : null}
      <div className="split">
        <div className="grid">
          <Card title="Fiche" icon="headphones">
            <Field label="Titre"><input className="field" defaultValue={p.title} onBlur={(ev) => ev.target.value !== p.title && void patch({ title: ev.target.value })} /></Field>
            <Field label="Description"><textarea className="field" defaultValue={p.description} onBlur={(ev) => ev.target.value !== p.description && void patch({ description: ev.target.value })} /></Field>
            {p.file_path ? <p className="small muted">Fichier : {p.file_path} · <a href={`/api/v1/files/raw?path=${encodeURIComponent(p.file_path)}`} target="_blank" rel="noreferrer">écouter</a></p> : null}
            {p.file_path ? <audio controls preload="none" src={`/api/v1/files/raw?path=${encodeURIComponent(p.file_path)}`} style={{ width: "100%" }} /> : null}
          </Card>
          <Card title="Droits (porte bloquante)" icon="shield">
            <div className="toolbar">
              {["unknown", "ok", "blocked"].map((s) => <Button key={s} variant={p.rights_status === s ? "primary" : "ghost"} onClick={() => void patch({ rights_status: s })}>{s === "unknown" ? "à vérifier" : s === "ok" ? "vérifiés" : "bloqués"}</Button>)}
            </div>
            <Field label="Notes de droits" hint="musique commerciale exclue via coupures, accord des invités…"><textarea className="field" defaultValue={p.rights_notes} onBlur={(ev) => ev.target.value !== p.rights_notes && void patch({ rights_notes: ev.target.value })} /></Field>
          </Card>
        </div>
        <div className="grid">
          <Card title="Publications" icon="send">
            {data.publications.length ? <ul className="rows">{data.publications.map((pub) => <li key={pub.id}><Pill tone={pub.status === "done" ? "ok" : pub.status === "failed" ? "warn" : "muted"}>{pub.target} · {pub.status}</Pill>{pub.url ? <a href={pub.url} target="_blank" rel="noreferrer" className="small">{pub.url}</a> : null}{pub.error ? <span className="small is-overdue-text">{pub.error}</span> : null}</li>)}</ul> : <Empty text="Pas encore publié." />}
            {publishStatus && !publishStatus.wordpress.configured ? <p className="small muted">WordPress non configuré (WORDPRESS_USER / APP_PASSWORD).</p> : null}
            {publishStatus && !publishStatus.edge.configured ? <p className="small muted">Bord public non configuré (EDGE_PROVIDER).</p> : null}
          </Card>
          <Card title="Valorisation" icon="star"><Promotions kind="podcast" id={id} /></Card>
        </div>
      </div>
    </Page>
  );
}

