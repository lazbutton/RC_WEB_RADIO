const KEY = "hub.wave";

export const WAVE_MODES = [
  { id: "ligne", label: "Ligne" },
  { id: "spectre", label: "Spectre" },
  { id: "nappe", label: "Nappe" },
  { id: "polar", label: "Polar" },
  { id: "points", label: "Points" },
  { id: "echo", label: "Echo" },
] as const;

export type WaveMode = (typeof WAVE_MODES)[number]["id"];

const IDS = new Set<string>(WAVE_MODES.map((row) => row.id));

export function loadWave(): WaveMode {
  const raw = localStorage.getItem(KEY) || "";
  return IDS.has(raw) ? (raw as WaveMode) : "spectre";
}

export function storeWave(mode: WaveMode): WaveMode {
  localStorage.setItem(KEY, mode);
  return mode;
}

export function waveLabel(mode: WaveMode): string {
  return WAVE_MODES.find((row) => row.id === mode)?.label || "Spectre";
}
