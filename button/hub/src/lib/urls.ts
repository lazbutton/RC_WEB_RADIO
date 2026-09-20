const LAN = "192.168.1.100";
const TS = "nasgul.taild4714f.ts.net";

export type HubUrls = {
  console: string;
  catalog: string;
  inbox: string;
  icecast: string;
  streamNasgul: string;
  streamStudio: string;
  streamPublic: string;
  now: string;
  nasgulStatus: string;
  publicStatus: string;
};

function here(url: string | undefined): string {
  if (!url) return "";
  try {
    const parsed = new URL(url, location.origin);
    const host = location.hostname;
    const nasgul = parsed.hostname === LAN || parsed.hostname === TS;
    if (!nasgul || (host !== LAN && host !== TS)) return parsed.href;
    if (parsed.protocol === "https:") return parsed.href;
    parsed.protocol = location.protocol;
    parsed.hostname = host;
    return parsed.href;
  } catch {
    return url;
  }
}

function studioHref(mp3Url: string): string {
  try {
    const next = new URL(mp3Url);
    next.pathname = "/studio.wav";
    next.search = "";
    next.hash = "";
    return next.href;
  } catch {
    return "";
  }
}

export function urlsFromBrand(brand: {
  apps?: Record<
    string,
    { url?: string; stream?: string; productionStreamUrl?: string }
  >;
}): HubUrls {
  const apps = brand.apps || {};
  const streamNasgul = here(apps.icecast?.stream);
  return {
    console: here(apps.console?.url),
    catalog: here(apps.catalog?.url),
    inbox: here(apps.inbox?.url),
    icecast: here(apps.icecast?.url),
    streamNasgul,
    streamStudio: studioHref(streamNasgul),
    streamPublic: apps.player?.productionStreamUrl || "",
    now: "/probe/now",
    nasgulStatus: "/probe/icecast",
    publicStatus: "/probe/vps",
  };
}

export type Atelier = { href: string; title: string };

export const ATELIER_LINK = {
  target: "_blank",
  rel: "noopener noreferrer",
} as const;

export function openAtelier(href: string) {
  window.open(href, "_blank", "noopener,noreferrer");
}

export function ateliers(urls: HubUrls): Atelier[] {
  const atelier = (path: string, title: string): Atelier => ({
    href: urls.console ? new URL(path, urls.console).href : "",
    title,
  });
  return [
    { href: urls.console, title: "Console" },
    atelier("carts", "Carts"),
    atelier("horloges", "Horloges"),
    atelier("semaine", "Semaine"),
    atelier("conducteur", "Conducteur"),
    atelier("antenne", "Antenne"),
    { href: urls.catalog, title: "Catalogue" },
    { href: urls.inbox, title: "Inbox" },
    { href: urls.icecast, title: "Icecast" },
    { href: urls.streamNasgul, title: "MP3" },
    { href: urls.streamStudio, title: "WAV" },
  ].filter((item) => item.href);
}
