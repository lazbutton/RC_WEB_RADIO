const KEY = "button-hub-listeners";
const MAX = 72;

export function loadListenerHistory(): number[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((n): n is number => typeof n === "number").slice(-MAX);
  } catch {
    return [];
  }
}

export function pushListener(history: number[], value: number): number[] {
  const next = [...history, value].slice(-MAX);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* quota */
  }
  return next;
}
