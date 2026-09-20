/// <reference types="vite/client" />

export type ButtonConfig = {
  streamUrl: string;
  productionStreamUrl?: string;
  nowUrl: string;
  icecastStatusUrl?: string;
  festivalUrl?: string;
};

declare global {
  interface Window {
    BUTTON_CONFIG?: ButtonConfig;
    webkitAudioContext?: typeof AudioContext;
  }
}

export {};
