import { useEffect, useRef } from "react";
import type { WaveMode } from "./lib/wave";

type Props = {
  analyser: AnalyserNode | null;
  playing: boolean;
  mode: WaveMode;
  className?: string;
};

const WHITE = "245, 245, 247";
const COLS = 96;
const MIN_HZ = 20;
const MAX_HZ = 12000;
const DECAY = 0.82;
const SILENCE = 0.03;

function hzAt(t: number) {
  return MIN_HZ * (MAX_HZ / MIN_HZ) ** t;
}

function binAt(hz: number, sampleRate: number, fftSize: number, bins: number) {
  return Math.min(bins - 1, Math.max(1, Math.round((hz * fftSize) / sampleRate)));
}

function columnEnergy(freq: Uint8Array, sampleRate: number, fftSize: number, t: number) {
  const bins = freq.length;
  const lo = binAt(hzAt(Math.max(0, t - 0.5 / COLS)), sampleRate, fftSize, bins);
  const hi = binAt(hzAt(Math.min(1, t + 0.5 / COLS)), sampleRate, fftSize, bins);
  let peak = 0;
  for (let i = lo; i <= hi; i++) peak = Math.max(peak, freq[i] / 255);
  return peak;
}

function mirroredSpectrum(bars: Float32Array, i: number) {
  const last = bars.length - 1;
  const dist = Math.abs(i - last / 2) / (last / 2);
  return bars[Math.round(dist * last)];
}

function findTrigger(data: Uint8Array) {
  const mid = 128;
  const last = Math.floor(data.length / 2);
  for (let i = 1; i < last; i++) {
    if (data[i - 1] < mid && data[i] >= mid) return i;
  }
  return 0;
}

function strokeTime(
  g: CanvasRenderingContext2D,
  data: Uint8Array,
  start: number,
  count: number,
  cssW: number,
  cssH: number,
  alpha: number,
  width: number,
) {
  const mid = cssH / 2;
  g.beginPath();
  for (let i = 0; i < count; i++) {
    const x = (i / Math.max(1, count - 1)) * cssW;
    const y = ((data[start + i] - 128) / 128) * (mid - 4) + mid;
    if (i === 0) g.moveTo(x, y);
    else g.lineTo(x, y);
  }
  g.strokeStyle = `rgba(${WHITE}, ${alpha})`;
  g.lineWidth = width;
  g.lineJoin = "round";
  g.lineCap = "round";
  g.stroke();
}

