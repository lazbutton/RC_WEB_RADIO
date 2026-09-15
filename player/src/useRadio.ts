import { useCallback, useEffect, useRef, useState } from "react";

export type NowPlaying = {
  title: string;
  artist: string;
  duration: number | null;
  startedAt: number | null;
};

type NowPayload = {
  title?: string;
  artist?: string;
  album?: string;
  duration?: number | string;
  started_at?: string;
  on_air?: string;
  received_at?: string;
  remaining?: number | string;
  server_description?: string;
  server_name?: string;
};

function parseSec(value: number | string | undefined): number | null {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function parseTime(value: string | undefined): number | null {
  if (!value) return null;
  const t = Date.parse(value);
  return Number.isFinite(t) ? t : null;
}

/** Cart Radiotomate : artist = nom de playlist, title = « Artiste - Titre ». */
export function displayNow(data: NowPayload): NowPlaying {
  let title = (data.title || data.server_description || "").trim();
  let artist = (data.artist || data.server_name || "").trim();
  const album = (data.album || "").trim();
  const cut = title.indexOf(" - ");
  if (cut > 0 && (!artist || artist === album)) {
    const left = title.slice(0, cut).trim();
    const right = title.slice(cut + 3).trim();
    if (left && right) {
      artist = left;
      title = right;
    }
  }
  const duration = parseSec(data.duration);
  const startedAt =
    parseTime(data.started_at) ||
    parseTime(data.on_air) ||
    (duration && parseSec(data.remaining)
      ? Date.now() - (duration - (parseSec(data.remaining) as number)) * 1000
      : parseTime(data.received_at));
  return { title, artist, duration, startedAt: duration ? startedAt : null };
}

export function remainingSeconds(now: NowPlaying, at = Date.now()): number | null {
  if (!now.duration || !now.startedAt) return null;
  return Math.max(0, now.duration - (at - now.startedAt) / 1000);
}

export function formatRemain(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `−${m}:${r.toString().padStart(2, "0")}`;
}

type IcecastStatus = {
  icestats?: {
    source?: NowPayload | NowPayload[];
  };
};

function cfg() {
  return window.NTR_CONFIG;
}

function wait(sb: SourceBuffer) {
  if (!sb.updating) return Promise.resolve();
  return new Promise<void>((resolve) => {
    sb.addEventListener("updateend", () => resolve(), { once: true });
  });
}

export function useRadio() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const connectedEl = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const playingRef = useRef(false);

  const [playing, setPlaying] = useState(false);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
  const [now, setNow] = useState<NowPlaying>({
    title: "",
    artist: "",
    duration: null,
    startedAt: null,
  });
  const [, setClock] = useState(0);

  const ensureGraph = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    if (!ctxRef.current) {
      const ctx = new Ctx();
      const node = ctx.createAnalyser();
      node.fftSize = 2048;
      node.smoothingTimeConstant = 0.15;
      ctxRef.current = ctx;
      analyserRef.current = node;
      setAnalyser(node);
    }
    if (connectedEl.current !== audio) {
      const source = ctxRef.current.createMediaElementSource(audio);
      const node = analyserRef.current;
      if (!node) return;
      source.connect(node);
      node.connect(ctxRef.current.destination);
      connectedEl.current = audio;
    }
  }, []);

  const teardownStream = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
  }, []);

  const startMse = useCallback(async (url: string) => {
    const audio = audioRef.current;
    if (!audio) throw new Error("no-audio");
    if (!window.MediaSource || !MediaSource.isTypeSupported("audio/mpeg")) {
      throw new Error("mse");
    }
    const abort = new AbortController();
    abortRef.current = abort;
    const res = await fetch(url, {
      mode: "cors",
      cache: "no-store",
      signal: abort.signal,
    });
    if (!res.ok || !res.body) throw new Error("fetch");

    const mediaSource = new MediaSource();
    const obj = URL.createObjectURL(mediaSource);
    objectUrlRef.current = obj;
    audio.src = obj;

    await new Promise<void>((resolve, reject) => {
      mediaSource.addEventListener("sourceopen", () => resolve(), { once: true });
      mediaSource.addEventListener("error", () => reject(new Error("ms")), { once: true });
    });

    const sb = mediaSource.addSourceBuffer("audio/mpeg");
    sb.mode = "sequence";
    const reader = res.body.getReader();

    void (async () => {
      try {
        while (playingRef.current) {
          const { done, value } = await reader.read();
          if (done || !value?.byteLength) break;
          if (mediaSource.readyState !== "open") break;
          await wait(sb);
          if (sb.buffered.length > 0) {
            const start = sb.buffered.start(0);
            const end = sb.buffered.end(sb.buffered.length - 1);
            if (end - start > 24) {
              sb.remove(start, end - 14);
              await wait(sb);
            }
          }
          sb.appendBuffer(value);
        }
      } catch {
        /* abort / quota */
      }
    })();
  }, []);

  const play = useCallback(async () => {
    const audio = audioRef.current;
    const url = cfg()?.streamUrl;
    if (!audio || !url) return;
    if (audio.getAttribute("src")) teardownStream();
    playingRef.current = true;
    try {
      try {
        await startMse(url);
      } catch {
        audio.crossOrigin = "anonymous";
        audio.src = url + (url.includes("?") ? "&" : "?") + "t=" + Date.now();
      }
      ensureGraph();
      if (ctxRef.current?.state === "suspended") await ctxRef.current.resume();
      await audio.play();
      setPlaying(true);
    } catch {
      playingRef.current = false;
      teardownStream();
      setPlaying(false);
    }
  }, [ensureGraph, startMse, teardownStream]);

  const stop = useCallback(() => {
    playingRef.current = false;
    teardownStream();
    void ctxRef.current?.suspend();
    setPlaying(false);
  }, [teardownStream]);

  const toggle = useCallback(() => {
    if (playingRef.current) stop();
    else void play();
  }, [play, stop]);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      const c = cfg();
      if (!c) return;
      try {
        const res = await fetch(c.nowUrl, { cache: "no-store" });
        if (res.ok) {
          const data = (await res.json()) as NowPayload;
          if (!cancelled) setNow(displayNow(data));
          return;
        }
      } catch {
        /* Icecast fallback */
      }
      if (!c.icecastStatusUrl) return;
      try {
        const res = await fetch(c.icecastStatusUrl, { cache: "no-store" });
        if (!res.ok) return;
        const json = (await res.json()) as IcecastStatus;
        const src = json.icestats?.source;
        const first = Array.isArray(src) ? src[0] : src;
        if (first && !cancelled) setNow(displayNow(first));
      } catch {
        /* keep last */
      }
    }
    void poll();
    const id = window.setInterval(() => void poll(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!now.duration || !now.startedAt) return;
    const id = window.setInterval(() => setClock((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [now.duration, now.startedAt]);

  useEffect(() => () => {
    playingRef.current = false;
    teardownStream();
    void ctxRef.current?.close();
  }, [teardownStream]);

  const remaining = remainingSeconds(now);

  return { audioRef, playing, analyser, now, remaining, play, stop, toggle };
}
