export function pickRandomExcept(ids: string[], last: string | null): string | null {
  if (!ids.length) return null;
  if (ids.length === 1) return ids[0] ?? null;
  const pool = last && ids.includes(last) ? ids.filter((id) => id !== last) : ids;
  const bag = pool.length ? pool : ids;
  const index = Math.floor(Math.random() * bag.length);
  return bag[index] ?? null;
}
