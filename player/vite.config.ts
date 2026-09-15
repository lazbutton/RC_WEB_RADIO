import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5174,
    strictPort: true,
    host: "127.0.0.1",
  },
  preview: {
    port: 5174,
    strictPort: true,
    host: "127.0.0.1",
  },
});
