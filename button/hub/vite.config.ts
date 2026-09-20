import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5178,
    strictPort: true,
    host: "127.0.0.1",
    proxy: {
      "/probe": {
        target: "http://nasgul.taild4714f.ts.net:30120",
        changeOrigin: true,
      },
      "/brand.json": {
        target: "http://nasgul.taild4714f.ts.net:30120",
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 5178,
    strictPort: true,
    host: "127.0.0.1",
  },
});
