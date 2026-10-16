import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the app on http://localhost:5173 and forwards /api/*
// to the backend, mirroring what nginx does in the Docker image.
// Point it elsewhere with VITE_DEV_PROXY_TARGET (default: backend on localhost:8000).
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_DEV_PROXY_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
