import type { CategoryId, LibraryCategory, PadColor } from "../types";

/** APC Mini MK2 — `apc` = velocity hardware. `hex` = UI web (plus douce, même famille). */
export const PAD_COLORS = [
  { id: "red", label: "Rouge", hex: "#e45b62", apc: 5 },
  { id: "orange", label: "Orange", hex: "#e8884a", apc: 9 },
  { id: "amber", label: "Ambre", hex: "#e0ae58", apc: 96 },
  { id: "yellow", label: "Jaune", hex: "#dcc85a", apc: 13 },
  { id: "lime", label: "Lime", hex: "#9ec85a", apc: 17 },
  { id: "green", label: "Vert", hex: "#4fc47a", apc: 21 },
  { id: "teal", label: "Turquoise", hex: "#3cc4a8", apc: 33 },
  { id: "ice", label: "Glace", hex: "#62b8e0", apc: 37 },
  { id: "blue", label: "Bleu", hex: "#5a7ee8", apc: 45 },
  { id: "violet", label: "Violet", hex: "#8b72e8", apc: 49 },
  { id: "pink", label: "Rose", hex: "#e05a88", apc: 57 },
  { id: "white", label: "Blanc", hex: "#efe8df", apc: 3 },
] as const satisfies readonly { id: PadColor; label: string; hex: string; apc: number }[];

const COLOR_IDS = new Set<string>(PAD_COLORS.map((row) => row.id));

export const MUSIC_ID = "son";
export const JINGLE_ID = "jingle";
export const PUB_ID = "pub";

export const SEED_CATEGORIES: LibraryCategory[] = [
  { id: MUSIC_ID, title: "Music", color: "amber", order: 0 },
  { id: JINGLE_ID, title: "Jingles", color: "red", order: 1 },
  { id: PUB_ID, title: "Pubs", color: "orange", order: 2 },
];

export function isPadColor(value: unknown): value is PadColor {
  return typeof value === "string" && COLOR_IDS.has(value);
}

export function colorFromKind(kind: CategoryId): PadColor {
  if (kind === JINGLE_ID) return "red";
  if (kind === PUB_ID) return "orange";
  if (kind === MUSIC_ID) return "amber";
  return "amber";
}

export function colorFromCategory(categories: LibraryCategory[], id: CategoryId): PadColor {
  return categories.find((row) => row.id === id)?.color ?? colorFromKind(id);
}

export function nextCategoryColor(used: PadColor[]): PadColor {
  const fresh = PAD_COLORS.find((row) => !used.includes(row.id));
  if (fresh) return fresh.id;
  return PAD_COLORS[used.length % PAD_COLORS.length]?.id ?? "amber";
}

export function apcVelocity(color: PadColor): number {
  return PAD_COLORS.find((row) => row.id === color)?.apc ?? 8;
}
