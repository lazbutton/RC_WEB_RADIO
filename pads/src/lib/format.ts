export function formatMmSs(totalSec: number): string {
  const sec = Math.max(0, Math.round(totalSec));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function splitLabel(raw: string): { lead: string; rest: string | null } {
  const dash = raw.indexOf(" - ");
  const em = raw.indexOf(" — ");
  const cut = dash >= 0 && (em < 0 || dash < em) ? dash : em;
  if (cut <= 0) return { lead: raw, rest: null };
  const lead = raw.slice(0, cut).trim();
  const rest = raw.slice(cut + 3).trim();
  if (!lead || !rest) return { lead: raw, rest: null };
  return { lead, rest };
}

