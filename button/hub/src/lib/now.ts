import type { IcecastSource, NowPlaying, TrackCue } from "./icecast";

const AUDIO_EXT = /\.(mp3|flac|ogg|opus|oga|wav|m4a|aac|aiff|aif|wma)$/i;
const LEADING_INDEX = /^\d+\s*[-.]?\s+/;
const CART_NAMES = new Set([
  "prog",
  "jingles",
  "pubs",
  "promos",
  "spots",
  "carts",
  "autodj",
  "auto-dj",
  "auto dj",
]);

export type OnAirCopy = {
  title: string;
  artist: string;
};

function looksLikeMediaFile(raw: string): boolean {
  const text = raw.trim();
  if (!text) return false;
  return AUDIO_EXT.test(text) || text.includes("/") || text.includes("\\");
}

export function stripMediaName(raw: string): string {
  let text = raw.trim();
  if (!text) return "";
  const slash = Math.max(text.lastIndexOf("/"), text.lastIndexOf("\\"));
  if (slash >= 0) text = text.slice(slash + 1);
  text = text.replace(AUDIO_EXT, "");
  text = text.replace(/_/g, " ").replace(/\s+/g, " ").trim();
  return text.replace(LEADING_INDEX, "").trim();
}

export function isCartName(raw: string): boolean {
  const name = raw.trim().toLowerCase();
  if (!name) return false;
  if (CART_NAMES.has(name)) return true;
  return /^(jingle|pub|promo|spot|cart)s?\b/.test(name);
}

function icyLooksLikeCartFile(artist: string, title: string, icyTitle: string): boolean {
  const name = artist.trim();
  if (!name || !icyTitle) return false;
  const sep = icyTitle.startsWith(`${name} — `) ? " — " : icyTitle.startsWith(`${name} - `) ? " - " : null;
  if (!sep) return false;
  const rest = icyTitle.slice(name.length + sep.length).trim();
  return looksLikeMediaFile(rest) || looksLikeMediaFile(title);
}

function splitArtistTitle(raw: string): OnAirCopy | null {
  const text = stripMediaName(raw);
  const sep = text.includes(" — ") ? " — " : text.includes(" - ") ? " - " : null;
  if (!sep) return null;
  const cut = text.indexOf(sep);
  const artist = text.slice(0, cut).trim();
  let title = text.slice(cut + sep.length).trim().replace(LEADING_INDEX, "").trim();
  if (artist && title.toLowerCase().startsWith(artist.toLowerCase() + sep)) {
    title = title.slice(artist.length + sep.length).trim();
  }
  if (artist && title) return { artist, title };
  return null;
}

function cueHasCopy(cue: TrackCue | null | undefined): cue is TrackCue {
  if (!cue) return false;
  return Boolean((cue.title || "").trim() || (cue.artist || "").trim());
}

/** Titre en grand, artiste / album en second. Jamais le nom de cart. */
export function onAirCopy(now: TrackCue | null | undefined, icy: IcecastSource | null = null): OnAirCopy {
  const rawTitle = (now?.title || "").trim();
  const rawArtist = (now?.artist || "").trim();
  const icyTitle = (icy?.title || "").trim();
  const album = (now?.album || "").trim();
  const combinedFrom = looksLikeMediaFile(rawTitle) && icyTitle ? icyTitle : rawTitle || icyTitle;

  let title = looksLikeMediaFile(combinedFrom) ? stripMediaName(combinedFrom) : combinedFrom;
  let artist = looksLikeMediaFile(rawArtist) ? stripMediaName(rawArtist) : rawArtist;

  if (
    isCartName(artist) ||
    isCartName(rawArtist) ||
    icyLooksLikeCartFile(rawArtist, rawTitle, icyTitle) ||
    icyLooksLikeCartFile(artist, rawTitle, icyTitle)
  ) {
    artist = "";
  }

  const combined = splitArtistTitle(combinedFrom);
  if (combined) {
    title = combined.title;
    if (!isCartName(combined.artist)) artist = combined.artist;
  }

  if (!title && album && !isCartName(album)) title = album;
  if (!artist && album && !isCartName(album) && album !== title) artist = album;
  return { title, artist };
}

export function previousTrack(now: NowPlaying | null): TrackCue | null {
  return cueHasCopy(now?.previous) ? now.previous : null;
}

export function pickNext(now: NowPlaying | null): TrackCue | null {
  if (!now) return null;
  const source = (now.source || "").toLowerCase();
  let cue: TrackCue | null | undefined;
  if (source.includes("jingle")) cue = now.next_jingle;
  else if (source.includes("cart") && !source.includes("autodj")) cue = now.next_cart;
  else cue = now.next_autodj;
  if (!cueHasCopy(cue)) {
    cue = now.next_autodj || now.next_cart || now.next_jingle;
  }
  return cueHasCopy(cue) ? cue : null;
}

export function remainingSeconds(now: NowPlaying | null): number | null {
  const rem = Number(now?.remaining);
  if (Number.isFinite(rem) && rem > 0) {
    const at = now?.received_at ? Date.parse(now.received_at) : Number.NaN;
    if (!Number.isFinite(at)) return rem;
    return Math.max(0, rem - (Date.now() - at) / 1000);
  }
  const duration = cueDuration(now);
  const elapsed = elapsedSeconds(now);
  if (duration == null || elapsed == null || duration < elapsed - 0.5) return null;
  const left = duration - elapsed;
  return left > 0 ? left : null;
}

export function elapsedSeconds(now: NowPlaying | null): number | null {
  const raw = Number(now?.elapsed);
  if (!Number.isFinite(raw) || raw < 0) return null;
  const at = now?.received_at ? Date.parse(now.received_at) : Number.NaN;
  if (!Number.isFinite(at)) return raw;
  return Math.max(0, raw + (Date.now() - at) / 1000);
}

export function cueDuration(cue: TrackCue | null | undefined): number | null {
  const duration = Number(cue?.duration);
  return Number.isFinite(duration) && duration > 0 ? duration : null;
}

export function trackDuration(now: NowPlaying | null): number | null {
  const fromCue = cueDuration(now);
  const elapsed = elapsedSeconds(now);
  if (fromCue && (elapsed == null || fromCue >= elapsed - 0.5)) return fromCue;
  const remaining = remainingSeconds(now);
  if (remaining == null || elapsed == null) return null;
  return remaining + elapsed;
}
