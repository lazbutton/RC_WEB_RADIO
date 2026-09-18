import { colorFromKind, isPadColor } from "./padColor";
import type { PadAssign, PadMap } from "../types";

const KEY = "pads-map-v1";

export function emptyMap(): PadMap {
  return Array.from({ length: 64 }, () => null);
}

export function loadMap(): PadMap {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return emptyMap();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length !== 64) return emptyMap();
    return parsed.map((slot) => parseSlot(slot));
  } catch {
    return emptyMap();
  }
}

function parseSlot(slot: unknown): PadAssign | null {
  if (!slot || typeof slot !== "object") return null;
  const row = slot as Record<string, unknown>;
  if (typeof row.title !== "string") return null;
  const kind = typeof row.kind === "string" && row.kind.trim() ? row.kind : "son";
  const color = isPadColor(row.color) ? row.color : colorFromKind(kind);
  if (row.mode === "folder") {
    if (typeof row.folderId !== "string") return null;
    return { mode: "folder", folderId: row.folderId, title: row.title, kind, color };
  }
  if (typeof row.soundId !== "string") return null;
  return {
    mode: "one",
    soundId: row.soundId,
    title: row.title,
    durationSec: typeof row.durationSec === "number" ? row.durationSec : 0,
    kind,
    color,
    ...(typeof row.catalogId === "string" ? { catalogId: row.catalogId } : {}),
    ...(row.source === "ntr" || row.source === "local" ? { source: row.source } : {}),
  };
}

export function saveMap(map: PadMap): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(map));
  } catch {
    /* quota */
  }
}
