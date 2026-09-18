export type NowPlaying = {
  note: number;
  title: string;
  remaining: number;
  duration: number;
};

type Listener = (now: NowPlaying | null) => void;

type Voice = {
  note: number;
  title: string;
  duration: number;
  started: number;
  source: AudioBufferSourceNode;
};

let ctx: AudioContext | null = null;
const buffers = new Map<string, AudioBuffer>();
let voices: Voice[] = [];
const listeners = new Set<Listener>();

function remainingOf(voice: Voice): number {
  if (!ctx) return 0;
  return voice.duration - (ctx.currentTime - voice.started);
}

function emit(): void {
  const now = snapshot();
  for (const fn of listeners) fn(now);
}

export function isNotePlaying(note: number): boolean {
  return voices.some((voice) => voice.note === note && remainingOf(voice) > 0);
}

export function snapshot(): NowPlaying | null {
  const voice = voices[voices.length - 1];
  if (!voice || !ctx) return null;
  const remaining = remainingOf(voice);
  if (remaining <= 0) return null;
  return { note: voice.note, title: voice.title, remaining, duration: voice.duration };
}

export function subscribe(fn: Listener): () => void {
  listeners.add(fn);
  fn(snapshot());
  return () => {
    listeners.delete(fn);
  };
}

export async function unlockAudio(): Promise<void> {
  ctx ??= new AudioContext();
  if (ctx.state === "suspended") await ctx.resume();
}

export async function playSound(
  soundId: string,
  blob: Blob,
  note: number,
  title: string,
): Promise<void> {
  if (isNotePlaying(note)) return;
  await unlockAudio();
  if (!ctx) throw new Error("audio");
  if (isNotePlaying(note)) return;
  let buffer = buffers.get(soundId);
  if (!buffer) {
    buffer = await ctx.decodeAudioData(await blob.arrayBuffer());
    buffers.set(soundId, buffer);
  }
  if (isNotePlaying(note)) return;
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  const voice: Voice = {
    note,
    title,
    duration: buffer.duration,
    started: ctx.currentTime,
    source,
  };
  voices.push(voice);
  source.onended = () => {
    voices = voices.filter((row) => row !== voice);
    emit();
  };
  source.start();
  emit();
}

export function stopAll(): void {
  for (const voice of voices) {
    try {
      voice.source.stop();
    } catch {
      /* déjà stoppé */
    }
  }
  voices = [];
  emit();
}

export function forgetBuffer(soundId: string): void {
  buffers.delete(soundId);
}
