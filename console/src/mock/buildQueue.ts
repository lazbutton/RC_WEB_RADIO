import { nextAnchor, parisParts } from "../lib/parisClock";
import {
  CATALOG,
  CLOCKS,
  itemsByCart,
  itemsByCategory,
  itemsByKind,
  type CatalogItem,
  type ClockDef,
} from "./catalog";

export type QueueItem = {
  uid: string;
  item: CatalogItem;
  role: "seq" | "anchor";
  anchor?: ":20" | ":40";
};

let uidSeq = 0;

function nextUid(): string {
  uidSeq += 1;
  return `q${uidSeq}`;
}

function pick(list: CatalogItem[], index: number): CatalogItem {
  return list[index % list.length];
}

export function buildQueue(
  clock: ClockDef,
  now: Date,
  musicCursor = 0,
  jingleCursor = 0,
  pubCursor = 0,
): QueueItem[] {
  const musics = itemsByCategory("Rotation");
  const archives = itemsByCategory("Archives");
  const jingles = itemsByCart("Jingles NTR");
  const pubs = itemsByCart("Pubs");
  const items: QueueItem[] = [];
  let elapsed = 0;
  const target = 34 * 60;
  const upcoming = clock.anchors.length ? nextAnchor(now) : null;
  let insertedAnchor = !upcoming;
  let m = musicCursor;
  let j = jingleCursor;
  let p = pubCursor;
  let motifI = 0;

  const resolveStep = (step: ClockDef["motif"][number]): CatalogItem => {
    if (step.kind === "jingle") return pick(jingles, j++);
    if (step.kind === "pub") return pick(pubs, p++);
    if (step.kind === "son") return pick(itemsByKind("son"), m++);
    if (step.category === "Archives") return pick(archives, m++);
    return pick(musics, m++);
  };

  while (elapsed < target || items.length < 10) {
    if (!insertedAnchor && upcoming && elapsed + 12 >= upcoming.remainingSec) {
      items.push({
        uid: nextUid(),
        item: pick(pubs, p++),
        role: "anchor",
        anchor: upcoming.label,
      });
      elapsed += items[items.length - 1].item.durationSec;
      insertedAnchor = true;
      continue;
    }
    const step = clock.motif[motifI % clock.motif.length];
    motifI += 1;
    const item = resolveStep(step);
    items.push({ uid: nextUid(), item, role: "seq" });
    elapsed += item.durationSec;
    if (items.length > 40) break;
  }

  return items;
}

export function buildPastQueue(clock: ClockDef, count = 8): QueueItem[] {
  const musics = itemsByCategory("Rotation");
  const archives = itemsByCategory("Archives");
  const jingles = itemsByCart("Jingles NTR");
  const pubs = itemsByCart("Pubs");
  let m = musics.length - 1;
  let j = jingles.length - 1;
  let p = pubs.length - 1;
  const items: QueueItem[] = [];

  const resolveStep = (step: ClockDef["motif"][number]): CatalogItem => {
    if (step.kind === "jingle") {
      const item = pick(jingles, Math.max(0, j));
      j -= 1;
      return item;
    }
    if (step.kind === "pub") {
      const item = pick(pubs, Math.max(0, p));
      p -= 1;
      return item;
    }
    if (step.kind === "son") return pick(itemsByKind("son"), 0);
    if (step.category === "Archives") {
      const item = pick(archives, Math.max(0, m));
      m -= 1;
      return item;
    }
    const item = pick(musics, Math.max(0, m));
    m -= 1;
    return item;
  };

  for (let i = 0; i < count; i += 1) {
    const step = clock.motif[i % clock.motif.length];
    items.push({ uid: nextUid(), item: resolveStep(step), role: "seq" });
  }
  return items;
}

export function defaultClock(now: Date): ClockDef {
  const { hour } = parisParts(now);
  return hour >= 7 && hour < 19 ? CLOCKS[0] : CLOCKS[1];
}

export function findItem(id: string): CatalogItem | undefined {
  return CATALOG.find((i) => i.id === id);
}
