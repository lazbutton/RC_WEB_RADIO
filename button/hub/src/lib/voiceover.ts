import { useCallback, useEffect, useRef, useState } from "react";

const FADE_KEY = "hub.fadeS";
const FADE_MIN = 0.4;
const FADE_MAX = 4;
const FADE_STEP = 0.2;
const FADE_DEFAULT = 1.2;
const DUCK = 0.18;
const TARGET_RATE = 48000;
const PACKET = Math.round(TARGET_RATE * 0.02);

const WORKLET = `
class PcmTap extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = new Float32Array(0);
    this._from = sampleRate;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch || !ch.length) return true;
    const merged = new Float32Array(this._buf.length + ch.length);
    merged.set(this._buf);
    merged.set(ch, this._buf.length);
    this._buf = merged;
    const ratio = this._from / ${TARGET_RATE};
    const need = Math.ceil(${PACKET} * ratio);
    while (this._buf.length >= need) {
      const pcm = new Int16Array(${PACKET});
      for (let i = 0; i < ${PACKET}; i++) {
        const src = i * ratio;
        const i0 = Math.min(this._buf.length - 1, Math.floor(src));
        const i1 = Math.min(this._buf.length - 1, i0 + 1);
        const t = src - i0;
        const x = this._buf[i0] * (1 - t) + this._buf[i1] * t;
        const y = Math.max(-1, Math.min(1, x));
        pcm[i] = y < 0 ? y * 0x8000 : y * 0x7fff;
      }
      const used = Math.min(this._buf.length, Math.floor(${PACKET} * ratio));
      this._buf = this._buf.subarray(used);
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}
registerProcessor("pcm-tap", PcmTap);
`;

export function loadFade(): number {
  const raw = Number(localStorage.getItem(FADE_KEY));
  if (!Number.isFinite(raw)) return FADE_DEFAULT;
  return Math.min(FADE_MAX, Math.max(FADE_MIN, raw));
}

export function storeFade(value: number): number {
  const next = Math.min(FADE_MAX, Math.max(FADE_MIN, Math.round(value * 10) / 10));
  localStorage.setItem(FADE_KEY, String(next));
  return next;
}

export function bumpFade(value: number, dir: -1 | 1): number {
  return storeFade(value + dir * FADE_STEP);
}

export type VoiceoverStatus = {
  on?: boolean;
  fade?: number;
  remaining?: number;
  gain?: number;
  mic?: boolean;
  error?: string;
};

function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/probe/voiceover/pcm`;
}

async function voiceoverHttp(method: "GET" | "POST", body?: { on: boolean; fade: number }): Promise<VoiceoverStatus> {
  const response = await fetch("/probe/voiceover", {
    method,
    cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let payload: VoiceoverStatus = {};
  try {
    payload = (await response.json()) as VoiceoverStatus;
  } catch {
    payload = {};
  }
  if (response.status === 409) {
    const reason = payload.error === "harbor" ? "harbor en onde" : "trop près du suivant";
    throw new Error(reason);
  }
  if (!response.ok) {
    throw new Error(payload.error || `voix ${response.status}`);
  }
  return payload;
}

export type VoiceoverHandle = {
  analyser: AnalyserNode;
  stop: () => Promise<void>;
};

export async function startVoiceover(fade: number, duckCasque: (gain: number, seconds: number) => void): Promise<VoiceoverHandle> {
  if (!window.isSecureContext) {
    throw new Error("HTTPS ou localhost pour le micro");
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
  });
  const ctx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: "interactive", sampleRate: TARGET_RATE });
  let socket: WebSocket | null = null;
  try {
    await ctx.resume().catch(() => undefined);
    const blob = URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
    await ctx.audioWorklet.addModule(blob);
    URL.revokeObjectURL(blob);
    const source = ctx.createMediaStreamSource(stream);
    const sidetone = ctx.createGain();
    sidetone.gain.value = 0.55;
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    const tap = new AudioWorkletNode(ctx, "pcm-tap");
    source.connect(tap);
    source.connect(analyser);
    source.connect(sidetone).connect(ctx.destination);

    await voiceoverHttp("POST", { on: true, fade });
    duckCasque(DUCK, fade);

    socket = new WebSocket(wsUrl());
    socket.binaryType = "arraybuffer";
    const opened = socket;
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(() => reject(new Error("micro coupé")), 4000);
      opened.onopen = () => {
        window.clearTimeout(timer);
        resolve();
      };
      opened.onerror = () => {
        window.clearTimeout(timer);
        reject(new Error("micro coupé"));
      };
    });
    tap.port.onmessage = (event) => {
      if (opened.readyState === WebSocket.OPEN) opened.send(event.data);
    };

    let stopped = false;
    return {
      analyser,
      stop: async () => {
        if (stopped) return;
        stopped = true;
        tap.port.onmessage = null;
        opened.close();
        stream.getTracks().forEach((track) => track.stop());
        duckCasque(1, fade);
        await voiceoverHttp("POST", { on: false, fade }).catch(() => undefined);
        await ctx.close().catch(() => undefined);
      },
    };
  } catch (err) {
    stream.getTracks().forEach((track) => track.stop());
    socket?.close();
    duckCasque(1, fade);
    await voiceoverHttp("POST", { on: false, fade }).catch(() => undefined);
    await ctx.close().catch(() => undefined);
    throw err;
  }
}

export { DUCK, FADE_DEFAULT };

export function useVoiceover(duckCasque: (gain: number, seconds: number) => void) {
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fade, setFadeState] = useState(loadFade);
  const [level, setLevel] = useState(0);
  const handle = useRef<VoiceoverHandle | null>(null);
  const duckRef = useRef(duckCasque);
  duckRef.current = duckCasque;

  useEffect(() => {
    if (!on || !handle.current) {
      setLevel(0);
      return;
    }
    const analyser = handle.current.analyser;
    const data = new Uint8Array(analyser.fftSize) as Uint8Array;
    let frame = 0;
    const tick = () => {
      analyser.getByteTimeDomainData(data as never);
      let sum = 0;
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128;
        sum += v * v;
      }
      setLevel(Math.min(1, Math.sqrt(sum / data.length) * 4));
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [on]);

  const setFade = useCallback((dir: -1 | 1) => {
    setFadeState((prev) => bumpFade(prev, dir));
  }, []);

  const toggle = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (handle.current) {
        await handle.current.stop();
        handle.current = null;
        setOn(false);
        return;
      }
      handle.current = await startVoiceover(fade, (gain, seconds) => duckRef.current(gain, seconds));
      setOn(true);
    } catch (err) {
      handle.current = null;
      setOn(false);
      setError(err instanceof Error ? err.message : "échec");
    } finally {
      setBusy(false);
    }
  }, [busy, fade]);

  useEffect(() => {
    return () => {
      void handle.current?.stop();
    };
  }, []);

  return { on, busy, error, fade, level, toggle, setFade };
}
