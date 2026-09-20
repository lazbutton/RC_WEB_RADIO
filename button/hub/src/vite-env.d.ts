/// <reference types="vite/client" />

export type BrandApp = {
  url?: string;
  health?: string;
  stream?: string;
  status?: string;
  streamUrl?: string;
  productionStreamUrl?: string;
  nowUrl?: string;
  icecastStatusUrl?: string;
};

export type Brand = {
  suite: { id: string; name: string; short: string; tagline?: string };
  infra: { smbShare?: string; smbHost?: string };
  apps: Record<string, BrandApp>;
};

declare global {
  interface Window {
    webkitAudioContext?: typeof AudioContext;
  }
}

export {};
