"""Émissions et podcasts : grille, masters détectés sur le NAS, transcription, bornes, export, droits."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from regie.kernel import db
from regie.kernel.actions import ActionCtx, ActionSpec
from regie.kernel.core import Kernel
from regie.kernel.crud import Table
from regie.kernel.jobs import P_BACKGROUND, P_SCAN, P_USER, JobContext
from regie.kernel.registry import EntityKind
from regie.modules.shows import media

log = logging.getLogger("regie.shows")

SHOWS_DIR = "40-emissions"
MASTER_DIR = "Entière"
EXTRACT_DIR = "extraits"
SHOW_COLUMNS = {"slug": "text", "name": "text", "description": "text", "folder": "text", "schedule": "text", "duration_min": "int", "hosts": "array", "color": "text", "handles": "json", "rss_enabled": "bool", "cover_path": "text", "tags": "array", "active": "bool"}
EPISODE_COLUMNS = {"show_id": "int", "title": "text", "aired_on": "date", "master_path": "text", "duration_s": "float", "sample_rate": "int", "channels": "int", "loudness_i": "float", "waveform": "json", "transcript_path": "text", "transcript_status": "text", "transcript_error": "text", "bornes_path": "text", "status": "text", "notes": "text"}
SEGMENT_COLUMNS = {"episode_id": "int", "position": "int", "title": "text", "kind": "text", "start_s": "float", "end_s": "float", "phrase_in": "text", "phrase_out": "text", "speaker": "text", "coupures": "json", "handles": "json", "valid": "bool", "confidence": "float", "notes": "text"}
PODCAST_COLUMNS = {"show_id": "int", "episode_id": "int", "segment_id": "int", "slug": "text", "title": "text", "description": "text", "file_path": "text", "file_size": "int", "duration_s": "float", "loudness_i": "float", "cover_path": "text", "rights_status": "text", "rights_notes": "text", "status": "text", "export_error": "text", "published_at": "ts", "created_by": "uuid"}
MASTER_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<slug>[a-z0-9-]+)_entiere\.(?P<ext>mp3|wav|flac|m4a)$", re.I)


def slugify(text: str) -> str:
    import unicodedata

    base = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "sans-nom"


class ShowsService:
    name = "shows"

    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel
        self.shows = Table(kernel.dsn, kernel.org_id, "shows", SHOW_COLUMNS, order="name")
        self.episodes = Table(kernel.dsn, kernel.org_id, "episodes", EPISODE_COLUMNS, order="aired_on DESC NULLS LAST, id DESC")
        self.segments = Table(kernel.dsn, kernel.org_id, "segments", SEGMENT_COLUMNS, order="position, start_s")
        self.podcasts = Table(kernel.dsn, kernel.org_id, "podcasts", PODCAST_COLUMNS, order="created_at DESC")
        self._register()

    def _register(self) -> None:
        k = self.kernel
        k.registry.register(EntityKind(kind="show", module="shows", label="Émission", icon="radio", table="shows", fetch=self.shows.many, summarize=lambda r: {"title": r.get("name") or "", "subtitle": r.get("schedule") or ""}, url=lambda i: f"/shows/{i}"))
        k.registry.register(EntityKind(kind="episode", module="shows", label="Épisode", icon="disc", table="episodes", fetch=self.episodes.many, summarize=lambda r: {"title": r.get("title") or Path(r.get("master_path") or "").stem, "subtitle": str(r.get("aired_on") or ""), "status": r.get("status")}, url=lambda i: f"/shows/episodes/{i}"))
        k.registry.register(EntityKind(kind="segment", module="shows", label="Séquence", icon="scissors", table="segments", fetch=self.segments.many, summarize=lambda r: {"title": r.get("title") or f"{r.get('kind')} {r.get('start_s')}", "subtitle": f"{r.get('start_s')} → {r.get('end_s')}"}, url=lambda i: f"/shows/segments/{i}"))
        k.registry.register(EntityKind(kind="podcast", module="shows", label="Podcast", icon="headphones", table="podcasts", fetch=self.podcasts.many, summarize=lambda r: {"title": r.get("title") or "", "subtitle": r.get("status") or "", "status": r.get("status")}, url=lambda i: f"/shows/podcasts/{i}", actions=["shows.publish"]))
        k.actions.register(ActionSpec(kind="shows.validate_segment", module="shows", entity_kind="segment", label="Séquence validée", apply=self._validate_apply, revert=self._validate_revert, snapshot=lambda ids: {i: bool((self.segments.get(int(i)) or {}).get("valid")) for i in ids}, undoable=True))
        k.jobs.register("shows.detect", self.job_detect, "media")
        k.jobs.register("shows.analyze", self.job_analyze, "media")
        k.jobs.register("shows.transcribe", self.job_transcribe, "transcribe")
        k.jobs.register("shows.export", self.job_export, "media")
        k.scheduler.ensure("shows.detect", 15 * 60, priority=P_BACKGROUND, enabled=k.files.available)

    # --- émissions ------------------------------------------------------------------------------

    def create_show(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = dict(data)
        payload["slug"] = slugify(payload.get("slug") or payload.get("name") or "")
        payload.setdefault("folder", payload.get("name") or payload["slug"])
        row = self.shows.create(payload)
        self.kernel.search.index("show", row["id"], row["name"], row.get("schedule") or "", row.get("description") or "", url=f"/shows/{row['id']}")
        self.kernel.outbox.emit("show.created", {"id": row["id"]})
        return row

    def update_show(self, show_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        row = self.shows.update(show_id, {k: v for k, v in data.items() if k in SHOW_COLUMNS and k != "slug"})
        if row:
            self.kernel.search.index("show", row["id"], row["name"], row.get("schedule") or "", row.get("description") or "", url=f"/shows/{row['id']}")
            self.kernel.outbox.emit("show.updated", {"id": show_id})
        return row

    def show_by_slug(self, slug: str) -> dict[str, Any] | None:
        rows = self.shows.list("slug = %s", [slug], 1)
        return rows[0] if rows else None

    def show_by_folder(self, folder: str) -> dict[str, Any] | None:
        rows = self.shows.list("lower(folder) = lower(%s)", [folder], 1)
        return rows[0] if rows else None

    # --- détection des masters ------------------------------------------------------------------------

    def job_detect(self, ctx: JobContext) -> dict[str, Any]:
        files = self.kernel.files
        if not files.available:
            return {"skipped": True, "reason": "médiathèque absente"}
        root = files.resolve(SHOWS_DIR)
        if not root.is_dir():
            return {"skipped": True, "reason": f"{SHOWS_DIR} absent"}
        created = 0
        for show_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            master_dir = show_dir / MASTER_DIR
            if not master_dir.is_dir():
                continue
            show = self.show_by_folder(show_dir.name)
            for path in sorted(master_dir.iterdir()):
                if not path.is_file() or path.suffix.lower() not in {".mp3", ".wav", ".flac", ".m4a"} or path.name.startswith("."):
                    continue
                rel = files.rel(path)
                if self.episodes.list("master_path = %s", [rel], 1):
                    continue
                match = MASTER_RE.match(path.name)
                aired = match.group("date") if match else None
                if show is None:
                    show = self.create_show({"name": show_dir.name, "slug": (match.group("slug") if match else slugify(show_dir.name)), "folder": show_dir.name})
                title = f"{show['name']} · {aired}" if aired else f"{show['name']} · {path.stem}"
                episode = self.episodes.create({"show_id": show["id"], "title": title, "aired_on": aired, "master_path": rel, "status": "detected"})
                files.index_file(rel, {"role": "master", "episode_id": episode["id"]})
                self.kernel.links.link("episode", episode["id"], "file", rel, role="master")
                self._index_episode(episode, show)
                self.kernel.jobs.submit("shows.analyze", {"episode_id": episode["id"]}, P_SCAN, dedupe=True)
                self.kernel.outbox.emit("episode.detected", {"id": episode["id"], "show_id": show["id"], "master_path": rel})
                created += 1
                transcript = path.with_name(path.stem.replace("_entiere", "") + "_transcript.json")
                if transcript.is_file():
                    self.episodes.update(episode["id"], {"transcript_path": files.rel(transcript), "transcript_status": "done", "status": "transcribed"})
                bornes = path.with_name(path.stem.replace("_entiere", "") + "_bornes.json")
                if bornes.is_file():
                    self.import_bornes(int(episode["id"]), json.loads(bornes.read_text(encoding="utf-8")), files.rel(bornes))
        return {"created": created}

    def _index_episode(self, episode: dict[str, Any], show: dict[str, Any] | None = None) -> None:
        show = show or self.shows.get(episode["show_id"]) or {}
        self.kernel.search.index("episode", episode["id"], episode.get("title") or "", show.get("name") or "", episode.get("notes") or "", url=f"/shows/episodes/{episode['id']}")

    def job_analyze(self, ctx: JobContext) -> dict[str, Any]:
        episode = self.episodes.get(int(ctx.payload.get("episode_id") or 0))
        if not episode:
            raise ValueError("épisode introuvable")
        path = self.kernel.files.open_path(episode["master_path"])
        info = media.probe(path)
        ctx.progress("Forme d'onde")
        peaks = media.waveform(path) if info["duration_s"] else []
        self.episodes.update(episode["id"], {"duration_s": info["duration_s"], "sample_rate": info["sample_rate"], "channels": info["channels"], "waveform": peaks})
        self.kernel.bus.publish("episode", {"id": episode["id"], "reason": "analyzed"})
        return {"duration_s": info["duration_s"], "peaks": len(peaks)}

    # --- transcription -----------------------------------------------------------------------------------

    def request_transcription(self, episode_id: int, actor: str | None = None) -> dict[str, Any]:
        episode = self.episodes.get(episode_id)
        if not episode:
            raise KeyError("épisode introuvable")
        self.episodes.update(episode_id, {"transcript_status": "queued", "transcript_error": ""})
        return self.kernel.jobs.submit("shows.transcribe", {"episode_id": episode_id}, P_USER, dedupe=True, created_by=actor, max_attempts=2)

    def job_transcribe(self, ctx: JobContext) -> dict[str, Any]:
        """Pris par bail par le worker Mac (mlx-whisper) ou par le NAS (faster-whisper si présent)."""
        episode = self.episodes.get(int(ctx.payload.get("episode_id") or 0))
        if not episode:
            raise ValueError("épisode introuvable")
        files = self.kernel.files
        master = files.open_path(episode["master_path"])
        self.episodes.update(episode["id"], {"transcript_status": "running"})
        show = self.shows.get(episode["show_id"]) or {}
        prompt = self._initial_prompt(show, episode)
        try:
            doc = self._run_whisper(master, prompt, ctx)
        except Exception as exc:
            self.episodes.update(episode["id"], {"transcript_status": "failed", "transcript_error": str(exc)[:300]})
            raise
        rel = str(Path(episode["master_path"]).with_name(Path(episode["master_path"]).stem.replace("_entiere", "") + "_transcript.json")).replace("\\", "/")
        files.write_text(rel, json.dumps(doc, ensure_ascii=False))
        self.episodes.update(episode["id"], {"transcript_path": rel, "transcript_status": "done", "status": "transcribed" if episode.get("status") == "detected" else episode.get("status")})
        self.kernel.links.link("episode", episode["id"], "file", rel, role="transcription")
        self.kernel.bus.publish("episode", {"id": episode["id"], "reason": "transcribed"})
        self.kernel.outbox.emit("episode.transcribed", {"id": episode["id"]})
        return {"transcript_path": rel, "words": len(media.transcript_words(doc))}

    def _initial_prompt(self, show: dict[str, Any], episode: dict[str, Any]) -> str:
        names = []
        contacts = self.kernel.modules.get("contacts")
        if contacts:
            for link in self.kernel.links.of("episode", episode["id"], "person"):
                person = contacts.people.get(int(link["other_id"]))
                if person:
                    names.append(person["display_name"])
        parts = ["Radio Campus Orléans", show.get("name") or "", *names]
        return ", ".join(p for p in parts if p)

    def _run_whisper(self, master: Path, prompt: str, ctx: JobContext) -> dict[str, Any]:
        engine = self.kernel.setting("shows.transcribe_engine") or ("mlx" if shutil.which("mlx_whisper") else ("faster" if _importable("faster_whisper") else ""))
        if engine == "mlx":
            with tempfile.TemporaryDirectory() as tmp:
                model = self.kernel.setting("shows.whisper_model", "mlx-community/whisper-large-v3-turbo")
                cmd = ["mlx_whisper", str(master), "--model", model, "--language", "fr", "--word-timestamps", "True", "--condition-on-previous-text", "False", "--output-format", "json", "--output-dir", tmp]
                if prompt:
                    cmd += ["--initial-prompt", prompt]
                ctx.progress("Transcription (mlx-whisper)")
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=4 * 3600)
                if res.returncode != 0:
                    raise RuntimeError(f"mlx_whisper : {res.stderr.strip()[-300:]}")
                produced = list(Path(tmp).glob("*.json"))
                if not produced:
                    raise RuntimeError("mlx_whisper : aucun JSON produit")
                return json.loads(produced[0].read_text(encoding="utf-8"))
        if engine == "faster":
            from faster_whisper import WhisperModel  # type: ignore

            ctx.progress("Transcription (faster-whisper)")
            model = WhisperModel(self.kernel.setting("shows.whisper_model_nas", "small"), device="cpu", compute_type="int8")
            segments, _info = model.transcribe(str(master), language="fr", word_timestamps=True, condition_on_previous_text=False, initial_prompt=prompt or None)
            out = {"segments": []}
            for seg in segments:
                out["segments"].append({"start": seg.start, "end": seg.end, "text": seg.text, "words": [{"word": w.word, "start": w.start, "end": w.end, "probability": w.probability} for w in (seg.words or [])]})
                ctx.heartbeat()
            return out
        raise RuntimeError("aucun moteur de transcription ici : lancer `python -m regie worker transcribe` sur le Mac (mlx-whisper)")

    def transcript(self, episode_id: int) -> dict[str, Any] | None:
        episode = self.episodes.get(episode_id)
        if not episode or not episode.get("transcript_path"):
            return None
        try:
            doc = json.loads(self.kernel.files.open_path(episode["transcript_path"]).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        words = media.transcript_words(doc)
        return {"words": words, "segments": [{"start": s.get("start"), "end": s.get("end"), "text": s.get("text"), "speaker": s.get("speaker")} for s in doc.get("segments") or []], "gaps": media.speech_gaps(words, duration_s=float(episode.get("duration_s") or 0))}

    # --- bornes / séquences ------------------------------------------------------------------------------------

    def segments_of(self, episode_id: int) -> list[dict[str, Any]]:
        return self.segments.list("episode_id = %s", [episode_id], 500)

    def save_segment(self, episode_id: int, data: dict[str, Any], segment_id: int | None = None) -> dict[str, Any]:
        episode = self.episodes.get(episode_id)
        if not episode:
            raise KeyError("épisode introuvable")
        payload = {k: v for k, v in data.items() if k in SEGMENT_COLUMNS and k != "episode_id"}
        duration = float(episode.get("duration_s") or 0)
        if "start_s" in payload or "end_s" in payload or segment_id is None:
            current = self.segments.get(segment_id) if segment_id else {}
            start = float(payload.get("start_s", (current or {}).get("start_s", 0)))
            end = float(payload.get("end_s", (current or {}).get("end_s", 0)))
            if duration and (start < 0 or end > duration + 0.001):
                raise ValueError("bornes hors du master")
            if end <= start:
                raise ValueError("fin avant début")
            payload["start_s"], payload["end_s"] = start, end
        if "phrase_in" in payload or "phrase_out" in payload:
            transcript = self.transcript(episode_id)
            if transcript:
                for key in ("phrase_in", "phrase_out"):
                    phrase = payload.get(key) or ""
                    if phrase:
                        hits = media.find_phrase(transcript["words"], phrase)
                        if len(hits) > 1:
                            raise ValueError(f"{key} : phrase non unique ({len(hits)} occurrences)")
        if segment_id is None:
            payload.setdefault("position", len(self.segments_of(episode_id)))
            row = self.segments.create(payload, episode_id=episode_id)
        else:
            row = self.segments.update(segment_id, payload)
            if row is None:
                raise KeyError("séquence introuvable")
        self.write_bornes(episode_id)
        self.kernel.bus.publish("episode", {"id": episode_id, "reason": "segments"})
        return row

    def delete_segment(self, segment_id: int) -> bool:
        row = self.segments.get(segment_id)
        if not row:
            return False
        ok = self.segments.delete(segment_id)
        if ok:
            self.kernel.links.purge("segment", segment_id)
            self.write_bornes(int(row["episode_id"]))
        return ok

    def _validate_apply(self, ctx: ActionCtx) -> dict[str, Any]:
        for seg_id in ctx.ids:
            self.segments.update(int(seg_id), {"valid": bool(ctx.params.get("valid", True))})
        episodes = {int(self.segments.get(int(i))["episode_id"]) for i in ctx.ids if self.segments.get(int(i))}
        for episode_id in episodes:
            self.write_bornes(episode_id)
            if all(s.get("valid") for s in self.segments_of(episode_id)):
                self.episodes.update(episode_id, {"status": "bounded"})
        return {"validated": ctx.ids}

    def _validate_revert(self, ctx: ActionCtx) -> None:
        for seg_id, previous in ctx.before.items():
            self.segments.update(int(seg_id), {"valid": bool(previous)})
        for seg_id in ctx.ids:
            row = self.segments.get(int(seg_id))
            if row:
                self.write_bornes(int(row["episode_id"]))

    def write_bornes(self, episode_id: int) -> str:
        """Écrit `{date}_{show}_bornes.json` à côté du master : le ReaScript continue de fonctionner."""
        episode = self.episodes.get(episode_id)
        if not episode:
            raise KeyError("épisode introuvable")
        show = self.shows.get(episode["show_id"]) or {}
        doc = media.bornes_document(episode, self.segments_of(episode_id), show.get("handles") or {"in": 0.5, "out": 0.8})
        rel = str(Path(episode["master_path"]).with_name(Path(episode["master_path"]).stem.replace("_entiere", "") + "_bornes.json")).replace("\\", "/")
        if self.kernel.files.available:
            self.kernel.files.write_text(rel, json.dumps(doc, ensure_ascii=False, indent=2))
            self.kernel.links.link("episode", episode_id, "file", rel, role="bornes")
        self.episodes.update(episode_id, {"bornes_path": rel})
        return rel

    def import_bornes(self, episode_id: int, doc: dict[str, Any], rel: str = "") -> int:
        with db.connect(self.kernel.dsn) as conn:
            db.execute(conn, "DELETE FROM segments WHERE episode_id = %s", (episode_id,))
        count = 0
        for seq in media.sequences_from_bornes(doc):
            self.segments.create(seq, episode_id=episode_id)
            count += 1
        self.episodes.update(episode_id, {"bornes_path": rel or "", "status": "bounded" if count else "detected"})
        return count

    def suggest_segments(self, episode_id: int) -> list[dict[str, Any]]:
        """Propositions : blocs de parole entre trous (sans Claude ici : déterministe, l'humain valide)."""
        episode = self.episodes.get(episode_id)
        transcript = self.transcript(episode_id) if episode else None
        if not episode or not transcript:
            return []
        duration = float(episode.get("duration_s") or 0)
        gaps = transcript["gaps"]
        words = transcript["words"]
        blocks: list[dict[str, Any]] = []
        cursor = 0.0
        for gap in [*gaps, {"start": duration or (words[-1]["end"] if words else 0), "end": duration}]:
            if gap["start"] - cursor >= 60:
                inside = [w for w in words if cursor <= w["start"] < gap["start"]]
                if inside:
                    blocks.append({"start_s": round(inside[0]["start"], 2), "end_s": round(inside[-1]["end"], 2), "phrase_in": " ".join(w["text"] for w in inside[:6]), "phrase_out": " ".join(w["text"] for w in inside[-6:]), "kind": "itw", "confidence": 0.5})
            cursor = gap["end"]
        return blocks

    # --- podcasts ------------------------------------------------------------------------------------------------------

    def create_podcast(self, data: dict[str, Any], actor: str | None = None) -> dict[str, Any]:
        segment = self.segments.get(int(data["segment_id"])) if data.get("segment_id") else None
        episode = self.episodes.get(int(data.get("episode_id") or (segment or {}).get("episode_id") or 0))
        if not episode:
            raise KeyError("épisode introuvable")
        if segment and not segment.get("valid"):
            raise ValueError("la séquence doit être validée avant d'en faire un podcast")
        show = self.shows.get(episode["show_id"]) or {}
        title = data.get("title") or (segment or {}).get("title") or episode.get("title") or "Podcast"
        base = f"{episode.get('aired_on') or date.today().isoformat()}-{slugify(show.get('slug') or 'emission')}-{slugify(title)}"[:80]
        slug = base
        index = 2
        while self.podcasts.list("slug = %s", [slug], 1):
            slug = f"{base}-{index}"
            index += 1
        row = self.podcasts.create({"show_id": episode["show_id"], "episode_id": episode["id"], "segment_id": segment["id"] if segment else None, "slug": slug, "title": title, "description": data.get("description") or "", "rights_status": data.get("rights_status") or "unknown", "rights_notes": data.get("rights_notes") or "", "created_by": actor})
        self.kernel.links.link("podcast", row["id"], "episode", episode["id"], role="source")
        if segment:
            self.kernel.links.link("podcast", row["id"], "segment", segment["id"], role="source")
        self.kernel.search.index("podcast", row["id"], title, show.get("name") or "", row.get("description") or "", url=f"/shows/podcasts/{row['id']}")
        self.kernel.outbox.emit("podcast.created", {"id": row["id"]})
        return row

    def update_podcast(self, podcast_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        row = self.podcasts.update(podcast_id, {k: v for k, v in data.items() if k in {"title", "description", "rights_status", "rights_notes", "cover_path"}})
        if row:
            show = self.shows.get(row["show_id"]) or {}
            self.kernel.search.index("podcast", row["id"], row["title"], show.get("name") or "", row.get("description") or "", url=f"/shows/podcasts/{row['id']}")
            self.kernel.outbox.emit("podcast.updated", {"id": podcast_id})
        return row

    def request_export(self, podcast_id: int, actor: str | None = None, fmt: str = "mp3") -> dict[str, Any]:
        podcast = self.podcasts.get(podcast_id)
        if not podcast:
            raise KeyError("podcast introuvable")
        self.podcasts.update(podcast_id, {"status": "exporting", "export_error": ""})
        return self.kernel.jobs.submit("shows.export", {"podcast_id": podcast_id, "format": fmt}, P_USER, dedupe=True, created_by=actor, max_attempts=2)

    def job_export(self, ctx: JobContext) -> dict[str, Any]:
        podcast = self.podcasts.get(int(ctx.payload.get("podcast_id") or 0))
        if not podcast:
            raise ValueError("podcast introuvable")
        episode = self.episodes.get(int(podcast["episode_id"])) if podcast.get("episode_id") else None
        if not episode:
            raise ValueError("épisode source absent")
        segment = self.segments.get(int(podcast["segment_id"])) if podcast.get("segment_id") else None
        show = self.shows.get(episode["show_id"]) or {}
        files = self.kernel.files
        master = files.open_path(episode["master_path"])
        duration = float(episode.get("duration_s") or 0) or media.probe(master)["duration_s"]
        start = float(segment["start_s"]) if segment else 0.0
        end = float(segment["end_s"]) if segment else duration
        handles = (segment or {}).get("handles") or show.get("handles") or {"in": 0.5, "out": 0.8}
        fmt = str(ctx.payload.get("format") or "mp3")
        rel = f"{SHOWS_DIR}/{show.get('folder') or show.get('name')}/{EXTRACT_DIR}/{podcast['slug']}.{fmt}"
        if not files.can_write(rel):
            raise PermissionError(f"{rel} : dossier non inscriptible")
        out = files.resolve(rel)
        ctx.progress("Export ffmpeg")
        try:
            result = media.export_segment(master, out, start, end, duration_s=duration, coupures=(segment or {}).get("coupures") or [], handles=handles)
        except Exception as exc:
            self.podcasts.update(podcast["id"], {"status": "failed", "export_error": str(exc)[:300]})
            raise
        ctx.progress("Mesure loudness")
        loudness = media.measure_loudness(out)
        info = files.index_file(rel, {"role": "extrait", "podcast_id": podcast["id"]})
        self.podcasts.update(podcast["id"], {"file_path": rel, "file_size": int(info.get("size") or 0), "duration_s": result["duration_s"], "loudness_i": loudness, "status": "exported", "export_error": ""})
        self.kernel.links.link("podcast", podcast["id"], "file", rel, role="extrait")
        self.episodes.update(episode["id"], {"status": "edited" if episode.get("status") != "published" else "published"})
        self.kernel.bus.publish("podcast", {"id": podcast["id"], "reason": "exported"})
        self.kernel.outbox.emit("podcast.exported", {"id": podcast["id"], "file_path": rel})
        return {"file_path": rel, **result, "loudness_i": loudness}

    def publishable(self, podcast: dict[str, Any]) -> tuple[bool, str]:
        """Porte bloquante : droits vérifiés et fichier exporté."""
        if podcast.get("rights_status") != "ok":
            return False, "droits non vérifiés (musique, invités) : la publication est bloquée"
        if podcast.get("status") not in {"exported", "published"} or not podcast.get("file_path"):
            return False, "podcast non exporté"
        return True, ""

    def episode_view(self, episode_id: int) -> dict[str, Any] | None:
        episode = self.episodes.get(episode_id)
        if not episode:
            return None
        show = self.shows.get(episode["show_id"])
        links = self.kernel.links.of("episode", episode_id)
        summaries = self.kernel.registry.summaries([(str(l["other_kind"]), str(l["other_id"])) for l in links if l["other_kind"] != "file"])
        for link in links:
            link["other"] = summaries.get(f"{link['other_kind']}:{link['other_id']}") or {"kind": "file", "id": link["other_id"], "title": Path(str(link["other_id"])).name, "icon": "file"}
        return {"episode": episode, "show": show, "segments": self.segments_of(episode_id), "podcasts": self.podcasts.list("episode_id = %s", [episode_id], 100), "links": links, "has_transcript": bool(episode.get("transcript_path"))}


def _importable(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False
