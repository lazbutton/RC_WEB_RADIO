import { isWavUrl } from "./format";

const NEEDLE_S = 2.6;

export type TapOk = {
  ok: true;
  br: number;
  bytes: number;
  burstBytes: number;
  prerollMs: number | null;
  wav: boolean;
  chunks: Uint8Array[];
};

export type TapFail = { ok: false; status?: number; error?: string };
export type Tap = TapOk | TapFail;

type TapOpts = {
  onPreroll?: (ms: number) => void;
  stopOnPreroll?: boolean;
};

function burstOf(points: { t: number; bytes: number }[], br: number) {
  const realtime = (br * 1000) / 8;
  let burstBytes = 0;
  let settled = false;
  for (let i = 0; i < points.length; i++) {
    if (i === 0) {
      burstBytes = points[i].bytes;
      continue;
    }
    const dt = (points[i].t - points[i - 1].t) / 1000;
    const db = points[i].bytes - points[i - 1].bytes;
    const rate = dt > 0.012 ? db / dt : Number.POSITIVE_INFINITY;
    if (rate < realtime * 2.2) {
      settled = true;
      break;
    }
    burstBytes = points[i].bytes;
  }
  return { burstBytes, settled };
}

export async function tapStream(
  url: string | undefined,
  seconds: number,
  opts: TapOpts = {},
): Promise<Tap> {
  if (!url) return { ok: false, status: 0 };
  const ctrl = new AbortController();
  const stopAt = performance.now() + seconds * 1000;
  const chunks: Uint8Array[] = [];
  let bytes = 0;
  let br = 128;
  let wav = false;
  let reported = false;
  const points: { t: number; bytes: number }[] = [];
  try {
    const res = await fetch(url, { cache: "no-store", mode: "cors", signal: ctrl.signal });
    if (!res.ok || !res.body) return { ok: false, status: res.status };
    const ctype = (res.headers.get("content-type") || "").toLowerCase();
    wav = ctype.includes("wav") || isWavUrl(url);
    br = wav ? 1411 : Number(res.headers.get("icy-br") || 128) || 128;
    const reader = res.body.getReader();
    try {
      while (performance.now() < stopAt) {
        const { done, value } = await reader.read();
        if (done || !value) break;
        chunks.push(value);
        bytes += value.byteLength;
        points.push({ t: performance.now(), bytes });
        const burst = burstOf(points, br);
        if (burst.settled && burst.burstBytes && !reported) {
          reported = true;
          opts.onPreroll?.(Math.round((burst.burstBytes * 8) / br));
          if (opts.stopOnPreroll) break;
        }
      }
    } finally {
      try {
        await reader.cancel();
      } catch {
        /* closed */
      }
    }
  } catch (err) {
    if (!chunks.length) return { ok: false, error: err instanceof Error ? err.message : "fetch" };
  } finally {
    ctrl.abort();
  }
  const burst = burstOf(points, br);
  const prerollMs = burst.burstBytes ? Math.round((burst.burstBytes * 8) / br) : null;
  if (!reported && prerollMs != null) opts.onPreroll?.(prerollMs);
  return {
    ok: true,
    br,
    bytes,
    burstBytes: burst.burstBytes,
    prerollMs,
    wav,
    chunks,
  };
}

function concatChunks(chunks: Uint8Array[]): ArrayBuffer {
  const total = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out.buffer;
}

function mp3Start(buf: ArrayBuffer): ArrayBuffer {
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length - 1; i++) {
    if (bytes[i] === 0xff && (bytes[i + 1] & 0xe0) === 0xe0) return buf.slice(i);
  }
  return buf;
}

type Decoded = { data: Float32Array; sr: number };

export async function decodeTap(tap: Tap): Promise<Decoded | null> {
  if (!tap.ok || !tap.chunks.length) return null;
  const raw = mp3Start(concatChunks(tap.chunks));
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return null;
  const ctx = new Ctx();
  try {
    const audio = await ctx.decodeAudioData(raw.slice(0));
    const length = audio.length;
    const channels = audio.numberOfChannels;
    const mixed = new Float32Array(length);
    for (let c = 0; c < channels; c++) {
      const data = audio.getChannelData(c);
      for (let i = 0; i < length; i++) mixed[i] += data[i];
    }
    if (channels > 1) {
      for (let i = 0; i < length; i++) mixed[i] /= channels;
    }
    const target = 8000;
    const step = audio.sampleRate / target;
    const count = Math.floor(mixed.length / step);
    const down = new Float32Array(count);
    for (let i = 0; i < count; i++) down[i] = mixed[Math.floor(i * step)];
    let mean = 0;
    for (let i = 0; i < count; i++) mean += down[i];
    mean /= count || 1;
    for (let i = 0; i < count; i++) down[i] -= mean;
    return { data: down, sr: target };
  } catch {
    return null;
  } finally {
    await ctx.close().catch(() => undefined);
  }
}

function nccAt(needle: Float32Array, hay: Float32Array, offset: number): number {
  let sum = 0;
  let energyA = 0;
  let energyB = 0;
  for (let i = 0; i < needle.length; i++) {
    const a = needle[i];
    const b = hay[offset + i];
    sum += a * b;
    energyA += a * a;
    energyB += b * b;
  }
  return sum / (Math.sqrt(energyA * energyB) || 1);
}

function rmsOf(buf: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  return Math.sqrt(sum / (buf.length || 1));
}

function findNeedle(needle: Float32Array, hay: Float32Array, sr: number) {
  const win = needle.length;
  if (hay.length <= win + 8) return null;
  const hop = Math.max(1, Math.round(sr * 0.015));
  let best = -1;
  let lag = 0;
  for (let i = 0; i + win < hay.length; i += hop) {
    const score = nccAt(needle, hay, i);
    if (score > best) {
      best = score;
      lag = i;
    }
  }
  const lo = Math.max(0, lag - hop);
  const hi = Math.min(hay.length - win, lag + hop);
  for (let i = lo; i <= hi; i++) {
    const score = nccAt(needle, hay, i);
    if (score > best) {
      best = score;
      lag = i;
    }
  }
  return { ms: (lag / sr) * 1000, conf: best };
}

export function delayBehind(reference: Decoded, other: Decoded): { ms: number; conf: number } | null {
  const sr = reference.sr;
  const skip = Math.round(sr * 0.35);
  const win = Math.round(sr * NEEDLE_S);
  if (reference.data.length < skip + win || other.data.length < skip + win) return null;
  const needleA = reference.data.subarray(skip, skip + win);
  const needleB = other.data.subarray(skip, skip + win);
  if (rmsOf(needleA) < 0.008 || rmsOf(needleB) < 0.008) return null;
  const aInB = findNeedle(needleA, other.data, sr);
  const bInA = findNeedle(needleB, reference.data, sr);
  const useA = Boolean(aInB && aInB.conf >= 0.35);
  const useB = Boolean(bInA && bInA.conf >= 0.35);
  if (useA && aInB && (!useB || !bInA || aInB.conf >= bInA.conf)) {
    return { ms: Math.round(aInB.ms), conf: aInB.conf };
  }
  if (useB && bInA) return { ms: -Math.round(bInA.ms), conf: bInA.conf };
  return null;
}
