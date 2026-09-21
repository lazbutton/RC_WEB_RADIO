"""Publier : RSS par émission, articles WordPress, blocs iframe statiques poussés vers le bord public. Déclenché par l'outbox."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from regie.connectors.edge import EdgeConnector
from regie.connectors.wordpress import WordPressConnector
from regie.kernel import db
from regie.kernel.actions import ActionCtx, ActionSpec
from regie.kernel.core import Kernel
from regie.kernel.jobs import P_BACKGROUND, P_USER, JobContext
from regie.modules.publish import blocks

log = logging.getLogger("regie.publish")
TARGETS = ("wordpress", "edge")
REGEN_SECONDS = 3600


class PublishService:
    name = "publish"

    def __init__(self, kernel: Kernel, wordpress=None, edge=None) -> None:
        self.kernel = kernel
        s = kernel.settings
        self.wordpress = wordpress or WordPressConnector(s.wordpress_url, s.wordpress_user, s.wordpress_app_password)
        self.edge = edge or EdgeConnector(s.edge_provider, s.edge_token, s.edge_project, s.edge_account_id, local_dir=kernel.setting("publish.local_dir") or f"{s.regie_data}/public", public_url=kernel.setting("publish.public_url"))
        self._register()

    def _register(self) -> None:
        k = self.kernel
        k.connectors.register(self.wordpress)
        k.connectors.register(self.edge)
        k.actions.register(ActionSpec(kind="shows.publish", module="publish", entity_kind="podcast", label="Podcast publié", apply=self._publish_apply, revert=self._publish_revert, snapshot=self._snapshot, undoable=True))
        k.jobs.register("publish.run", self.job_run, "publish")
        k.jobs.register("publish.edge", self.job_edge, "publish")
        k.outbox.on("publish.requested", lambda event: k.jobs.submit("publish.run", {}, P_USER, dedupe=True))
        k.outbox.on("podcast.published", lambda event: k.jobs.submit("publish.edge", {}, P_USER, dedupe=True))
        k.outbox.on("events.refreshed", lambda event: k.jobs.submit("publish.edge", {}, P_BACKGROUND, dedupe=True))
        k.scheduler.ensure("publish.edge", REGEN_SECONDS, priority=P_BACKGROUND, enabled=self.edge.configured())

    # --- podcast → publication ---------------------------------------------------------------------

    def _snapshot(self, ids: list[str]) -> dict[str, Any]:
        shows = self.kernel.modules.get("shows")
        return {i: {"status": (shows.podcasts.get(int(i)) or {}).get("status"), "published_at": (shows.podcasts.get(int(i)) or {}).get("published_at")} for i in ids} if shows else {}

    def _publish_apply(self, ctx: ActionCtx) -> dict[str, Any]:
        shows = self.kernel.modules.get("shows")
        if shows is None:
            raise RuntimeError("module Émissions absent")
        targets = [t for t in (ctx.params.get("targets") or list(TARGETS)) if t in TARGETS]
        requested = []
        for podcast_id in ctx.ids:
            podcast = shows.podcasts.get(int(podcast_id))
            if not podcast:
                raise ValueError(f"podcast {podcast_id} introuvable")
            ok, reason = shows.publishable(podcast)
            if not ok:
                raise PermissionError(reason)
            shows.podcasts.update(int(podcast_id), {"status": "published", "published_at": podcast.get("published_at") or db.utcnow()})
            with db.connect(self.kernel.dsn) as conn:
                for target in targets:
                    if target == "wordpress" and not self.wordpress.configured():
                        continue
                    if target == "edge" and not self.edge.configured():
                        continue
                    db.execute(conn, "INSERT INTO publications (org_id, entity_kind, entity_id, target, status, payload) VALUES (%s, 'podcast', %s, %s, 'pending', %s) ON CONFLICT (entity_kind, entity_id, target) DO UPDATE SET status = 'pending', error = '', payload = EXCLUDED.payload", (self.kernel.org_id, str(podcast_id), target, db.J({"status": ctx.params.get("wp_status") or "draft"})))
            requested.append(int(podcast_id))
            self.kernel.outbox.emit("podcast.published", {"id": int(podcast_id), "targets": targets})
        self.kernel.outbox.emit("publish.requested", {"podcasts": requested})
        return {"published": requested, "targets": targets}

    def _publish_revert(self, ctx: ActionCtx) -> None:
        shows = self.kernel.modules.get("shows")
        for podcast_id, snap in ctx.before.items():
            if shows:
                shows.podcasts.update(int(podcast_id), {"status": snap.get("status") or "exported", "published_at": snap.get("published_at")})
            with db.connect(self.kernel.dsn) as conn:
                db.execute(conn, "UPDATE publications SET status = 'removed' WHERE entity_kind = 'podcast' AND entity_id = %s", (str(podcast_id),))
        self.kernel.jobs.submit("publish.edge", {}, P_USER, dedupe=True)

    def job_run(self, ctx: JobContext) -> dict[str, Any]:
        with db.connect(self.kernel.dsn) as conn:
            pending = db.fetch_all(conn, "SELECT * FROM publications WHERE org_id = %s AND status = 'pending' ORDER BY id", (self.kernel.org_id,))
        done = failed = 0
        for pub in pending:
            try:
                if pub["target"] == "wordpress":
                    result = self._publish_wordpress(pub)
                elif pub["target"] == "edge":
                    result = {"external_id": "edge", "url": (self.edge.public_url or "") + "/podcasts/index.html"}
                else:
                    raise ValueError(f"cible inconnue : {pub['target']}")
                with db.connect(self.kernel.dsn) as conn:
                    db.execute(conn, "UPDATE publications SET status = 'done', external_id = %s, url = %s, error = '', done_at = now() WHERE id = %s", (str(result.get("external_id") or ""), str(result.get("url") or ""), pub["id"]))
                done += 1
            except Exception as exc:
                failed += 1
                log.warning("publication %s/%s : %s", pub["target"], pub["entity_id"], exc)
                with db.connect(self.kernel.dsn) as conn:
                    db.execute(conn, "UPDATE publications SET status = 'failed', error = %s WHERE id = %s", (str(exc)[:300], pub["id"]))
                self.kernel.connectors.state.error(pub["target"], str(exc))
        if pending:
            self.kernel.jobs.submit("publish.edge", {}, P_USER, dedupe=True)
        self.kernel.bus.publish("publications", {"done": done, "failed": failed})
        return {"done": done, "failed": failed}

    def _publish_wordpress(self, pub: dict[str, Any]) -> dict[str, Any]:
        shows = self.kernel.modules["shows"]
        podcast = shows.podcasts.get(int(pub["entity_id"]))
        if not podcast:
            raise ValueError("podcast disparu")
        show = shows.shows.get(podcast["show_id"]) or {}
        audio_url = self._audio_url(podcast)
        content = f"<p>{podcast.get('description') or ''}</p>"
        if audio_url:
            content += f"<!-- wp:audio --><figure class=\"wp-block-audio\"><audio controls src=\"{audio_url}\"></audio></figure><!-- /wp:audio -->"
        rss = f"{self.edge.public_url}/rss/{show.get('slug')}.xml" if self.edge.public_url else ""
        if rss:
            content += f"<p><a href=\"{rss}\">S'abonner au podcast {show.get('name') or ''}</a></p>"
        status = str((pub.get("payload") or {}).get("status") or "draft")
        ref = self.kernel.connectors.refs.get("wordpress", "podcast", podcast["id"])
        result = self.kernel.connectors.guarded("wordpress", self.wordpress.upsert_post, ref["external_id"] if ref else "", podcast["title"], content, status, (podcast.get("description") or "")[:200], None, podcast["slug"])
        self.kernel.connectors.refs.set("wordpress", "podcast", podcast["id"], result["id"], meta={"url": result.get("url")})
        self.kernel.connectors.state.ok("wordpress")
        return {"external_id": result["id"], "url": result.get("url") or ""}

    def _audio_url(self, podcast: dict[str, Any]) -> str:
        base = self.kernel.setting("publish.audio_base_url")
        if base and podcast.get("file_path"):
            return base.rstrip("/") + "/" + podcast["file_path"].split("/")[-1]
        return ""

    # --- bord public : RSS, JSON, blocs ------------------------------------------------------------------

    def rss(self, show_slug: str) -> str | None:
        from feedgen.feed import FeedGenerator

        shows = self.kernel.modules.get("shows")
        if shows is None:
            return None
        show = shows.show_by_slug(show_slug)
        if not show or not show.get("rss_enabled"):
            return None
        fg = FeedGenerator()
        fg.load_extension("podcast")
        site = self.kernel.settings.wordpress_url or "https://orleans.radiocampus.org"
        fg.id(f"{site}/podcasts/{show['slug']}")
        fg.title(f"{show['name']} · Radio Campus Orléans")
        fg.link(href=site, rel="alternate")
        fg.description(show.get("description") or f"Podcast de l'émission {show['name']} sur Radio Campus Orléans.")
        fg.language("fr")
        fg.podcast.itunes_author("Radio Campus Orléans")
        fg.podcast.itunes_explicit("no")
        if show.get("cover_path") and self.edge.public_url:
            fg.podcast.itunes_image(f"{self.edge.public_url}/covers/{show['slug']}.jpg")
        for podcast in shows.podcasts.list("show_id = %s AND status = 'published'", [show["id"]], 200, order="published_at DESC"):
            entry = fg.add_entry()
            entry.id(f"{site}/podcasts/{podcast['slug']}")
            entry.title(podcast["title"])
            entry.description(podcast.get("description") or podcast["title"])
            published = podcast.get("published_at")
            if published:
                entry.published(datetime.fromisoformat(str(published)) if isinstance(published, str) else published)
            audio = self._audio_url(podcast)
            if audio:
                entry.enclosure(audio, str(int(podcast.get("file_size") or 0)), "audio/mpeg" if audio.endswith(".mp3") else "audio/wav")
            if podcast.get("duration_s"):
                entry.podcast.itunes_duration(int(float(podcast["duration_s"])))
        return fg.rss_str(pretty=True).decode("utf-8")

    def build_files(self) -> dict[str, bytes]:
        """Tous les fichiers du bord public, régénérés depuis Régie."""
        k = self.kernel
        files: dict[str, bytes] = {}
        now = datetime.now(timezone.utc)
        events_mod = k.modules.get("events")
        agenda: list[dict[str, Any]] = []
        if events_mod is not None:
            rows = events_mod.agenda(now.isoformat(), (now + timedelta(days=60)).isoformat())
            agenda = [{"id": e["id"], "title": e["title"], "starts_at": e.get("starts_at"), "ends_at": e.get("ends_at"), "venue_name": e.get("venue_name"), "price": e.get("price"), "url": e.get("url"), "image_url": e.get("image_url"), "radio_campus": e.get("is_radio_campus"), "coverage": [c["kind"] for c in e.get("coverage") or []]} for e in rows if e.get("is_radio_campus") or e.get("coverage") or (k.setting("publish.agenda_all") == "1")]
        files["agenda/index.html"] = blocks.agenda_block(agenda).encode()
        files["agenda/data.json"] = json.dumps(agenda, ensure_ascii=False).encode()
        shows_mod = k.modules.get("shows")
        upcoming: list[dict[str, Any]] = []
        podcasts: list[dict[str, Any]] = []
        if shows_mod is not None:
            for show in shows_mod.shows.list("active", [], 100):
                upcoming.append({"when": show.get("schedule") or "", "title": show["name"], "subtitle": (show.get("description") or "")[:120]})
            for event in agenda:
                if event.get("radio_campus"):
                    upcoming.append({"when": blocks._when(event.get("starts_at") or ""), "title": event["title"], "subtitle": event.get("venue_name") or ""})
            shows_by_id = {s["id"]: s for s in shows_mod.shows.list("", [], 200)}
            for podcast in shows_mod.podcasts.list("status = 'published'", [], 30, order="published_at DESC"):
                show = shows_by_id.get(podcast["show_id"], {})
                ref = k.connectors.refs.get("wordpress", "podcast", podcast["id"])
                podcasts.append({"id": podcast["id"], "title": podcast["title"], "description": podcast.get("description") or "", "show": show.get("name") or "", "show_slug": show.get("slug") or "", "published_at": podcast.get("published_at") or "", "duration_s": podcast.get("duration_s"), "audio_url": self._audio_url(podcast), "url": (ref or {}).get("meta", {}).get("url") or ""})
            for show in shows_by_id.values():
                xml = self.rss(show["slug"])
                if xml:
                    files[f"rss/{show['slug']}.xml"] = xml.encode()
        files["upcoming/index.html"] = blocks.upcoming_block(upcoming).encode()
        files["upcoming/data.json"] = json.dumps(upcoming, ensure_ascii=False).encode()
        files["podcasts/index.html"] = blocks.podcasts_block(podcasts).encode()
        files["podcasts/data.json"] = json.dumps(podcasts, ensure_ascii=False).encode()
        playlist = self._playlist()
        files["playlist/index.html"] = blocks.playlist_block(playlist).encode()
        files["playlist/data.json"] = json.dumps(playlist, ensure_ascii=False).encode()
        team = self._team()
        files["team/index.html"] = blocks.team_block(team).encode()
        files["team/data.json"] = json.dumps(team, ensure_ascii=False).encode()
        files["index.json"] = json.dumps({"generated_at": now.isoformat(), "blocks": ["agenda", "upcoming", "podcasts", "playlist", "team"], "rss": [f for f in files if f.startswith("rss/")]}, ensure_ascii=False).encode()
        return files

    def _playlist(self) -> list[dict[str, Any]]:
        raw = self.kernel.setting("publish.playlist_json")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        return [t for t in data if isinstance(t, dict)][:30]

    def _team(self) -> list[dict[str, Any]]:
        contacts = self.kernel.modules.get("contacts")
        if contacts is None:
            return []
        rows = contacts.people.list("merged_into IS NULL AND 'equipe' = ANY(tags)", [], 100)
        return [{"name": p["display_name"], "role": p.get("job_title") or ""} for p in rows]

    def job_edge(self, ctx: JobContext) -> dict[str, Any]:
        if not self.edge.configured():
            return {"skipped": True, "reason": "bord public non configuré"}
        files = self.build_files()
        ctx.progress(f"Dépôt de {len(files)} fichiers")
        result = self.kernel.connectors.guarded("edge", self.edge.deploy, files)
        self.kernel.connectors.state.ok("edge", meta={"files": len(files), "url": result.get("url")})
        self.kernel.set_setting("publish.last_edge_at", db.iso(db.utcnow()))
        self.kernel.bus.publish("publish", {"edge": result})
        return result

    def snippets(self) -> dict[str, str]:
        base = self.edge.public_url or "https://blocs.radiocampus.example"
        return {block: blocks.embed_snippet(base, block) for block in ("agenda", "upcoming", "podcasts", "playlist", "team")}

    def publications(self, entity_kind: str | None = None, entity_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM publications WHERE org_id = %s"
        args: list[Any] = [self.kernel.org_id]
        if entity_kind:
            sql += " AND entity_kind = %s"
            args.append(entity_kind)
        if entity_id:
            sql += " AND entity_id = %s"
            args.append(str(entity_id))
        sql += " ORDER BY id DESC LIMIT %s"
        args.append(limit)
        with db.connect(self.kernel.dsn) as conn:
            return [db.jsonable(r) or {} for r in db.fetch_all(conn, sql, args)]
