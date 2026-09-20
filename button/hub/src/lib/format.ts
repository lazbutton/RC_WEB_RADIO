export const HARBOR_MIN_MS = 5000;
export const HARBOR_MIN_S = 5;
export const HARBOR_MAX_S = 30;
export const TAP_PRE_S = 1.8;
export const TAP_CORR_S = 16;
export const META_DEAD_S = 8;

export function fmtMs(ms: number): string {
  const rounded = Math.round(ms);
  const sign = rounded < 0 ? "−" : "";
  const abs = Math.abs(rounded);
  if (abs < 1000) return `${sign}${abs} ms`;
  const s = Math.floor(abs / 1000);
  const rem = abs % 1000;
  if (rem === 0) return `${sign}${s} s`;
  return `${sign}${s} s ${String(rem).padStart(3, "0")} ms`;
}

export function clockLabel(date = new Date()): string {
  return date.toLocaleTimeString("fr-FR", {
    timeZone: "Europe/Paris",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function sourceLabel(source: string | undefined): string {
  const value = (source || "").toLowerCase();
  if (/live|stream|harbor/.test(value)) return "live";
  if (value.includes("jingle")) return "jingle";
  if (value.includes("cart")) return "cart";
  if (/auto|dj/.test(value)) return "auto-DJ";
  return "antenne";
}

export function sinceLabel(iso: string | undefined): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${String(m % 60).padStart(2, "0")}`;
}

export function ageSeconds(iso: string | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  return Math.max(0, (Date.now() - t) / 1000);
}

export function fmtMmSs(totalSec: number, round: "floor" | "ceil" = "floor"): string {
  const n = Math.max(0, round === "ceil" ? Math.ceil(totalSec) : Math.floor(totalSec));
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
}

export function fmtSec(value: number): string {
  if (value < 10) return `${value.toFixed(1).replace(".", ",")} s`;
  return `${Math.round(value)} s`;
}

export function isWavUrl(url: string): boolean {
  try {
    return /\.wav$/i.test(new URL(url, location.origin).pathname);
  } catch {
    return /\.wav(\?|$)/i.test(url || "");
  }
}
