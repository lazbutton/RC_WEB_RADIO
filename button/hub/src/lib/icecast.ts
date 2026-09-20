export type IcecastSource = {
  listeners?: number;
  listener_peak?: number;
  listenurl?: string;
  server_name?: string;
  title?: string;
  bitrate?: number | string;
  samplerate?: number;
  server_type?: string;
  stream_start?: string;
  stream_start_iso8601?: string;
  audio_info?: string;
};

export type IcecastStats = {
  icestats?: { source?: IcecastSource | IcecastSource[] };
};

export type TrackCue = {
  artist?: string;
  title?: string;
  album?: string;
  source?: string;
  rid?: number;
  duration?: number | string;
};

export type NowPlaying = TrackCue & {
  on_air?: string;
  remaining?: number | string;
  elapsed?: number | string;
  received_at?: string;
  previous?: TrackCue | null;
  next_autodj?: TrackCue | null;
  next_jingle?: TrackCue | null;
  next_cart?: TrackCue | null;
};

export function icecastSources(payload: IcecastStats | null | undefined): IcecastSource[] {
  const raw = payload?.icestats?.source;
  if (!raw) return [];
  return Array.isArray(raw) ? raw : [raw];
}

export function icecastSource(payload: IcecastStats | null | undefined): IcecastSource | null {
  const all = icecastSources(payload);
  return (
    all.find((item) => /button\.mp3/i.test(`${item.listenurl || ""} ${item.server_name || ""}`)) ||
    all[0] ||
    null
  );
}

export function sourceBitrate(src: IcecastSource | null): number | null {
  if (!src) return null;
  const raw = Number(src.bitrate);
  if (Number.isFinite(raw) && raw > 0) return raw;
  const info = src.audio_info || "";
  const match = /bitrate=(\d+)/i.exec(info);
  return match ? Number(match[1]) : null;
}

export function sourceMount(src: IcecastSource | null): string {
  const url = src?.listenurl || "";
  if (!url) return "";
  try {
    const name = new URL(url).pathname.split("/").filter(Boolean).pop() || "";
    return name;
  } catch {
    const match = /\/([^/?#]+\.[a-z0-9]+)/i.exec(url);
    return match ? match[1] : "";
  }
}

export function sourceCodec(src: IcecastSource | null): string {
  const type = (src?.server_type || "").toLowerCase();
  if (/mpeg|mp3/.test(type)) return "MP3";
  if (type.includes("ogg") || type.includes("vorbis")) return "Ogg";
  if (type.includes("aac")) return "AAC";
  if (/wav|pcm/.test(type)) return "WAV";
  const mount = sourceMount(src).toLowerCase();
  if (mount.endsWith(".mp3")) return "MP3";
  if (mount.endsWith(".ogg")) return "Ogg";
  if (mount.endsWith(".aac")) return "AAC";
  if (mount.endsWith(".wav")) return "WAV";
  return src?.server_type || "";
}
