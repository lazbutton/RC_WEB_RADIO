from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from regie.connectors.edge import EdgeConnector
from regie.connectors.wordpress import FakeWordPress


@pytest.fixture
def stack(dsn, media_root, tmp_path):
    from regie.app import create_app
    from regie.config import get_settings
    from regie.kernel.core import Kernel
    from regie.modules import load_modules
    from regie.modules import publish as publish_module

    settings = get_settings().model_copy(update={"regie_run_workers": False})
    kernel = Kernel(settings)
    load_modules(kernel, ["regie.modules.mail", "regie.modules.contacts", "regie.modules.events", "regie.modules.planning", "regie.modules.shows"])
    wp = FakeWordPress()
    edge = EdgeConnector("dir", local_dir=str(tmp_path / "public"), public_url="https://blocs.radiocampus.test")
    kernel.set_setting("publish.audio_base_url", "https://media.radiocampus.test/podcasts")
    kernel.modules["publish"] = publish_module.setup(kernel, wordpress=wp, edge=edge)
    app = create_app(settings, start_workers=False, kernel=kernel)
    kernel.auth.ensure_default_permissions()
    kernel.auth.create_user("laz@test", "Laz", "motdepasse-solide", "admin")
    client = TestClient(app, headers={"X-Regie": "1"})
    client.__enter__()
    client.post("/api/v1/auth/login", json={"email": "laz@test", "password": "motdepasse-solide"})
    client.kernel = kernel  # type: ignore[attr-defined]
    client.wp = wp  # type: ignore[attr-defined]
    client.public = tmp_path / "public"  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


def _podcast(kernel, rights: str = "ok"):
    shows = kernel.modules["shows"]
    show = shows.create_show({"name": "Hph", "schedule": "Vendredi 18h", "description": "Hip-hop et alentours"})
    episode = shows.episodes.create({"show_id": show["id"], "title": "Hph · 2026-09-11", "aired_on": "2026-09-11", "master_path": "40-emissions/Hph/Entière/2026-09-11_hph_entiere.mp3", "duration_s": 3600, "status": "edited"})
    podcast = shows.create_podcast({"episode_id": episode["id"], "title": "Jasmine Not Jafar en interview", "description": "Avant le concert à l'Astrolabe.", "rights_status": rights})
    shows.podcasts.update(podcast["id"], {"status": "exported", "file_path": f"40-emissions/Hph/extraits/{podcast['slug']}.mp3", "file_size": 12_345_678, "duration_s": 912.4})
    return show, shows.podcasts.get(podcast["id"])


def test_publish_flow_wordpress_rss_and_blocks(stack):
    client = stack
    kernel = client.kernel
    show, podcast = _podcast(kernel)
    blocked_show, blocked = _podcast(kernel, rights="unknown") if False else (None, None)
    status = client.get("/api/v1/publish/status").json()
    assert status["edge"]["configured"] and "regie:height" in status["snippets"]["podcasts"]
    res = client.post(f"/api/v1/publish/podcasts/{podcast['id']}", json={"targets": ["wordpress", "edge"], "wp_status": "draft"})
    assert res.status_code == 200, res.text
    pubs = res.json()["publications"]
    assert {p["target"] for p in pubs} == {"wordpress", "edge"} and all(p["status"] == "pending" for p in pubs)
    kernel.outbox.deliver_pending()
    kernel.jobs.drain()
    published = kernel.modules["shows"].podcasts.get(podcast["id"])
    assert published["status"] == "published" and published["published_at"]
    pubs = client.get(f"/api/v1/shows/podcasts/{podcast['id']}").json()["publications"]
    assert {p["target"]: p["status"] for p in pubs} == {"wordpress": "done", "edge": "done"}
    post = next(iter(client.wp.posts.values()))
    assert post["status"] == "draft" and "Jasmine" in post["title"] and "wp-block-audio" in post["content"]
    assert kernel.connectors.refs.get("wordpress", "podcast", podcast["id"])["external_id"] == post["id"]
    public: Path = client.public
    assert (public / "podcasts" / "index.html").is_file()
    html = (public / "podcasts" / "index.html").read_text()
    assert "Jasmine Not Jafar en interview" in html and "regie:height" in html and "Content-Security-Policy" in html
    rss = (public / "rss" / "hph.xml").read_text()
    assert "<enclosure" in rss and "media.radiocampus.test/podcasts" in rss and "<itunes:duration>912</itunes:duration>" in rss
    index = json.loads((public / "index.json").read_text())
    assert set(index["blocks"]) == {"agenda", "upcoming", "podcasts", "playlist", "team"} and "rss/hph.xml" in index["rss"]
    upcoming = json.loads((public / "upcoming" / "data.json").read_text())
    assert upcoming[0]["title"] == "Hph" and upcoming[0]["when"] == "Vendredi 18h"
    local_rss = TestClient(client.app).get("/api/v1/publish/rss/hph.xml", params={"key": kernel.modules["mail"].feed_token()})
    assert local_rss.status_code == 200 and local_rss.text.startswith("<?xml")
    assert client.get("/api/v1/publish/preview/podcasts").status_code == 200
    assert client.get("/api/v1/publish/preview/nope").status_code == 404
    # annulation : le podcast redevient exporté, les publications sont marquées retirées, le bord est régénéré sans lui
    action = client.get("/api/v1/actions?module=publish").json()["actions"][0]
    assert client.post(f"/api/v1/actions/{action['id']}/undo").status_code == 200
    kernel.jobs.drain()
    assert kernel.modules["shows"].podcasts.get(podcast["id"])["status"] == "exported"
    assert "Jasmine" not in (public / "podcasts" / "index.html").read_text()
    # republier = même article WordPress (idempotent, via external_refs), pas un doublon
    client.post(f"/api/v1/publish/podcasts/{podcast['id']}", json={"targets": ["wordpress"], "wp_status": "publish"})
    kernel.outbox.deliver_pending()
    kernel.jobs.drain()
    assert len(client.wp.posts) == 1 and next(iter(client.wp.posts.values()))["status"] == "publish"
    assert "Jasmine" in (public / "podcasts" / "index.html").read_text()
    # échec WordPress : publication en échec, coupe-circuit incrémenté, rien de perdu
    client.wp.fail = True
    client.post(f"/api/v1/publish/podcasts/{podcast['id']}", json={"targets": ["wordpress"]})
    kernel.outbox.deliver_pending()
    kernel.jobs.drain()
    failed = [p for p in client.get(f"/api/v1/shows/podcasts/{podcast['id']}").json()["publications"] if p["target"] == "wordpress"][0]
    assert failed["status"] == "failed" and "indisponible" in failed["error"]
    assert kernel.connectors.state.get("wordpress")["failures"] >= 1


def test_rights_gate_blocks_publication(stack):
    client = stack
    _show, podcast = _podcast(client.kernel, rights="unknown")
    res = client.post(f"/api/v1/publish/podcasts/{podcast['id']}", json={})
    assert res.status_code == 409 and "droits" in res.json()["detail"]["error"]
    assert client.kernel.modules["shows"].podcasts.get(podcast["id"])["status"] == "exported"
    assert client.get("/api/v1/publish/files").json()["files"]
