/// <reference types="vite/client" />

export type NtrConfig = {
  streamUrl: string;
  productionStreamUrl?: string;
  nowUrl: string;
  icecastStatusUrl?: string;
  festivalUrl?: string;
};

declare global {
  interface Window {
    NTR_CONFIG?: NtrConfig;
    webkitAudioContext?: typeof AudioContext;
  }
}

export {};
