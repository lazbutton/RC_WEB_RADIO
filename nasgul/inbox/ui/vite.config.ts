import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5178,
    strictPort: true,
    host: "127.0.0.1",
    proxy: {
      "/api": "http://127.0.0.1:30128",
      "/health": "http://127.0.0.1:30128",
      "/brand.json": "http://127.0.0.1:30128",
    },
  },
  preview: {
    port: 5178,
    strictPort: true,
    host: "127.0.0.1",
  },
});
