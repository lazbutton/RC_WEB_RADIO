from __future__ import annotations

import array
import json
import math
import subprocess
import wave
from pathlib import Path

import pytest

from regie.modules.shows import media

pytestmark = pytest.mark.skipif(not media.available(), reason="ffmpeg absent")
RATE = 8000


def make_master(path: Path, seconds: int = 12) -> None:
    """Une seconde = une fréquence (200 Hz × n) : on retrouve chaque seconde dans l'export."""
    samples = array.array("h")
    for second in range(seconds):
        freq = 200 * (second + 1)
        for i in range(RATE):
            samples.append(int(12000 * math.sin(2 * math.pi * freq * i / RATE)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(samples.tobytes())


def dominant_freq(path: Path, start_s: float, length_s: float = 0.5) -> float:
    """Fréquence dominante d'une tranche (comptage de passages à zéro), sur le fichier exporté décodé par ffmpeg."""
    res = subprocess.run([media.FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"], capture_output=True, check=True)
    data = array.array("h")
    data.frombytes(res.stdout[: len(res.stdout) - len(res.stdout) % 2])
    begin = int(start_s * RATE)
    chunk = data[begin : begin + int(length_s * RATE)]
    crossings = sum(1 for a, b in zip(chunk, chunk[1:]) if (a < 0) != (b < 0))
    return crossings / (2 * length_s)


def test_media_helpers_never_leave_master_bounds():
    assert media.clamp_bounds(1.0, 5.0, 10.0, {"in": 2, "out": 10}) == (0.0, 10.0)
    with pytest.raises(ValueError):
        media.clamp_bounds(5.0, 5.0, 10.0)
    assert media.kept_intervals(0.0, 10.0, [[2, 4], [3, 5], [9, 12]]) == [(0.0, 2.0), (5.0, 9.0)]
    words = [{"text": "bonjour", "start": 0.0, "end": 0.4}, {"text": "à", "start": 0.5, "end": 0.6}, {"text": "tous", "start": 0.6, "end": 0.9}, {"text": "reprise", "start": 40.0, "end": 40.5}, {"text": "bonjour", "start": 41.0, "end": 41.3}]
    assert media.speech_gaps(words, min_gap_s=20, merge_s=0, duration_s=100.0) == [{"start": 0.9, "end": 40.0}, {"start": 41.3, "end": 100.0}]
    assert media.speech_gaps(words, min_gap_s=20, merge_s=30, duration_s=100.0) == [{"start": 0.9, "end": 100.0}], "un jingle parlé de 1 s ne coupe pas le bloc musical"
    assert media.find_phrase(words, "Bonjour à tous") == [0.0]
    assert len(media.find_phrase(words, "bonjour")) == 2


def test_export_with_cut_keeps_only_wanted_seconds(tmp_path):
    master = tmp_path / "master.wav"
    make_master(master, seconds=12)
    out = tmp_path / "extrait.wav"
    result = media.export_segment(master, out, 2.0, 8.0, duration_s=12.0, coupures=[[4.0, 6.0]], handles={"in": 0, "out": 0}, normalize=False, fade_s=0.01)
    assert result["kept"] == [(2.0, 4.0), (6.0, 8.0)]
    assert abs(result["duration_s"] - 4.0) < 0.05
    # secondes 2,3 (600 Hz, 800 Hz) puis 6,7 (1400 Hz, 1600 Hz) : la coupure 4–6 a disparu.
    assert abs(dominant_freq(out, 0.25) - 600) < 40
    assert abs(dominant_freq(out, 1.25) - 800) < 40
    assert abs(dominant_freq(out, 2.25) - 1400) < 60
    assert abs(dominant_freq(out, 3.25) - 1600) < 60
    clipped = media.export_segment(master, tmp_path / "fin.wav", 10.0, 30.0, duration_s=12.0, handles={"in": 0.5, "out": 5}, normalize=False)
    assert clipped["end"] == 12.0 and abs(clipped["duration_s"] - 2.5) < 0.05, "jamais au-delà du master"
    peaks = media.waveform(master, buckets=24)
    assert len(peaks) == 24 and all(0 < p <= 1 for p in peaks)
    with pytest.raises(ValueError):
        media.export_segment(master, tmp_path / "vide.wav", 2.0, 4.0, duration_s=12.0, coupures=[[2.0, 4.0]], handles={"in": 0, "out": 0})


@pytest.fixture
def stack(app_client, media_root):
    kernel = app_client.kernel
    show_dir = media_root / "40-emissions" / "Hph" / "Entière"
    make_master(show_dir / "2026-09-11_hph_entiere.wav", seconds=12)
    words = []
    phrases = ["bonjour à tous bienvenue dans hph", "on reçoit ce soir jasmine", "merci d'être venue", "à la semaine prochaine"]
    t = 0.2
    for phrase in phrases:
        for word in phrase.split():
            words.append({"word": word, "start": round(t, 2), "end": round(t + 0.25, 2)})
            t += 0.3
        t += 0.5
    (show_dir / "2026-09-11_hph_transcript.json").write_text(json.dumps({"segments": [{"start": 0.2, "end": t, "text": " ".join(phrases), "words": words}]}), encoding="utf-8")
    return app_client, kernel


def test_detect_transcript_bounds_export_and_rights_gate(stack):
    client, kernel = stack
    shows = kernel.modules["shows"]
    assert client.post("/api/v1/shows/detect").status_code == 202
    kernel.jobs.drain()
    listed = client.get("/api/v1/shows").json()["shows"]
    assert listed[0]["name"] == "Hph" and listed[0]["slug"] == "hph" and listed[0]["episodes"] == 1
    episodes = client.get("/api/v1/shows/episodes").json()["episodes"]
    episode = episodes[0]
    assert episode["aired_on"] == "2026-09-11" and episode["transcript_status"] == "done"
    assert abs(episode["duration_s"] - 12.0) < 0.05 and episode["channels"] == 1
    view = client.get(f"/api/v1/shows/episodes/{episode['id']}").json()
    assert view["has_transcript"] and len(view["episode"]["waveform"]) > 100
    transcript = client.get(f"/api/v1/shows/episodes/{episode['id']}/transcript").json()
    assert transcript["words"][0]["text"] == "bonjour"
    # bornes : hors master refusé, phrase non unique refusée, puis une séquence valide écrit le bornes.json
    assert client.post(f"/api/v1/shows/episodes/{episode['id']}/segments", json={"start_s": 2, "end_s": 40}).status_code == 400
    bad = client.post(f"/api/v1/shows/episodes/{episode['id']}/segments", json={"start_s": 1, "end_s": 3, "phrase_in": "à"})
    assert bad.status_code == 400 and "non unique" in bad.json()["detail"]["error"]
    seg = client.post(f"/api/v1/shows/episodes/{episode['id']}/segments", json={"title": "Interview Jasmine", "start_s": 2.0, "end_s": 8.0, "phrase_in": "on reçoit ce soir", "phrase_out": "merci d'être venue", "coupures": [[4.0, 6.0]]})
    assert seg.status_code == 201, seg.text
    segment = seg.json()["segment"]
    bornes_path = client.get(f"/api/v1/shows/episodes/{episode['id']}").json()["episode"]["bornes_path"]
    assert bornes_path.endswith("2026-09-11_hph_bornes.json")
    doc = json.loads(kernel.files.open_path(bornes_path).read_text(encoding="utf-8"))
    assert doc["sequences"][0]["in"] == 2.0 and doc["sequences"][0]["coupures"] == [[4.0, 6.0]] and doc["sequences"][0]["valide"] is False
    # podcast impossible avant validation
    refused = client.post("/api/v1/shows/podcasts", json={"segment_id": segment["id"], "title": "Jasmine"})
    assert refused.status_code == 400
    validated = client.post("/api/v1/shows/segments/validate", json={"ids": [segment["id"]]})
    assert validated.status_code == 200
    assert json.loads(kernel.files.open_path(bornes_path).read_text())["sequences"][0]["valide"] is True
    assert client.get(f"/api/v1/shows/episodes/{episode['id']}").json()["episode"]["status"] == "bounded"
    created = client.post("/api/v1/shows/podcasts", json={"segment_id": segment["id"], "title": "Jasmine Not Jafar en interview"})
    assert created.status_code == 201
    podcast = created.json()["podcast"]
    assert podcast["slug"].startswith("2026-09-11-hph-jasmine")
    assert client.get(f"/api/v1/shows/podcasts/{podcast['id']}").json()["podcast"]["blocked_reason"].startswith("droits")
    assert client.post(f"/api/v1/shows/podcasts/{podcast['id']}/export", json={"format": "wav"}).status_code == 202
    kernel.jobs.drain()
    exported = client.get(f"/api/v1/shows/podcasts/{podcast['id']}").json()["podcast"]
    assert exported["status"] == "exported", exported
    assert exported["file_path"] == f"40-emissions/Hph/extraits/{podcast['slug']}.wav"
    assert abs(exported["duration_s"] - 5.3) < 0.1, "2→8 avec marges 0,5/0,8 et coupure 4–6"
    assert exported["loudness_i"] is not None
    assert exported["publishable"] is False, "droits inconnus : publication bloquée"
    client.patch(f"/api/v1/shows/podcasts/{podcast['id']}", json={"rights_status": "ok", "rights_notes": "Pas de musique commerciale, accord invitée"})
    assert client.get(f"/api/v1/shows/podcasts/{podcast['id']}").json()["podcast"]["publishable"] is True
    undo = client.post(f"/api/v1/actions/{validated.json()['action']['id']}/undo")
    assert undo.status_code == 200
    assert json.loads(kernel.files.open_path(bornes_path).read_text())["sequences"][0]["valide"] is False
    suggestions = client.get(f"/api/v1/shows/episodes/{episode['id']}/suggest").json()["suggestions"]
    assert isinstance(suggestions, list)
    assert client.get("/api/v1/search", params={"q": "jasmine"}).json()["hits"]
    assert shows.import_bornes(int(episode["id"]), doc) == 1
