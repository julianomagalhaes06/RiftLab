import { expect, test, type Page } from "@playwright/test";
import { matchSummary, mockApi, ok, problem } from "./support/mock-api";

/**
 * Falhas previstas nas quatro áreas do conceito C (CC-601), com API simulada: a tela explica o
 * motivo e preserva o que é verdade. Painel vazio fica em cc311.spec.ts e CDN do Data Dragon fora
 * do ar no Contexto em champions.spec.ts.
 */
const evidence = "../.private/validation/sprint-6/CC-601";
const observed = {
  queue: "RANKED_SOLO_5x5", season: "2026-3", observedAt: "2026-10-10T12:00:00Z",
  origin: "RIOT_LEAGUE_V4", state: "READY", tier: "SILVER", division: "II",
  leaguePoints: 40, wins: 11, losses: 9,
};

async function noHorizontalScroll(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
    await page.evaluate(() => document.documentElement.clientWidth),
  );
}

test("Card de elo do Painel: League-V4 sem resposta preserva a última observação e diz o motivo", async ({ page }) => {
  await mockApi(page, {
    "GET /api/v1/ranked/me": () => ok({ status: "STALE", reason: "UPSTREAM_UNAVAILABLE", position: observed, change: null }),
  });
  await page.goto("/painel");
  await expect(page.getByText("A Riot não respondeu. A posição anterior foi preservada.")).toBeVisible();
  await expect(page.getByText("Elo Prata II, 40 de 100 PDL")).toBeVisible();
  await page.screenshot({ path: `${evidence}/posicao-league-v4-indisponivel.png`, fullPage: true });
});

test("Card de elo do Painel: sem observação e API fora do ar não inventam elo", async ({ page }) => {
  let calls = 0;
  await mockApi(page, {
    "GET /api/v1/ranked/me": () =>
      ++calls === 1
        ? problem(503, "SERVICE_UNAVAILABLE")
        : ok({ status: "UNAVAILABLE", reason: "NO_OBSERVATION", position: null, change: null }),
  });
  await page.goto("/painel");
  await expect(page.locator(".my-rank").getByRole("alert")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/Elo (Ferro|Bronze|Prata|Ouro)/);
  await page.reload();
  await expect(page.getByText(/Ainda não há observação desta (conta|fila)/)).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/Elo (Ferro|Bronze|Prata|Ouro)/);
});

test("Campeões: amostra vazia não inventa padrões e não chama o CDN", async ({ page }) => {
  const external: string[] = [];
  await page.route("**/*", (route) => {
    const host = new URL(route.request().url()).hostname;
    if (host === "127.0.0.1" || host === "localhost") {
      return route.fallback();
    }
    external.push(host);
    return route.abort();
  });
  await mockApi(page);
  await page.goto("/campeoes");
  await expect(page.getByRole("heading", { name: "Ainda não há trajetos elegíveis neste filtro" })).toBeVisible();
  await expect(page.getByText(/Nenhum padrão é preenchido por estimativa/)).toBeVisible();
  await page.getByRole("link", { name: "Examinar partidas no Histórico" }).click();
  await expect(page.getByRole("heading", { name: "Partidas para estudar" })).toBeVisible();
  expect(external).toEqual([]);
});

for (const width of [1280, 360]) {
  test(`Histórico em ${width}px: busca sem resultado explica e limpar volta à lista`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 700 ? 780 : 900 });
    await mockApi(page, {
      "GET /api/v1/matches": () =>
        ok({
          items: [
            matchSummary({ matchId: "BR1_123", patch: "16.19", championsPresent: [{ championId: 51, name: "Caitlyn" }] }),
            matchSummary({ matchId: "DEMO-0008" }),
          ],
          nextCursor: null,
        }),
    });
    await page.goto("/historico");
    const search = page.getByLabel("Buscar campeão ou partida");
    await search.fill("Teemo");
    await expect(page.getByRole("heading", { name: "Nenhuma partida neste filtro" })).toBeVisible();
    await expect(page.getByRole("link", { name: /Contexto da partida/ })).toHaveCount(0);
    await noHorizontalScroll(page);
    await page.screenshot({ path: `${evidence}/historico-busca-vazia-${width}.png`, fullPage: true });
    await search.clear();
    await expect(page.getByRole("link", { name: "Contexto da partida BR1_123" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Contexto da partida DEMO-0008" })).toHaveCount(0);
    await page.getByRole("button", { name: "DEMO", exact: true }).click();
    await expect(page.getByRole("link", { name: "Contexto da partida DEMO-0008" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Contexto da partida BR1_123" })).toHaveCount(0);
  });
}
