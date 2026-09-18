/// <reference types="vite/client" />
/// <reference path="./midi/webmidi.d.ts" />

interface ImportMetaEnv {
  readonly VITE_CATALOG_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
