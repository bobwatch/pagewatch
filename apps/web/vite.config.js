import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds straight into the Python package so `pagewatch serve` ships the UI.
// Stable asset names (no hashes) keep rebuilds reviewable in git.
export default defineConfig({
  plugins: [react()],
  base: "./",
  publicDir: "src/public",
  build: {
    outDir: "../../src/pagewatch/webui",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: "assets/app.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/app[extname]",
      },
    },
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8787" },
  },
});
