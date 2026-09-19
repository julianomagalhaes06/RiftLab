/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiProxyTarget = process.env.RIFTLAB_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

const proxy = {
  "/api": { target: apiProxyTarget, changeOrigin: true },
  "/health": { target: apiProxyTarget, changeOrigin: true },
};

export default defineConfig({
  plugins: [react()],
  // Roteamento por History API (CC-205): qualquer caminho de navegação que não seja
  // arquivo nem proxy (/api, /health) devolve index.html, para deep link, recarregar
  // e voltar/avançar não darem 404 — no dev server, no `vite preview` e no container.
  appType: "spa",
  server: {
    host: true,
    port: 5173,
    proxy,
  },
  preview: {
    host: true,
    port: 5173,
    proxy,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    exclude: ["node_modules/**", "tests/e2e/**"],
  },
});
