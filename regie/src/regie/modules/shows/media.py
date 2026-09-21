"""Traitement audio : ffprobe, forme d'onde, trous de parole, export ffmpeg (marges, coupures, R128), bornes.json.

Règle absolue : le master ne se découpe jamais ; on écrit toujours ailleurs et jamais hors de ses limites.
"""

from __future__ import annotations

import array
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
PODCAST_LUFS = -16.0
PODCAST_TP = -1.5
PODCAST_LRA = 11.0


def available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe(path: Path) -> dict[str, Any]:
    """Durée, fréquence, canaux, débit. Lève RuntimeError si ffprobe échoue."""
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe : {res.stderr.strip()[:200]}")
    data = json.loads(res.stdout or "{}")
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    fmt = data.get("format", {})
    return {
        "duration_s": float(fmt.get("duration") or audio.get("duration") or 0),
        "sample_rate": int(audio.get("sample_rate") or 0),
        "channels": int(audio.get("channels") or 0),
        "codec": audio.get("codec_name") or "",
        "bit_rate": int(fmt.get("bit_rate") or 0),
        "size": int(fmt.get("size") or 0),
    }


def waveform(path: Path, buckets: int = 800) -> list[float]:
    """Pics normalisés (0..1) sur `buckets` colonnes, décodage mono 8 kHz via ffmpeg."""
    cmd = [FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    res = subprocess.run(cmd, capture_output=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg : {res.stderr.decode(errors='replace').strip()[:200]}")
    samples = array.array("h")
    samples.frombytes(res.stdout[: len(res.stdout) - (len(res.stdout) % 2)])
    total = len(samples)
    if total == 0:
        return []
    size = max(1, math.ceil(total / buckets))
    peaks: list[float] = []
    for start in range(0, total, size):
        chunk = samples[start : start + size]
        peak = max(abs(min(chunk)), abs(max(chunk))) if len(chunk) else 0
        peaks.append(round(peak / 32768.0, 3))
    return peaks[:buckets]


def speech_gaps(words: list[dict[str, Any]], min_gap_s: float = 20.0, merge_s: float = 30.0, duration_s: float | None = None) -> list[dict[str, float]]:
    """Trous de parole (blocs musicaux probables) à partir des mots horodatés."""
    times = sorted((float(w["start"]), float(w["end"])) for w in words if w.get("start") is not None and w.get("end") is not None)
    gaps: list[dict[str, float]] = []
    cursor = 0.0
    for start, end in times:
        if start - cursor >= min_gap_s:
            gaps.append({"start": round(cursor, 2), "end": round(start, 2)})
        cursor = max(cursor, end)
    if duration_s and duration_s - cursor >= min_gap_s:
        gaps.append({"start": round(cursor, 2), "end": round(duration_s, 2)})
    merged: list[dict[str, float]] = []
    for gap in gaps:
        if merged and gap["start"] - merged[-1]["end"] <= merge_s:
            merged[-1]["end"] = gap["end"]
        else:
            merged.append(dict(gap))
    return merged


def clamp_bounds(start_s: float, end_s: float, duration_s: float, handles: dict[str, float] | None = None) -> tuple[float, float]:
    """Bornes avec marges, jamais hors du master, jamais inversées."""
    handles = handles or {}
    begin = max(0.0, float(start_s) - float(handles.get("in") or 0))
    finish = min(float(duration_s), float(end_s) + float(handles.get("out") or 0)) if duration_s else float(end_s) + float(handles.get("out") or 0)
    if finish <= begin:
        raise ValueError("bornes inversées ou vides")
    return round(begin, 3), round(finish, 3)


def kept_intervals(start_s: float, end_s: float, coupures: list[list[float]] | list[dict[str, float]]) -> list[tuple[float, float]]:
    """Intervalles conservés après retrait des coupures (triées, fusionnées, bornées au segment)."""
    cuts: list[tuple[float, float]] = []
    for cut in coupures or []:
        if isinstance(cut, dict):
            a, b = float(cut.get("start", 0)), float(cut.get("end", 0))
        else:
            a, b = float(cut[0]), float(cut[1])
        a, b = max(start_s, min(a, b)), min(end_s, max(a, b))
        if b > a:
            cuts.append((a, b))
    cuts.sort()
    merged: list[tuple[float, float]] = []
    for a, b in cuts:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    kept: list[tuple[float, float]] = []
    cursor = start_s
    for a, b in merged:
        if a > cursor:
            kept.append((cursor, a))
        cursor = max(cursor, b)
    if end_s > cursor:
        kept.append((cursor, end_s))
    return [(round(a, 3), round(b, 3)) for a, b in kept if b - a > 0.05]


def export_segment(master: Path, out: Path, start_s: float, end_s: float, *, duration_s: float, coupures: list | None = None, handles: dict[str, float] | None = None, normalize: bool = True, fade_s: float = 0.15, codec: str = "libmp3lame", bitrate: str = "192k") -> dict[str, Any]:
    """Extrait un segment du master vers `out` (mp3 ou wav selon l'extension). Renvoie durée attendue et intervalles conservés."""
    begin, finish = clamp_bounds(start_s, end_s, duration_s, handles)
    kept = kept_intervals(begin, finish, coupures or [])
    if not kept:
        raise ValueError("rien à conserver après les coupures")
    filters: list[str] = []
    labels: list[str] = []
    for index, (a, b) in enumerate(kept):
        filters.append(f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[s{index}]")
        labels.append(f"[s{index}]")
    chain = "".join(labels) + f"concat=n={len(kept)}:v=0:a=1[cat]"
    filters.append(chain)
    expected = sum(b - a for a, b in kept)
    post = f"[cat]afade=t=in:st=0:d={fade_s},afade=t=out:st={max(0.0, expected - fade_s):.3f}:d={fade_s}"
    if normalize:
        post += f",loudnorm=I={PODCAST_LUFS}:TP={PODCAST_TP}:LRA={PODCAST_LRA}"
    post += "[out]"
    filters.append(post)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    cmd = [FFMPEG, "-v", "error", "-y", "-i", str(master), "-filter_complex", ";".join(filters), "-map", "[out]"]
    if out.suffix.lower() == ".wav":
        cmd += ["-c:a", "pcm_s16le"]
    else:
        cmd += ["-c:a", codec, "-b:a", bitrate]
    cmd += ["-f", "wav" if out.suffix.lower() == ".wav" else "mp3", str(tmp)]
    res = subprocess.run(cmd, capture_output=True, timeout=1800)
    if res.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg : {res.stderr.decode(errors='replace').strip()[:300]}")
    tmp.replace(out)
    info = probe(out)
    return {"begin": begin, "end": finish, "kept": kept, "expected_s": round(expected, 3), "duration_s": info["duration_s"], "size": info["size"]}


def measure_loudness(path: Path) -> float | None:
    """Loudness intégrée (LUFS) via ebur128, ou None si indisponible."""
    cmd = [FFMPEG, "-v", "info", "-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    for line in reversed(res.stderr.splitlines()):
        line = line.strip()
        if line.startswith("I:") and "LUFS" in line:
            try:
                return float(line.split(":")[1].split("LUFS")[0].strip())
            except ValueError:
                return None
    return None


def bornes_document(episode: dict[str, Any], segments: list[dict[str, Any]], show_handles: dict[str, float]) -> dict[str, Any]:
    """bornes.json compatible ReaScript : `sequences[]` avec in/out/phrase_in/phrase_out/coupures/handles/valide."""
    return {
        "version": 2,
        "master": Path(episode["master_path"]).name,
        "master_path": episode["master_path"],
        "duree": float(episode.get("duration_s") or 0),
        "handles": show_handles,
        "sequences": [
            {
                "titre": seg.get("title") or "",
                "type": seg.get("kind") or "itw",
                "in": float(seg["start_s"]),
                "out": float(seg["end_s"]),
                "phrase_in": seg.get("phrase_in") or "",
                "phrase_out": seg.get("phrase_out") or "",
                "locuteur": seg.get("speaker") or "",
                "coupures": [[float(c[0]), float(c[1])] if not isinstance(c, dict) else [float(c["start"]), float(c["end"])] for c in (seg.get("coupures") or [])],
                "handles": seg.get("handles") or show_handles,
                "valide": bool(seg.get("valid")),
                "confiance": float(seg.get("confidence") or 0),
                "note": seg.get("notes") or "",
            }
            for seg in sorted(segments, key=lambda s: (s.get("position", 0), s["start_s"]))
        ],
    }


def sequences_from_bornes(doc: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for index, seq in enumerate(doc.get("sequences") or []):
        out.append({"position": index, "title": seq.get("titre") or "", "kind": seq.get("type") or "itw", "start_s": float(seq["in"]), "end_s": float(seq["out"]), "phrase_in": seq.get("phrase_in") or "", "phrase_out": seq.get("phrase_out") or "", "speaker": seq.get("locuteur") or "", "coupures": seq.get("coupures") or [], "handles": seq.get("handles"), "valid": bool(seq.get("valide")), "confidence": float(seq.get("confiance") or 0), "notes": seq.get("note") or ""})
    return out


def transcript_words(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Mots horodatés depuis un JSON Whisper (mlx-whisper / faster-whisper / openai-whisper)."""
    words: list[dict[str, Any]] = []
    for segment in doc.get("segments") or []:
        for word in segment.get("words") or []:
            text = (word.get("word") or word.get("text") or "").strip()
            if text and word.get("start") is not None:
                words.append({"text": text, "start": float(word["start"]), "end": float(word.get("end") or word["start"])})
        if not segment.get("words") and segment.get("text") and segment.get("start") is not None:
            words.append({"text": str(segment["text"]).strip(), "start": float(segment["start"]), "end": float(segment.get("end") or segment["start"])})
    return words


def find_phrase(words: list[dict[str, Any]], phrase: str) -> list[float]:
    """Positions (secondes) où la phrase apparaît ; l'éditeur exige l'unicité."""
    target = [w for w in phrase.lower().replace("’", "'").split() if w]
    if not target:
        return []
    tokens = [w["text"].lower().strip(".,;:!?«»\"()").replace("’", "'") for w in words]
    hits: list[float] = []
    for index in range(len(tokens) - len(target) + 1):
        if all(tokens[index + j].startswith(target[j].strip(".,;:!?«»\"()")) for j in range(len(target))):
            hits.append(float(words[index]["start"]))
    return hits
