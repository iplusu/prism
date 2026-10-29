import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Development: the FastAPI backend runs on 127.0.0.1:8000. The proxy streams
// responses, so server-sent events (/api/tasks/{id}/stream) pass through
// unbuffered; the backend already sends Cache-Control: no-cache.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
      // Collaboration WebSocket (used when COLLAB_PUBLIC_URL is empty and the
      // client derives ws://<host>/collab). Target: the collab service.
      "/collab": { target: "ws://127.0.0.1:1234", ws: true, changeOrigin: false },
    },
  },
  preview: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
});
