import { cacheCatalogBlob, getSoundBlob } from "./library";
import { colorFromKind } from "./padColor";
import type { CategoryId, LibrarySound } from "../types";

export type CatalogHit = {
  id: string;
  path: string;
  filename: string;
  title: string;
  artist: string;
  kind: string;
  grouping: string;
  folder: string;
  duration: number;
  bytes: number;
  mime: string;
};

const DEFAULT_URL = "http://192.168.1.100:30127";

export function catalogBase(): string {
  const fromEnv = import.meta.env.VITE_CATALOG_URL;
  if (typeof fromEnv === "string" && fromEnv.trim()) return fromEnv.replace(/\/$/, "");
  return DEFAULT_URL;
}

export function catalogSoundId(path: string): string {
  return `ntr:${path}`;
}

export function catalogPathFromId(soundId: string): string | null {
  return soundId.startsWith("ntr:") ? soundId.slice(4) : null;
}

export function hitToSound(hit: CatalogHit): LibrarySound {
  const kind = (hit.kind || "son") as CategoryId;
  const title = hit.artist ? `${hit.artist} — ${hit.title}` : hit.title;
  return {
    soundId: catalogSoundId(hit.path),
    catalogId: hit.id,
    title,
    durationSec: hit.duration || 0,
    kind,
    color: colorFromKind(kind),
    source: "ntr",
  };
}

export async function searchCatalog(query: {
  q?: string;
  kind?: string;
  grouping?: string;
  folder?: string;
  limit?: number;
}): Promise<CatalogHit[]> {
  const url = new URL("/search", catalogBase());
  if (query.q) url.searchParams.set("q", query.q);
  if (query.kind) url.searchParams.set("kind", query.kind);
  if (query.grouping) url.searchParams.set("grouping", query.grouping);
  if (query.folder) url.searchParams.set("folder", query.folder);
  url.searchParams.set("limit", String(query.limit ?? 80));
  const response = await fetch(url);
  if (!response.ok) throw new Error("catalog");
  const payload = (await response.json()) as { hits?: CatalogHit[] };
  return payload.hits ?? [];
}

export async function fetchCatalogBlob(hitOrId: CatalogHit | string): Promise<Blob> {
  const id = typeof hitOrId === "string" ? hitOrId : hitOrId.id;
  const response = await fetch(`${catalogBase()}/media/${encodeURIComponent(id)}`);
  if (!response.ok) throw new Error("media");
  return response.blob();
}

export async function ensureCatalogCache(soundId: string, catalogId?: string): Promise<void> {
  if (!catalogId) return;
  if (await getSoundBlob(soundId)) return;
  const blob = await fetchCatalogBlob(catalogId);
  await cacheCatalogBlob(soundId, blob, blob.type || "audio/mpeg");
}
