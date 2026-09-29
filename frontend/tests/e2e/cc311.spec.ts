import { expect, test } from "@playwright/test";
import { matchSummary, mockApi, ok, problem } from "./support/mock-api";

const evidence = "../.private/validation/sprint-3/CC-311";
const rankedUnavailable = { status: "UNAVAILABLE", reason: "NO_OBSERVATION", position: null, change: null };
const fixtureMatches = [
  matchSummary({ matchId: "BR1_31203456789012345678", patch: "16.17", durationSeconds: 1872, championsPresent: [{ championId: 51, name: "Caitlyn" }] }),
  matchSummary({ matchId: "DEMO-0008", championsPresent: [{ championId: 9000, name: null }] }),
  matchSummary({ matchId: "BR1_311003", patch: "16.16", durationSeconds: 1450, championsPresent: [] }),
  matchSummary({ matchId: "BR1_311002", patch: "16.17", durationSeconds: 2110, championsPresent: [{ championId: 145, name: "Kai'Sa" }] }),
  matchSummary({ matchId: "BR1_311001", patch: "16.17", durationSeconds: 1654, championsPresent: [{ championId: 81, name: "Ezreal" }] }),
];

async function layoutIsClear(page: import("@playwright/test").Page, width: number) {
  const metrics = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: document.documentElement.clientWidth,
  }));
  expect(metrics.documentWidth).toBeLessThanOrEqual(width);
  if (width < 700) {
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    expect(await page.evaluate(() => {
      const footerEnd = document.querySelector(".app__footer p:last-child")!.getBoundingClientRect().bottom;
      const navTop = document.querySelector(".main-nav")!.getBoundingClientRect().top;
      return footerEnd <= navTop;
    })).toBe(true);
  }
}

for (const width of [360, 390, 1280, 1440]) {
  test(`CC-311 Painel e Histórico com dados disponíveis em ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 700 ? 780 : 900 });
    const calls = await mockApi(page, {
      "GET /api/v1/ranked/me": () => ok(rankedUnavailable),
      "GET /api/v1/matches": () => ok({ items: fixtureMatches, nextCursor: null }),
    });
    await page.goto("/painel");
    await expect(page.getByRole("heading", { name: "Partidas importadas recentes" })).toBeVisible();
    await expect(page.locator(".recent-row")).toHaveCount(5);
    await expect(page.getByText(/Mostrando 5 partidas importadas recentes/)).toBeVisible();
    await expect(page.getByText(/Ainda não há observação desta conta/)).toBeVisible();
    await expect(page.getByText(/vitória|derrota|KDA|tropas/i)).toHaveCount(0);
    expect(calls.count("GET /api/v1/matches")).toBe(1);
    await layoutIsClear(page, width);
    await page.screenshot({ path: `${evidence}/painel-dados-${width}.png`, fullPage: true });

    const navHistory = page.getByRole("navigation", { name: "Áreas principais" }).getByRole("link", { name: /Histórico/ });
    await navHistory.focus();
    expect(await navHistory.evaluate((node) => node.matches(":focus-visible"))).toBe(true);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Partidas para estudar" })).toBeVisible();
    await expect(page.getByRole("list", { name: "Partidas carregadas" }).locator(".history-row")).toHaveCount(5);
    expect(calls.count("GET /api/v1/matches")).toBe(2);
    const select = page.getByRole("button", { name: "Selecionar detalhes da partida DEMO-0008" });
    await select.focus();
    await page.keyboard.press("Enter");
    await expect(select).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator("#history-selection")).toContainText("DEMO-0008");
    expect(calls.count("GET /api/v1/matches")).toBe(2);
    await layoutIsClear(page, width);
    await page.screenshot({ path: `${evidence}/historico-dados-${width}.png`, fullPage: true });
    await page.getByLabel("Buscar por identificador ou campeão presente").fill("caitlyn");
    await expect(page.getByRole("list", { name: "Partidas carregadas" }).locator(".history-row")).toHaveCount(1);
    await page.getByLabel("Buscar por identificador ou campeão presente").clear();
    await page.getByRole("button", { name: "DEMO", exact: true }).click();
    await expect(page.getByRole("list", { name: "Partidas carregadas" }).locator(".history-row")).toHaveCount(1);
    await page.getByRole("link", { name: "Contexto da partida DEMO-0008" }).click();
    await expect(page).toHaveURL(/\/historico\/DEMO-0008$/);
    await page.getByRole("link", { name: /^Participante 1 —/ }).click();
    await page.getByRole("link", { name: "Decisão em 7:52" }).click();
    await expect(page).toHaveURL(/\/decisions\//);
  });

  test(`CC-311 estados vazios sem posição em ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 700 ? 780 : 900 });
    await mockApi(page, {
      "GET /api/v1/ranked/me": () => ok(rankedUnavailable),
      "GET /api/v1/matches": () => ok({ items: [], nextCursor: null }),
    });
    await page.goto("/painel");
    await expect(page.getByText("Nenhuma partida importada")).toBeVisible();
    await expect(page.getByText(/Ainda não há observação desta conta/)).toBeVisible();
    await layoutIsClear(page, width);
    await page.screenshot({ path: `${evidence}/painel-vazio-${width}.png`, fullPage: true });
    await page.getByRole("navigation", { name: "Áreas principais" }).getByRole("link", { name: /Histórico/ }).click();
    await expect(page.getByText("Nenhuma partida importada")).toBeVisible();
    await layoutIsClear(page, width);
    await page.screenshot({ path: `${evidence}/historico-vazio-${width}.png`, fullPage: true });
  });
}

test("CC-311 falha e retry no Painel e no Histórico", async ({ page }) => {
  let attempts = 0;
  await mockApi(page, {
    "GET /api/v1/ranked/me": () => ok(rankedUnavailable),
    "GET /api/v1/matches": () => ++attempts % 2 ? problem(503, "SERVICE_UNAVAILABLE") : ok({ items: [], nextCursor: null }),
  });
  await page.goto("/painel");
  await expect(page.getByRole("button", { name: "Tentar novamente" })).toBeVisible();
  await page.getByRole("button", { name: "Tentar novamente" }).click();
  await expect(page.getByText("Nenhuma partida importada")).toBeVisible();
  await page.goto("/historico");
  await expect(page.getByRole("button", { name: "Tentar novamente" })).toBeVisible();
  await page.getByRole("button", { name: "Tentar novamente" }).click();
  await expect(page.getByText("Nenhuma partida importada")).toBeVisible();
});
