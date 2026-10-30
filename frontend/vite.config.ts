import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: http://localhost:5173 proxies API calls to FastAPI on :8010 (no CORS needed; VITE_API_PORT overrides).
// Build: output is served by FastAPI itself at /ui, on whatever port console.bat chose.
// (read through globalThis so the config type-checks without @types/node)
const env = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env ?? {};
const api = `http://127.0.0.1:${env.VITE_API_PORT ?? "8010"}`;

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/ui/" : "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": { target: api, ws: true }, // ws: live co-editing of drafts
      "/health": api,
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
}));
