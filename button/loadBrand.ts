export type BrandApp = {
  url?: string;
  health?: string;
  stream?: string;
  status?: string;
  streamUrl?: string;
  productionStreamUrl?: string;
  nowUrl?: string;
  icecastStatusUrl?: string;
  festivalUrl?: string;
};

export type Brand = {
  suite: { id: string; name: string; short: string; tagline?: string; formerly?: string };
  labels: Record<string, Record<string, string>>;
  infra: {
    smbShare: string;
    mediaDataset?: string;
    mediaMount?: string;
    group?: string;
    partnerCode: string;
    smbHost?: string;
    smbUser?: string;
  };
  apps: Record<string, BrandApp>;
  tailscaleBase?: string;
};

function hubUrl(): string {
  const env =
    typeof import.meta !== "undefined"
      ? (import.meta as ImportMeta & { env?: { VITE_BUTTON_HUB_URL?: string } }).env
          ?.VITE_BUTTON_HUB_URL
      : undefined;
  if (env && env.trim()) return env.replace(/\/$/, "");
  if (typeof window !== "undefined" && (window as Window & { BUTTON_HUB_URL?: string }).BUTTON_HUB_URL) {
    return String((window as Window & { BUTTON_HUB_URL?: string }).BUTTON_HUB_URL).replace(/\/$/, "");
  }
  return "";
}

export function interpolate(template: string, brand: Brand): string {
  return (template || "")
    .replaceAll("{name}", brand.suite.name)
    .replaceAll("{short}", brand.suite.short)
    .replaceAll("{share}", brand.infra.smbShare);
}

export function label(brand: Brand, group: string, key: string): string {
  return interpolate(brand.labels?.[group]?.[key] || "", brand);
}

async function fetchJson(url: string): Promise<Brand | null> {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as Brand;
  } catch {
    return null;
  }
}

export async function loadBrand(fallback?: Brand): Promise<Brand> {
  const hub = hubUrl();
  if (hub) {
    const remote = await fetchJson(`${hub}/brand.json`);
    if (remote?.suite?.name) return remote;
  }
  const local = await fetchJson("./brand.json");
  if (local?.suite?.name) return local;
  if (fallback?.suite?.name) return fallback;
  throw new Error("brand.json introuvable");
}
