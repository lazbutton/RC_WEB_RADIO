import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../api/client";
import { Button } from "../../components/Button";
import { Checkbox } from "../../components/Checkbox";
import { Card, Empty, Field, Page, Pill, fmtDuration } from "../../components/Page";
import { useToasts } from "../../components/Toast";
import type { Episode, Podcast, Show } from "./ShowsPage";

type Segment = { id: number; position: number; title: string; kind: string; start_s: number; end_s: number; phrase_in: string; phrase_out: string; speaker: string; coupures: number[][]; handles: { in: number; out: number } | null; valid: boolean; confidence: number; notes: string };
type Word = { text: string; start: number; end: number };
type Transcript = { words: Word[]; gaps: { start: number; end: number }[] };
type View = { episode: Episode & { waveform: number[] }; show: Show | null; segments: Segment[]; podcasts: Podcast[]; has_transcript: boolean };

export function EpisodePage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toasts = useToasts();
  const [view, setView] = useState<View | null>(null);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [draft, setDraft] = useState<Partial<Segment>>({});
  const [cursor, setCursor] = useState<number>(0);
  const audioRef = useRef<HTMLAudioElement>(null);
  const [suggestions, setSuggestions] = useState<Partial<Segment>[]>([]);

  const load = useCallback(() => {
    api.get<View>(`/api/v1/shows/episodes/${id}`).then((res) => {
      setView(res);
      if (res.has_transcript) api.get<Transcript>(`/api/v1/shows/episodes/${id}/transcript`).then(setTranscript).catch(() => setTranscript(null));
    }).catch(() => setView(null));
  }, [id]);
  useEffect(() => {
    load();
    const onEvent = (event: Event) => {
      const type = (event as CustomEvent<{ type: string; data?: { id?: number } }>).detail;
      if ((type?.type === "episode" && String(type.data?.id) === id) || type?.type === "podcast" || type?.type === "job") load();
    };
    window.addEventListener("regie-event", onEvent);
    return () => window.removeEventListener("regie-event", onEvent);
  }, [load, id]);

  const duration = view?.episode.duration_s || 0;
  const current = useMemo(() => view?.segments.find((s) => s.id === selected) || null, [view, selected]);
  useEffect(() => {
    setDraft(current ? { ...current } : {});
  }, [current]);

  function seek(t: number) {
    setCursor(t);
    if (audioRef.current) audioRef.current.currentTime = t;
  }

  async function saveDraft() {
    if (!view) return;
    const body = { title: draft.title || "", kind: draft.kind || "itw", start_s: Number(draft.start_s), end_s: Number(draft.end_s), phrase_in: draft.phrase_in || "", phrase_out: draft.phrase_out || "", speaker: draft.speaker || "", coupures: draft.coupures || [], notes: draft.notes || "", handles: draft.handles || undefined };
    try {
      if (current) await api.patch(`/api/v1/shows/segments/${current.id}`, body);
      else {
        const res = await api.post<{ segment: Segment }>(`/api/v1/shows/episodes/${id}/segments`, body);
        setSelected(res.segment.id);
      }
      toasts.push({ text: "Séquence enregistrée · bornes.json écrit", tone: "ok" });
      load();
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn", duration: 7000 });
    }
  }

  async function validate(seg: Segment, valid: boolean) {
    const res = await api.post<{ action: { id: number } }>("/api/v1/shows/segments/validate", { ids: [seg.id], valid });
    toasts.push({ text: valid ? "Séquence validée" : "Validation retirée", tone: "ok", action: { label: "Annuler", onClick: () => api.post(`/api/v1/actions/${res.action.id}/undo`).then(load) } });
    load();
  }

  async function makePodcast(seg: Segment) {
    try {
      const res = await api.post<{ podcast: Podcast }>("/api/v1/shows/podcasts", { segment_id: seg.id, title: seg.title || view?.episode.title });
      navigate(`/shows/podcasts/${res.podcast.id}`);
    } catch (err) {
      toasts.push({ text: err instanceof Error ? err.message : "erreur", tone: "warn" });
    }
  }

  function wordAt(t: number, which: "phrase_in" | "phrase_out") {
    if (!transcript) return;
    const index = transcript.words.findIndex((w) => w.start >= t);
    const slice = which === "phrase_in" ? transcript.words.slice(index, index + 5) : transcript.words.slice(Math.max(0, index - 4), index + 1);
    setDraft((prev) => ({ ...prev, [which]: slice.map((w) => w.text).join(" "), [which === "phrase_in" ? "start_s" : "end_s"]: which === "phrase_in" ? slice[0]?.start ?? t : slice[slice.length - 1]?.end ?? t }));
  }

  if (!view) return <Page title="Épisode"><Empty text="Épisode introuvable." /></Page>;
  const ep = view.episode;
  const audioUrl = `/api/v1/files/raw?path=${encodeURIComponent(ep.master_path)}`;

  return (
    <Page
      title={ep.title}
      subtitle={<span className="chips"><Link to="/shows">{view.show?.name}</Link><span>{ep.aired_on}</span><span>{fmtDuration(duration)}</span><Pill>{ep.status}</Pill><Pill tone={ep.transcript_status === "done" ? "ok" : ep.transcript_status === "failed" ? "warn" : "muted"}>transcription : {ep.transcript_status}</Pill>{ep.bornes_path ? <span className="small muted">bornes.json à jour</span> : null}</span>}
      wide
      actions={
        <>
          {ep.transcript_status !== "done" ? <Button icon="sparkle" onClick={() => void api.post(`/api/v1/shows/episodes/${id}/transcribe`).then(() => toasts.push({ text: "Transcription demandée : le worker Mac (mlx-whisper) la prendra", tone: "ok", duration: 7000 }))} disabled={ep.transcript_status === "running" || ep.transcript_status === "queued"}>Transcrire</Button> : null}
          <Button icon="refresh" onClick={() => void api.post(`/api/v1/shows/episodes/${id}/analyze`)}>Ré-analyser</Button>
          <Button icon="sparkle" onClick={() => void api.get<{ suggestions: Partial<Segment>[] }>(`/api/v1/shows/episodes/${id}/suggest`).then((r) => { setSuggestions(r.suggestions); if (!r.suggestions.length) toasts.push({ text: "Aucun bloc de parole détecté (transcription manquante ?)", tone: "neutral" }); })} disabled={!view.has_transcript}>Proposer des séquences</Button>
          <Button variant="primary" icon="plus" onClick={() => { setSelected(null); setDraft({ start_s: Math.max(0, cursor), end_s: Math.min(duration, cursor + 60), kind: "itw" }); }}>Nouvelle séquence</Button>
        </>
      }
    >
      {ep.transcript_error ? <p className="is-overdue-text small">Transcription : {ep.transcript_error}</p> : null}
      <Waveform peaks={ep.waveform || []} duration={duration} segments={view.segments} gaps={transcript?.gaps || []} cursor={cursor} draft={draft} onSeek={seek} />
      <audio ref={audioRef} controls preload="none" src={audioUrl} style={{ width: "100%" }} onTimeUpdate={(ev) => setCursor((ev.target as HTMLAudioElement).currentTime)} />
      <div className="split">
        <div className="grid">
          <Card title={`Séquences (${view.segments.length})`} icon="scissors">
            {view.segments.length ? (
              view.segments.map((seg) => (
                <div key={seg.id} className={`seg-row${seg.valid ? " is-valid" : ""}${selected === seg.id ? " is-on" : ""}`} onClick={() => setSelected(seg.id)}>
                  <div>
                    <div><strong>{seg.title || `${seg.kind} #${seg.position + 1}`}</strong> <Pill tone="muted">{seg.kind}</Pill> {seg.speaker ? <span className="small muted">{seg.speaker}</span> : null}</div>
                    <div className="times">{fmtDuration(seg.start_s)} → {fmtDuration(seg.end_s)} · {fmtDuration(seg.end_s - seg.start_s)}{seg.coupures?.length ? ` · ${seg.coupures.length} coupure(s)` : ""}</div>
                    {seg.phrase_in ? <div className="small muted">« {seg.phrase_in} » … « {seg.phrase_out} »</div> : null}
                  </div>
                  <div className="toolbar">
                    <Button variant="icon" icon="play" aria-label="Écouter" onClick={(ev) => { ev.stopPropagation(); seek(seg.start_s); void audioRef.current?.play(); }} />
                    <Checkbox checked={seg.valid} onToggle={() => void validate(seg, !seg.valid)} label="valide" />
                    <Button variant="quiet" icon="headphones" disabled={!seg.valid} onClick={(ev) => { ev.stopPropagation(); void makePodcast(seg); }}>Podcast</Button>
                  </div>
                </div>
              ))
            ) : (
              <Empty text="Aucune séquence. Clique sur la transcription pour poser des bornes, ou « Proposer des séquences »." icon="scissors" />
            )}
            {suggestions.length ? (
              <div className="grid">
                <p className="small muted">Propositions (blocs de parole entre les plages musicales) :</p>
                {suggestions.map((sug, i) => <Button key={i} onClick={() => { setSelected(null); setDraft(sug); setSuggestions([]); }}>{fmtDuration(sug.start_s || 0)} → {fmtDuration(sug.end_s || 0)} · « {(sug.phrase_in || "").slice(0, 40)} »</Button>)}
              </div>
            ) : null}
          </Card>
          <Card title={current ? "Modifier la séquence" : "Nouvelle séquence"} icon="scissors">
            <Field label="Titre"><input className="field" value={draft.title || ""} onChange={(ev) => setDraft((p) => ({ ...p, title: ev.target.value }))} /></Field>
            <div className="form-row">
              <Field label="Type"><select className="field" value={draft.kind || "itw"} onChange={(ev) => setDraft((p) => ({ ...p, kind: ev.target.value }))}>{["itw", "chro", "live", "debat", "plateau", "autre"].map((k) => <option key={k}>{k}</option>)}</select></Field>
              <Field label="Locuteur"><input className="field" value={draft.speaker || ""} onChange={(ev) => setDraft((p) => ({ ...p, speaker: ev.target.value }))} /></Field>
            </div>
            <div className="form-row">
              <Field label="Début (s)"><input className="field" type="number" step="0.1" value={draft.start_s ?? ""} onChange={(ev) => setDraft((p) => ({ ...p, start_s: Number(ev.target.value) }))} /></Field>
              <Field label="Fin (s)"><input className="field" type="number" step="0.1" value={draft.end_s ?? ""} onChange={(ev) => setDraft((p) => ({ ...p, end_s: Number(ev.target.value) }))} /></Field>
              <Button onClick={() => setDraft((p) => ({ ...p, start_s: Number(cursor.toFixed(2)) }))}>Début = curseur</Button>
              <Button onClick={() => setDraft((p) => ({ ...p, end_s: Number(cursor.toFixed(2)) }))}>Fin = curseur</Button>
            </div>
            <Field label="Phrase d’entrée" hint="doit être unique dans la transcription"><input className="field" value={draft.phrase_in || ""} onChange={(ev) => setDraft((p) => ({ ...p, phrase_in: ev.target.value }))} /></Field>
            <Field label="Phrase de sortie"><input className="field" value={draft.phrase_out || ""} onChange={(ev) => setDraft((p) => ({ ...p, phrase_out: ev.target.value }))} /></Field>
            <Field label="Coupures (musique à retirer)" hint="une par ligne : début fin, en secondes">
              <textarea className="field" value={(draft.coupures || []).map((c) => `${c[0]} ${c[1]}`).join("\n")} onChange={(ev) => setDraft((p) => ({ ...p, coupures: ev.target.value.split("\n").map((l) => l.trim().split(/[\s,;]+/).map(Number)).filter((c) => c.length === 2 && !Number.isNaN(c[0]) && !Number.isNaN(c[1])) }))} />
              <Button onClick={() => setDraft((p) => ({ ...p, coupures: [...(p.coupures || []), [Number(cursor.toFixed(1)), Number((cursor + 30).toFixed(1))]] }))}>Ajouter une coupure au curseur</Button>
            </Field>
            <Field label="Notes"><input className="field" value={draft.notes || ""} onChange={(ev) => setDraft((p) => ({ ...p, notes: ev.target.value }))} /></Field>
            <div className="form-foot">
              {current ? <Button variant="danger" icon="trash" onClick={() => void api.delete(`/api/v1/shows/segments/${current.id}`).then(() => { setSelected(null); load(); })}>Supprimer</Button> : null}
              <Button variant="primary" onClick={() => void saveDraft()} disabled={draft.start_s === undefined || draft.end_s === undefined}>Enregistrer</Button>
            </div>
          </Card>
        </div>
        <div className="grid">
          <Card title="Transcription" icon="list" actions={transcript ? <span className="small muted">{transcript.words.length} mots · {transcript.gaps.length} plages sans parole</span> : undefined}>
            {transcript ? (
              <div className="transcript">
                {transcript.words.map((word, i) => {
                  const inDraft = draft.start_s !== undefined && draft.end_s !== undefined && word.start >= Number(draft.start_s) && word.end <= Number(draft.end_s);
                  const gapBefore = transcript.gaps.some((g) => Math.abs(g.end - word.start) < 0.05);
                  return (
                    <span key={i} className={`word${inDraft ? " is-in" : ""}${gapBefore ? " is-gap" : ""}`} title={`${fmtDuration(word.start)} · clic = début, ⇧clic = fin, ⌥clic = écouter`} onClick={(ev) => { if (ev.altKey) { seek(word.start); void audioRef.current?.play(); } else wordAt(word.start, ev.shiftKey ? "phrase_out" : "phrase_in"); }}>
                      {word.text}{" "}
                    </span>
                  );
                })}
              </div>
            ) : (
              <Empty text={ep.transcript_status === "done" ? "Transcription illisible." : "Pas de transcription. Lance « Transcrire » : le worker Mac la produira."} icon="list" />
            )}
            <p className="small muted">Clic sur un mot = phrase d’entrée et début · ⇧ clic = phrase de sortie et fin · ⌥ clic = écouter ici. Les ♪ marquent les plages sans parole (musique probable).</p>
          </Card>
          <Card title="Podcasts issus de cet épisode" icon="headphones">
            {view.podcasts.length ? <ul className="rows">{view.podcasts.map((p) => <li key={p.id}><Link to={`/shows/podcasts/${p.id}`}>{p.title}</Link><Pill tone={p.status === "published" ? "ok" : "muted"}>{p.status}</Pill><Pill tone={p.rights_status === "ok" ? "ok" : "warn"}>droits {p.rights_status}</Pill></li>)}</ul> : <Empty text="Aucun podcast encore." icon="headphones" />}
          </Card>
        </div>
      </div>
    </Page>
  );
}

