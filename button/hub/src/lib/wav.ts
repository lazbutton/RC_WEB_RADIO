type Timing = { keepS: number; maxS: number; startS: number };

const PCM_LAN: Timing = { keepS: 0.12, maxS: 0.2, startS: 0.04 };
/** Tailscale : file large, pas de trou à chaque jitter, burst Icecast ~3 s. */
const PCM_REMOTE: Timing = { keepS: 2.5, maxS: 6, startS: 1 };
const CHUNK_S = 0.04;
const RATE = 44100;

function timingForUrl(url: string): Timing {
  try {
    const host = new URL(url).hostname;
    if (
      host === "192.168.1.100" ||
      host === "nasgul" ||
      host === "localhost" ||
      host === "127.0.0.1"
    ) {
      return PCM_LAN;
    }
  } catch {
    /* proto */
  }
  return PCM_REMOTE;
}

export type WavHandle = {
  analyser: AnalyserNode;
  stop: () => void;
  duck: (gain: number, seconds: number) => void;
};

function skipRiff(bytes: Uint8Array): { rest: Uint8Array; header: boolean } | null {
  if (bytes.length < 4) return null;
  const tag = String.fromCharCode(bytes[0], bytes[1], bytes[2], bytes[3]);
  if (tag !== "RIFF") return { rest: bytes, header: false };
  const text = String.fromCharCode(...bytes.subarray(0, Math.min(bytes.length, 512)));
  const dataAt = text.indexOf("data");
  if (dataAt < 0) return null;
  return { rest: bytes.subarray(dataAt + 8), header: false };
}

function framesFromPcm(bytes: Uint8Array, count: number) {
  const copy = new Uint8Array(bytes.subarray(0, count * 4));
  const view = new DataView(copy.buffer);
  const left = new Float32Array(count);
  const right = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    left[i] = view.getInt16(i * 4, true) / 32768;
    right[i] = view.getInt16(i * 4 + 2, true) / 32768;
  }
  return { left, right };
}

export async function playStudioWav(
  url: string,
  signal: AbortSignal,
  onLag: (ms: number) => void,
  onError?: (message: string) => void,
): Promise<WavHandle> {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) throw new Error("audio");
  const timing = timingForUrl(url);
  const ctx = new Ctx({
    latencyHint: timing.keepS >= 1 ? "playback" : "interactive",
  });
  const dest = ctx.createGain();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 4096;
  analyser.smoothingTimeConstant = 0.7;
  analyser.minDecibels = -75;
  analyser.maxDecibels = -20;
  dest.connect(analyser);
  analyser.connect(ctx.destination);
  await ctx.resume().catch(() => undefined);
  const res = await fetch(url, { cache: "no-store", mode: "cors", signal });
  if (!res.ok || !res.body) throw new Error("flux");
  const reader = res.body.getReader();
  let leftover: Uint8Array = new Uint8Array(0);
  let playAt = 0;
  let header = true;
  let alive = true;
  const sources: AudioBufferSourceNode[] = [];
  const minFrames = Math.max(1024, Math.round(RATE * CHUNK_S));

  function clearSources() {
    for (const src of sources) {
      try {
        src.stop();
      } catch {
        /* already stopped */
      }
      try {
        src.disconnect();
      } catch {
        /* already disconnected */
      }
    }
    sources.length = 0;
  }

  const lagTimer = window.setInterval(() => {
    if (!alive || ctx.state !== "running") return;
    const queued = Math.max(0, playAt - ctx.currentTime);
    const device = (ctx.outputLatency || 0) + (ctx.baseLatency || 0);
    onLag((queued + device) * 1000);
  }, 250);

  function stop() {
    alive = false;
    window.clearInterval(lagTimer);
    clearSources();
    void reader.cancel().catch(() => undefined);
    void ctx.close().catch(() => undefined);
  }

  signal.addEventListener("abort", stop, { once: true });

  function duck(gain: number, seconds: number) {
    const now = ctx.currentTime;
    const dur = Math.max(0.05, seconds);
    dest.gain.cancelScheduledValues(now);
    dest.gain.setValueAtTime(dest.gain.value, now);
    dest.gain.linearRampToValueAtTime(Math.max(0, Math.min(1, gain)), now + dur);
  }

  function enqueue(left: Float32Array, right: Float32Array) {
    let frames = left.length;
    if (frames <= 0) return;
    const now = ctx.currentTime;
    let when = playAt;
    if (playAt <= 0) {
      when = now + timing.startS;
    } else if (when < now) {
      when = now;
    }
    const queued = Math.max(0, when - now);
    if (queued >= timing.maxS) return;
    const roomFrames = Math.floor((timing.maxS - queued) * RATE);
    if (frames > roomFrames) {
      const skip = frames - Math.max(0, roomFrames);
      if (skip >= frames) return;
      left = left.subarray(skip);
      right = right.subarray(skip);
      frames = left.length;
    }
    const buffer = ctx.createBuffer(2, frames, RATE);
    buffer.getChannelData(0).set(left);
    buffer.getChannelData(1).set(right);
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(dest);
    src.onended = () => {
      const i = sources.indexOf(src);
      if (i >= 0) sources.splice(i, 1);
    };
    src.start(when);
    sources.push(src);
    playAt = when + frames / RATE;
  }

  void (async () => {
    try {
      while (alive && !signal.aborted) {
        const { done, value } = await reader.read();
        if (done || !value?.byteLength) break;
        const next = new Uint8Array(leftover.length + value.length);
        next.set(leftover);
        next.set(value, leftover.length);
        leftover = next;
        if (header) {
          const skipped = skipRiff(leftover);
          if (!skipped) continue;
          leftover = skipped.rest;
          header = skipped.header;
        }
        if (ctx.state !== "running") {
          void ctx.resume().catch(() => undefined);
          continue;
        }
        const frames = Math.floor(leftover.length / 4);
        if (frames < minFrames) continue;
        const { left, right } = framesFromPcm(leftover, frames);
        leftover = leftover.subarray(frames * 4) as Uint8Array;
        enqueue(left, right);
      }
    } catch (err) {
      if (!signal.aborted) onError?.(err instanceof Error ? err.message : "coupé");
    }
  })();

  return { analyser, stop, duck };
}
