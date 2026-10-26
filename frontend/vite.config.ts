/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const apiProxyTarget = process.env.RIFTLAB_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

const proxy = {
  "/api": { target: apiProxyTarget, changeOrigin: true },
  "/health": { target: apiProxyTarget, changeOrigin: true },
};

// A API não tem autenticação: o loopback é a fronteira (CC-1215). Dev e preview escutam só em
// 127.0.0.1; o container passa --host 0.0.0.0 (frontend/Dockerfile) e o compose publica a porta
// só em 127.0.0.1. Nenhuma página de outro site enquadra a SPA nem lê respostas por CORS.
export const loopbackHost = "127.0.0.1";
export const securityHeaders = {
  "X-Frame-Options": "DENY",
  "Content-Security-Policy": "frame-ancestors 'none'",
};

// O Vite dev abre o editor do operador em /__open-in-editor; só o overlay de erro da própria
// SPA (mesma origem) pode chamá-lo, nunca uma navegação vinda de outro site.
export function guardOpenInEditor(): Plugin {
  return {
    name: "riftlab:guard-open-in-editor",
    configureServer(server) {
      server.middlewares.use("/__open-in-editor", (req, res, next) => {
        if (req.headers["sec-fetch-site"] === "same-origin") {
          next();
          return;
        }
        res.statusCode = 403;
        res.end();
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), guardOpenInEditor()],
  // Roteamento por History API (CC-205): qualquer caminho de navegação que não seja
  // arquivo nem proxy (/api, /health) devolve index.html, para deep link, recarregar
  // e voltar/avançar não darem 404 — no dev server, no `vite preview` e no container.
  appType: "spa",
  server: {
    host: loopbackHost,
    port: 5173,
    cors: false,
    headers: securityHeaders,
    proxy,
  },
  preview: {
    host: loopbackHost,
    port: 5173,
    cors: false,
    headers: securityHeaders,
    proxy,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    exclude: ["node_modules/**", "tests/e2e/**"],
  },
});
