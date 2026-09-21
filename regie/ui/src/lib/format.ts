import { displayName } from "./threads";

export function initials(name: string): string {
  const parts = name
    .replace(/[^\p{L}\p{N}\s.-]/gu, " ")
    .trim()
    .split(/[\s.-]+/)
    .filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

const TEAM_CHIP: Record<string, string> = {
  volontairelocal: "Lou",
  "lou martinat": "Lou",
  lou: "Lou",
  "viviane berreur": "Viviane",
  viviane: "Viviane",
  "erwann cochery": "Erwann",
  erwann: "Erwann",
};

export function chipName(name: string): string {
  const shown = displayName(name);
  const known = TEAM_CHIP[shown.toLocaleLowerCase("fr")];
  if (known) return known;
  return shown.split(/\s+/)[0] || shown;
}

export function formatSize(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} ko`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
}

export function relativeShort(rel: string): string {
  const match = /\(([^)]+)\)\s*$/.exec(rel || "");
  return match ? match[1] : rel;
}

export function absoluteStamp(rel: string): string {
  return (rel || "").replace(/\s*\([^)]*\)\s*$/, "");
}

export function agoLabel(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "jamais";
  const stamp = new Date(iso).getTime();
  if (Number.isNaN(stamp)) return "";
  const diff = Math.max(0, Math.round((now - stamp) / 1000));
  if (diff < 45) return "à l’instant";
  if (diff < 3600) return `il y a ${Math.round(diff / 60)} min`;
  if (diff < 86400) return `il y a ${Math.round(diff / 3600)} h`;
  return `il y a ${Math.round(diff / 86400)} j`;
}

export function folderShort(folder: string): string {
  if (!folder || folder === "INBOX") return "Boîte";
  return folder.split("/").pop() || folder;
}

export function catClass(category: string): string {
  if (category === "todo" || category === "waiting" || category === "read" || category === "newsletters" || category === "spam") {
    return `is-${category}`;
  }
  return "";
}
