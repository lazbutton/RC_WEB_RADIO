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
    partnerCode: string;
    smbHost?: string;
    smbUser?: string;
  };
  apps: Record<string, BrandApp>;
  tailscaleBase?: string;
};

function hubUrl(): string {
  const env = import.meta.env?.VITE_BUTTON_HUB_URL;
  if (typeof env === "string" && env.trim()) return env.replace(/\/$/, "");
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

export async function loadBrand(): Promise<Brand> {
  const hub = hubUrl();
  if (hub) {
    const remote = await fetchJson(`${hub}/brand.json`);
    if (remote?.suite?.name) return remote;
  }
  const local = await fetchJson("/brand.json");
  if (local?.suite?.name) return local;
  const bundled = await fetchJson("./brand.json");
  if (bundled?.suite?.name) return bundled;
  throw new Error("brand.json introuvable");
}
