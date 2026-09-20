const KEEP = 0.035;
const MAX = 0.07;
const START = 0.008;

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
  const ctx = new Ctx({ latencyHint: "interactive", sampleRate: 44100 });
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

  const lagTimer = window.setInterval(() => {
    if (!alive || ctx.state !== "running") return;
    const queued = Math.max(0, playAt - ctx.currentTime);
    const device = (ctx.outputLatency || 0) + (ctx.baseLatency || 0);
    onLag((queued + device) * 1000);
  }, 250);

  function stop() {
    alive = false;
    window.clearInterval(lagTimer);
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
        const frames = Math.floor(leftover.length / 4);
        if (frames <= 0 || ctx.state !== "running") continue;
        const { left, right } = framesFromPcm(leftover, frames);
        leftover = leftover.subarray(frames * 4) as Uint8Array;
        const buffer = ctx.createBuffer(2, frames, 44100);
        buffer.getChannelData(0).set(left);
        buffer.getChannelData(1).set(right);
        const src = ctx.createBufferSource();
        src.buffer = buffer;
        src.connect(dest);
        const now = ctx.currentTime;
        let when = playAt;
        if (when < now + START) when = now + START;
        const duration = frames / 44100;
        let offset = 0;
        if (when - now + duration > MAX) {
          offset = when - now + duration - KEEP;
          if (offset >= duration) continue;
          when = now + START;
        }
        src.start(when, offset);
        playAt = when + (duration - offset);
      }
    } catch (err) {
      if (!signal.aborted) onError?.(err instanceof Error ? err.message : "coupé");
    }
  })();

  return { analyser, stop, duck };
}
