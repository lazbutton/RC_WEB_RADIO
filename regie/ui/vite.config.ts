import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5179,
    strictPort: true,
    host: "127.0.0.1",
    proxy: {
      "/api": "http://127.0.0.1:30130",
      "/health": "http://127.0.0.1:30130",
      "/brand.json": "http://127.0.0.1:30130",
    },
  },
  preview: {
    port: 5179,
    strictPort: true,
    host: "127.0.0.1",
  },
});
