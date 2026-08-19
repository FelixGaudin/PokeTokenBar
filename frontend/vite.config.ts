import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    // `yarn dev` talks to a backend started separately on 8000.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
