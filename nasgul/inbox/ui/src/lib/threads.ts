import type { MailAttachment, MailItem } from "../api";

const PREFIX = /^(re|fw|fwd|tr|enc|aw|sv)\s*:\s*/i;
const URGENCY = ["todo", "waiting", "read", "newsletters", "spam"];

export type Piece = MailAttachment & { itemId: number };

export type Thread = {
  key: string;
  title: string;
  items: MailItem[];
  latest: MailItem;
  category: string;
  people: string[];
  recap: string;
  pieces: Piece[];
  unseen: boolean;
  flagged: boolean;
  notionUrl: string;
  ids: number[];
};

export function notionPieces(pieces: Piece[]): Piece[] {
  return pieces.filter((row) => row.kind === "pdf" || row.kind === "image" || row.kind === "audio");
}

export function threadKey(subject: string): string {
  let text = (subject || "").trim();
  while (true) {
    const next = text.replace(PREFIX, "").trim();
    if (next === text) break;
    text = next;
  }
  return text.replace(/\s+/g, " ").toLocaleLowerCase("fr");
}

export function threadTitle(subject: string): string {
  let text = (subject || "").trim() || "(sans objet)";
  while (true) {
    const next = text.replace(PREFIX, "").trim();
    if (next === text) break;
    text = next;
  }
  return text || "(sans objet)";
}

export function threadCategory(items: MailItem[]): string {
  for (const cat of URGENCY) {
    if (items.some((item) => item.category === cat)) return cat;
  }
  return items[0]?.category || "read";
}

export function itemStamp(item: MailItem): string {
  return item.mailed_at || item.created_at || "";
}

function normMid(value?: string): string {
  return (value || "").replace(/[<>\s]/g, "").toLowerCase();
}

export function groupThreads(items: MailItem[]): Thread[] {
  const parent = new Map<number, number>();
  for (const item of items) parent.set(item.id, item.id);

  const find = (id: number): number => {
    let cur = parent.get(id) ?? id;
    while (parent.get(cur) !== cur) {
      const next = parent.get(cur) ?? cur;
      parent.set(cur, parent.get(next) ?? next);
      cur = next;
    }
    return cur;
  };
  const union = (a: number, b: number) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(rb, ra);
  };

  const byMid = new Map<string, number>();
  const bySub = new Map<string, number>();
  for (const item of items) {
    const mid = normMid(item.message_id);
    if (mid) {
      const seen = byMid.get(mid);
      if (seen != null) union(item.id, seen);
      byMid.set(mid, item.id);
    }
    const irt = normMid(item.in_reply_to);
    if (irt) {
      const seen = byMid.get(irt);
      if (seen != null) union(item.id, seen);
      else byMid.set(irt, item.id);
    }
    const sub = threadKey(item.subject);
    if (sub) {
      const seen = bySub.get(sub);
      if (seen != null) union(item.id, seen);
      bySub.set(sub, item.id);
    }
  }

  const buckets = new Map<number, MailItem[]>();
  for (const item of items) {
    const key = find(item.id);
    const list = buckets.get(key) ?? [];
    list.push(item);
    buckets.set(key, list);
  }

  const threads: Thread[] = [];
  for (const [key, group] of buckets) {
    const chronological = [...group].sort((a, b) => {
      const delta = itemStamp(a).localeCompare(itemStamp(b));
      return delta !== 0 ? delta : a.id - b.id;
    });
    const latest = chronological[chronological.length - 1];
    const names: string[] = [];
    for (const item of chronological) {
      if (!names.includes(item.sender_name)) names.push(item.sender_name);
    }
    const recaps = chronological.map((item) => item.summary).filter(Boolean);
    const recap = recaps[recaps.length - 1] || latest.reason || "";
    const pieces: Piece[] = [];
    for (const item of chronological) {
      for (const row of item.attachments || []) pieces.push({ ...row, itemId: item.id });
    }
    threads.push({
      key: String(key),
      title: threadTitle(latest.subject),
      items: chronological,
      latest,
      category: threadCategory(chronological),
      people: names,
      recap,
      pieces,
      unseen: chronological.some((item) => item.seen === false),
      flagged: chronological.some((item) => item.flagged),
      notionUrl: chronological.find((item) => item.notion_url)?.notion_url || "",
      ids: chronological.map((item) => item.id),
    });
  }
  threads.sort((a, b) => {
    const delta = itemStamp(b.latest).localeCompare(itemStamp(a.latest));
    return delta !== 0 ? delta : b.latest.id - a.latest.id;
  });
  return threads;
}

export function displayName(name: string): string {
  const raw = (name || "").trim();
  if (!raw || raw === "—") return "—";
  if (/\s/.test(raw) && /\p{Lu}/u.test(raw)) return raw;
  const local = raw.includes("@") ? raw.split("@")[0] : raw;
  if (/[._-]/.test(local) && local === local.toLocaleLowerCase("fr")) {
    return local
      .split(/[._-]+/)
      .filter(Boolean)
      .map((part) => part.charAt(0).toLocaleUpperCase("fr") + part.slice(1))
      .join(" ");
  }
  if (local === local.toLocaleLowerCase("fr") && local.length > 1) {
    return local.charAt(0).toLocaleUpperCase("fr") + local.slice(1);
  }
  return raw;
}

const PARIS = "Europe/Paris";

function parisDay(value: Date): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: PARIS,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(value);
}

export function dayHeading(iso: string, now = new Date()): string {
  const stamp = new Date(iso);
  if (!iso || Number.isNaN(stamp.getTime())) return "Plus tôt";
  const day = parisDay(stamp);
  const today = parisDay(now);
  if (day === today) return "Aujourd’hui";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: PARIS,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const y = Number(parts.find((part) => part.type === "year")?.value);
  const m = Number(parts.find((part) => part.type === "month")?.value);
  const d = Number(parts.find((part) => part.type === "day")?.value);
  const yesterday = parisDay(new Date(Date.UTC(y, m - 1, d - 1, 12)));
  if (day === yesterday) return "Hier";
  return new Intl.DateTimeFormat("fr-FR", {
    timeZone: PARIS,
    day: "numeric",
    month: "short",
  }).format(stamp);
}

export type ThreadSection = {
  id: string;
  label: string;
  threads: Thread[];
};

export function listSections(threads: Thread[], pinAction: boolean): ThreadSection[] {
  const action = pinAction
    ? threads.filter((thread) => thread.category === "todo" || thread.category === "waiting")
    : [];
  const rest = pinAction
    ? threads.filter((thread) => thread.category !== "todo" && thread.category !== "waiting")
    : threads;
  const sections: ThreadSection[] = [];
  if (action.length) sections.push({ id: "action", label: "À traiter", threads: action });
  const groups = new Map<string, Thread[]>();
  const order: string[] = [];
  for (const thread of rest) {
    const label = dayHeading(itemStamp(thread.latest));
    const list = groups.get(label);
    if (list) list.push(thread);
    else {
      groups.set(label, [thread]);
      order.push(label);
    }
  }
  for (const label of order) {
    sections.push({ id: `day:${label}`, label, threads: groups.get(label) ?? [] });
  }
  return sections;
}

export function flattenSections(sections: ThreadSection[]): Thread[] {
  return sections.flatMap((section) => section.threads);
}

export function bubbleText(item: MailItem): string {
  if (item.category === "newsletters" || item.category === "spam") {
    return item.summary || item.excerpt;
  }
  const text = (item.excerpt || item.summary || "").replace(/[ \t]+\n/g, "\n").trim();
  if (text.length > 700) return `${text.slice(0, 699).trim()}…`;
  return text;
}
