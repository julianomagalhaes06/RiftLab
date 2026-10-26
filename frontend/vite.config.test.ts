import { readFileSync } from "node:fs";
import type { AddressInfo } from "node:net";
import { resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createServer, type ViteDevServer } from "vite";
import config, { guardOpenInEditor, loopbackHost, securityHeaders } from "./vite.config";

// CC-1215: a API não tem autenticação e o loopback é a fronteira. Caminhos relativos à raiz do
// Vitest (frontend/).
describe("config do Vite", () => {
  it("escuta só em 127.0.0.1, sem CORS e com anti-framing no dev e no preview", () => {
    for (const side of [config.server, config.preview]) {
      expect(side?.host).toBe("127.0.0.1");
      expect(side?.cors).toBe(false);
      expect(side?.headers).toEqual(securityHeaders);
    }
    expect(securityHeaders["X-Frame-Options"]).toBe("DENY");
    expect(securityHeaders["Content-Security-Policy"]).toBe("frame-ancestors 'none'");
  });

  it("não reabre o bind no script preview (o container passa --host no Dockerfile)", () => {
    const pkg = JSON.parse(readFileSync(resolve("package.json"), "utf8"));
    expect(pkg.scripts.preview).not.toContain("--host");
    expect(pkg.scripts.dev).not.toContain("--host");
  });
});

describe("servidor de desenvolvimento", () => {
  let server: ViteDevServer;
  let base: string;

  beforeAll(async () => {
    server = await createServer({
      configFile: false,
      root: resolve("."),
      logLevel: "silent",
      plugins: [guardOpenInEditor()],
      server: { host: loopbackHost, port: 0, headers: securityHeaders, cors: false, hmr: false },
    });
    await server.listen();
    const address = server.httpServer?.address() as AddressInfo;
    expect(address.address).toBe("127.0.0.1");
    base = `http://127.0.0.1:${address.port}`;
  });

  afterAll(async () => {
    await server.close();
  });

  it("devolve a página com anti-framing", async () => {
    const response = await fetch(`${base}/`, { headers: { Accept: "text/html" } });
    expect(response.headers.get("x-frame-options")).toBe("DENY");
    expect(response.headers.get("content-security-policy")).toBe("frame-ancestors 'none'");
  });

  it.each(["cross-site", "same-site", "none", undefined])(
    "recusa /__open-in-editor com Sec-Fetch-Site %s",
    async (site) => {
      const headers: Record<string, string> = site ? { "Sec-Fetch-Site": site } : {};
      const response = await fetch(`${base}/__open-in-editor?file=README.md`, { headers });
      expect(response.status).toBe(403);
    },
  );

  it("deixa o overlay de erro da própria SPA chegar ao editor", async () => {
    // Sem `file`, o middleware do Vite responde erro sem abrir editor algum.
    const response = await fetch(`${base}/__open-in-editor`, { headers: { "Sec-Fetch-Site": "same-origin" } });
    expect(response.status).not.toBe(403);
  });
});
