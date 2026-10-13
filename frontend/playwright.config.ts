import { defineConfig, devices } from "@playwright/test";

/**
 * Dois projetos:
 * - `chromium`: specs com API simulada (page.route) e o protótipo do CC-102. Sobe
 *   `vite preview` sozinho (o backend não é necessário).
 * - `real-stack`: demonstração ponta a ponta contra a stack compose REAL (CC-205).
 *   Exige E2E_BASE_URL apontando para o frontend da stack já no ar e migrada; nenhum
 *   servidor local é iniciado nesse caso.
 */
const externalBaseUrl = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  reporter: "list",
  use: {
    baseURL: externalBaseUrl ?? "http://127.0.0.1:5173",
  },
  webServer: externalBaseUrl
    ? undefined
    : {
        command: "npm run build && npm run preview",
        url: "http://127.0.0.1:5173",
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
  projects: [
    {
      name: "chromium",
      testIgnore: /(real-stack|demo)\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "real-stack",
      testMatch: /real-stack\.spec\.ts/,
      fullyParallel: false,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // Roteiro da demonstração (CC-605): stack de demonstração semeada por scripts/demo.sh.
      name: "demo",
      testMatch: /demo\.spec\.ts/,
      fullyParallel: false,
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