export function Oscilloscope({ analyser, playing, mode, className }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const freqData = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const timeData = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const shown = useRef<Float32Array>(new Float32Array(COLS));
  const echoes = useRef<Uint8Array[]>([]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const surface = canvas;
    const g = ctx;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    let raf = 0;
    let cssW = 0;
    let cssH = 0;

    function clear() {
      g.clearRect(0, 0, cssW, cssH);
    }

    function drawFlat() {
      clear();
      g.beginPath();
      g.moveTo(0, cssH / 2);
      g.lineTo(cssW, cssH / 2);
      g.strokeStyle = `rgba(${WHITE}, 0.28)`;
      g.lineWidth = 1.25;
      g.lineCap = "round";
      g.stroke();
    }

    function pullFreq() {
      if (!analyser) return null;
      const bins = analyser.frequencyBinCount;
      if (!freqData.current || freqData.current.length !== bins) {
        freqData.current = new Uint8Array(new ArrayBuffer(bins));
      }
      analyser.getByteFrequencyData(freqData.current as never);
      return freqData.current;
    }

    function pullTime() {
      if (!analyser) return null;
      const fft = analyser.fftSize;
      if (!timeData.current || timeData.current.length !== fft) {
        timeData.current = new Uint8Array(new ArrayBuffer(fft));
      }
      analyser.getByteTimeDomainData(timeData.current as never);
      return timeData.current;
    }

    function updateBars(freq: Uint8Array) {
      const rate = analyser!.context.sampleRate;
      const fft = analyser!.fftSize;
      const bars = shown.current;
      let peak = 0;
      for (let i = 0; i < COLS; i++) {
        const raw = columnEnergy(freq, rate, fft, i / (COLS - 1));
        bars[i] = Math.max(raw, bars[i] * DECAY);
        peak = Math.max(peak, bars[i]);
      }
      return peak;
    }

    function drawLigne() {
      const data = pullTime();
      if (!data) return;
      const start = findTrigger(data);
      const count = data.length - start;
      clear();
      strokeTime(g, data, start, count, cssW, cssH, 0.18, 8);
      strokeTime(g, data, start, count, cssW, cssH, 0.92, 1.5);
    }

    function drawSpectre() {
      const freq = pullFreq();
      if (!freq) return;
      const peak = updateBars(freq);
      clear();
      if (peak < SILENCE) return;
      const mid = cssH / 2;
      const gap = 1.25;
      const maxH = mid - 6;
      const step = cssW / COLS;
      const width = Math.max(1.4, step * 0.55);
      g.lineCap = "round";
      for (let i = 0; i < COLS; i++) {
        const v = mirroredSpectrum(shown.current, i);
        const h = v * maxH;
        if (h < 1.5) continue;
        const x = (i + 0.5) * step;
        g.strokeStyle = `rgba(${WHITE}, ${0.22 + v * 0.28})`;
        g.lineWidth = width + 2.4;
        g.beginPath();
        g.moveTo(x, mid - gap - h);
        g.lineTo(x, mid - gap);
        g.moveTo(x, mid + gap);
        g.lineTo(x, mid + gap + h);
        g.stroke();
        g.strokeStyle = `rgba(${WHITE}, ${0.55 + v * 0.4})`;
        g.lineWidth = width;
        g.beginPath();
        g.moveTo(x, mid - gap - h);
        g.lineTo(x, mid - gap);
        g.moveTo(x, mid + gap);
        g.lineTo(x, mid + gap + h);
        g.stroke();
      }
    }

    function drawNappe() {
      const freq = pullFreq();
      if (!freq) return;
      const peak = updateBars(freq);
      clear();
      if (peak < SILENCE) return;
      const mid = cssH / 2;
      const gap = 1.5;
      const maxH = mid - 8;
      const bars = shown.current;
      const strokeNappe = (sign: 1 | -1) => {
        g.beginPath();
        g.moveTo(0, mid + sign * gap);
        for (let i = 0; i < COLS; i++) {
          g.lineTo((i / (COLS - 1)) * cssW, mid + sign * (gap + mirroredSpectrum(bars, i) * maxH));
        }
        g.lineTo(cssW, mid + sign * gap);
        g.closePath();
        g.fillStyle = `rgba(${WHITE}, 0.14)`;
        g.fill();
        g.beginPath();
        for (let i = 0; i < COLS; i++) {
          const x = (i / (COLS - 1)) * cssW;
          const y = mid + sign * (gap + mirroredSpectrum(bars, i) * maxH);
          if (i === 0) g.moveTo(x, y);
          else g.lineTo(x, y);
        }
        g.strokeStyle = `rgba(${WHITE}, 0.82)`;
        g.lineWidth = 1.35;
        g.stroke();
      };
      strokeNappe(-1);
      strokeNappe(1);
    }

    function drawPolar() {
      const freq = pullFreq();
      if (!freq) return;
      const peak = updateBars(freq);
      clear();
      if (peak < SILENCE) return;
      const cx = cssW / 2;
      const cy = cssH / 2;
      const inner = Math.min(cssW, cssH) * 0.16;
      const span = Math.min(cssW, cssH) * 0.34;
      g.beginPath();
      for (let i = 0; i <= COLS; i++) {
        const t = i / COLS;
        const a = t * Math.PI * 2 - Math.PI / 2;
        const r = inner + shown.current[i % COLS] * span;
        const x = cx + Math.cos(a) * r;
        const y = cy + Math.sin(a) * r;
        if (i === 0) g.moveTo(x, y);
        else g.lineTo(x, y);
      }
      g.closePath();
      g.moveTo(cx + inner, cy);
      g.arc(cx, cy, inner, 0, Math.PI * 2, true);
      g.fillStyle = `rgba(${WHITE}, 0.1)`;
      g.fill("evenodd");
      g.beginPath();
      for (let i = 0; i <= COLS; i++) {
        const t = i / COLS;
        const a = t * Math.PI * 2 - Math.PI / 2;
        const r = inner + shown.current[i % COLS] * span;
        const x = cx + Math.cos(a) * r;
        const y = cy + Math.sin(a) * r;
        if (i === 0) g.moveTo(x, y);
        else g.lineTo(x, y);
      }
      g.closePath();
      g.strokeStyle = `rgba(${WHITE}, 0.88)`;
      g.lineWidth = 1.4;
      g.stroke();
    }

    function drawPoints() {
      const freq = pullFreq();
      if (!freq) return;
      const peak = updateBars(freq);
      clear();
      if (peak < SILENCE) return;
      const mid = cssH / 2;
      const maxH = mid - 8;
      g.fillStyle = `rgba(${WHITE}, 0.88)`;
      for (let i = 0; i < COLS; i++) {
        const v = mirroredSpectrum(shown.current, i);
        if (v < 0.03) continue;
        const x = ((i + 0.5) / COLS) * cssW;
        const n = 4 + Math.round(v * 14);
        const r = 0.42 + v * 0.85;
        for (let k = 0; k < n; k++) {
          const t = (k + 1) / (n + 1);
          const h = v * maxH * t;
          g.beginPath();
          g.arc(x, mid - h, r, 0, Math.PI * 2);
          g.arc(x, mid + h, r, 0, Math.PI * 2);
          g.fill();
        }
      }
    }

    function drawEcho() {
      const data = pullTime();
      if (!data) return;
      const snap = new Uint8Array(data);
      const hist = echoes.current;
      hist.push(snap);
      if (hist.length > 16) hist.shift();
      clear();
      const layers = [
        { offset: 12, alpha: 0.2, width: 1.1 },
        { offset: 6, alpha: 0.45, width: 1.3 },
        { offset: 1, alpha: 0.9, width: 1.6 },
      ];
      for (const layer of layers) {
        const frame = hist[hist.length - layer.offset];
        if (!frame) continue;
        const start = findTrigger(frame);
        strokeTime(g, frame, start, frame.length - start, cssW, cssH, layer.alpha, layer.width);
      }
    }

    function drawWave() {
      if (mode === "ligne") drawLigne();
      else if (mode === "nappe") drawNappe();
      else if (mode === "polar") drawPolar();
      else if (mode === "points") drawPoints();
      else if (mode === "echo") drawEcho();
      else drawSpectre();
    }

    function resize() {
      const dpr = window.devicePixelRatio || 1;
      cssW = surface.clientWidth;
      cssH = surface.clientHeight;
      surface.width = Math.max(1, Math.floor(cssW * dpr));
      surface.height = Math.max(1, Math.floor(cssH * dpr));
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (reduce.matches) clear();
      else if (!playing || !analyser) drawFlat();
    }

    function tick() {
      raf = 0;
      if (reduce.matches) clear();
      else if (!playing || !analyser) drawFlat();
      else drawWave();
      raf = requestAnimationFrame(tick);
    }

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(surface);
    raf = requestAnimationFrame(tick);
    return () => {
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, [analyser, playing, mode]);

  return <canvas ref={canvasRef} className={className} width={1600} height={420} aria-hidden="true" />;
}