function Waveform({ peaks, duration, segments, gaps, cursor, draft, onSeek }: { peaks: number[]; duration: number; segments: Segment[]; gaps: { start: number; end: number }[]; cursor: number; draft: Partial<Segment>; onSeek: (t: number) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const width = (el.width = el.clientWidth * 2);
    const height = (el.height = 240);
    const ctx = el.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, width, height);
    const x = (t: number) => (duration ? (t / duration) * width : 0);
    for (const gap of gaps) {
      ctx.fillStyle = "rgba(0,0,0,0.05)";
      ctx.fillRect(x(gap.start), 0, x(gap.end) - x(gap.start), height);
    }
    for (const seg of segments) {
      ctx.fillStyle = seg.valid ? "rgba(31,157,85,0.18)" : "rgba(17,17,17,0.10)";
      ctx.fillRect(x(seg.start_s), 0, x(seg.end_s) - x(seg.start_s), height);
      for (const cut of seg.coupures || []) {
        ctx.fillStyle = "rgba(179,38,30,0.18)";
        ctx.fillRect(x(cut[0]), 0, x(cut[1]) - x(cut[0]), height);
      }
    }
    if (draft.start_s !== undefined && draft.end_s !== undefined) {
      ctx.strokeStyle = "#111";
      ctx.lineWidth = 2;
      ctx.strokeRect(x(Number(draft.start_s)), 2, x(Number(draft.end_s)) - x(Number(draft.start_s)), height - 4);
    }
    ctx.fillStyle = "#111";
    const n = peaks.length || 1;
    const bar = width / n;
    peaks.forEach((peak, i) => {
      const h = Math.max(2, peak * (height - 20));
      ctx.fillRect(i * bar, (height - h) / 2, Math.max(1, bar - 1), h);
    });
    ctx.fillStyle = "#b3261e";
    ctx.fillRect(x(cursor) - 1, 0, 2, height);
  }, [peaks, duration, segments, gaps, cursor, draft]);
  return <canvas ref={canvas} className="wave" onClick={(ev) => { const rect = ev.currentTarget.getBoundingClientRect(); onSeek(((ev.clientX - rect.left) / rect.width) * duration); }} />;
}
