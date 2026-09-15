import { useEffect, useRef } from "react";

type Props = {
  analyser: AnalyserNode | null;
  playing: boolean;
};

function yellow(alpha: number) {
  const raw =
    getComputedStyle(document.documentElement).getPropertyValue("--yellow").trim() ||
    "#f0e24a";
  if (alpha >= 1) return raw;
  const r = parseInt(raw.slice(1, 3), 16);
  const g = parseInt(raw.slice(3, 5), 16);
  const b = parseInt(raw.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function findTrigger(data: Uint8Array) {
  const mid = 128;
  const last = Math.floor(data.length / 2);
  for (let i = 1; i < last; i++) {
    if (data[i - 1] < mid && data[i] >= mid) return i;
  }
  return 0;
}

function strokePath(
  g: CanvasRenderingContext2D,
  data: Uint8Array,
  start: number,
  count: number,
  cssW: number,
  cssH: number,
) {
  g.beginPath();
  for (let i = 0; i < count; i++) {
    const x = (i / Math.max(1, count - 1)) * cssW;
    const y = (data[start + i] / 255) * cssH;
    if (i === 0) g.moveTo(x, y);
    else g.lineTo(x, y);
  }
}

export function Oscilloscope({ analyser, playing }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const timeData = useRef<Uint8Array<ArrayBuffer> | null>(null);

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

    function resize() {
      const dpr = window.devicePixelRatio || 1;
      cssW = surface.clientWidth;
      cssH = surface.clientHeight;
      surface.width = Math.max(1, Math.floor(cssW * dpr));
      surface.height = Math.max(1, Math.floor(cssH * dpr));
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (!playing || !analyser || reduce.matches) drawFlat();
    }

    function drawFlat() {
      g.clearRect(0, 0, cssW, cssH);
      g.beginPath();
      g.moveTo(0, cssH / 2);
      g.lineTo(cssW, cssH / 2);
      g.strokeStyle = yellow(0.82);
      g.lineWidth = 1.75;
      g.lineCap = "round";
      g.stroke();
    }

    function drawWave() {
      if (!analyser) return;
      if (!timeData.current || timeData.current.length !== analyser.fftSize) {
        timeData.current = new Uint8Array(new ArrayBuffer(analyser.fftSize));
      }
      const data = timeData.current;
      analyser.getByteTimeDomainData(data);
      const start = findTrigger(data);
      const count = data.length - start;
      g.clearRect(0, 0, cssW, cssH);
      g.lineJoin = "round";
      g.lineCap = "round";
      strokePath(g, data, start, count, cssW, cssH);
      g.strokeStyle = yellow(0.28);
      g.lineWidth = 6;
      g.stroke();
      strokePath(g, data, start, count, cssW, cssH);
      g.strokeStyle = yellow(1);
      g.lineWidth = 1.75;
      g.stroke();
    }

    function tick() {
      raf = 0;
      if (!playing || !analyser || reduce.matches) drawFlat();
      else drawWave();
      raf = requestAnimationFrame(tick);
    }

    resize();
    drawFlat();
    const ro = new ResizeObserver(resize);
    ro.observe(surface);
    if (playing && analyser && !reduce.matches) {
      raf = requestAnimationFrame(tick);
    }
    return () => {
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, [analyser, playing]);

  return <canvas ref={canvasRef} className="wave" width={1200} height={160} aria-hidden="true" />;
}
