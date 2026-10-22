import { expect, test, type Page } from "@playwright/test";
import { mockApi, ok } from "./support/mock-api";

async function noHorizontalScroll(page: Page) {
  const widths = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
}

function player(n: number, overrides: Record<string, unknown> = {}) {
  return {
    gameName: `Jogador ${n}`, tagLine: "BR1", bot: false, requested: n === 0, championId: 51 + n, champion: null,
    spell1Id: 4, spell2Id: 14, keystoneId: 8010, perkStyle: 8000, perkSubStyle: 8400, rankState: "READY",
    rank: { tier: n % 2 ? "PLATINUM" : "GOLD", division: "II", leaguePoints: 41, wins: 10, losses: 8 }, ...overrides,
  };
}

const inGame = {
  state: "IN_GAME", gameName: "Jogador Teste", tagLine: "BR1", checkedAt: "2026-10-22T15:00:00Z",
  game: {
    queueId: 420, rankQueue: "RANKED_SOLO_5x5", gameMode: "CLASSIC", mapId: 11, startedAt: new Date(Date.now() - 600_000).toISOString(),
    lengthSeconds: 600, dataDragonVersion: "16.20.1",
    teams: [
      { teamId: 100, bans: [{ championId: 99, champion: null }, { championId: 98, champion: null }], participants: [0, 1, 2, 3, 4].map((n) => player(n)) },
      { teamId: 200, bans: [{ championId: 97, champion: null }], participants: [5, 6, 7, 8].map((n) => player(n)).concat(player(9, { bot: true, gameName: null, tagLine: null, rankState: "UNAVAILABLE", rank: null })) },
    ],
  },
};

for (const width of [360, 1280]) {
  test.describe(`partida ao vivo em ${width} px (CC-1211)`, () => {
    test.beforeEach(async ({ page }) => {
      await page.route("https://**/*", (route) => route.abort()); // sem rede externa: ícones caem no texto
      await page.setViewportSize({ width, height: 900 });
    });

    test("da página do jogador à partida ao vivo, com os dois times e o jogador buscado marcado", async ({ page }) => {
      let body: unknown = null;
      await mockApi(page, { "POST /api/v1/players/live": (request) => { body = request.body; return ok(inGame); } });
      await page.goto("/jogador/Jogador%20Teste-BR1");
      await page.getByRole("link", { name: "Partida ao vivo" }).click();
      await expect(page).toHaveURL(/\/jogador\/Jogador%20Teste-BR1\/ao-vivo$/);
      const blue = page.getByRole("region", { name: "Time azul" });
      const red = page.getByRole("region", { name: "Time vermelho" });
      await expect(blue.getByRole("listitem")).toHaveCount(5);
      await expect(red.getByRole("listitem")).toHaveCount(5);
      await expect(page.getByText("Ranqueada Solo/Duo")).toBeVisible();
      await expect(blue.locator(".live-player.is-selected")).toHaveCount(1);
      await expect(red.getByText("Bot").first()).toBeVisible();
      expect(body).toEqual({ gameName: "Jogador Teste", tagLine: "BR1" });
      await noHorizontalScroll(page);
      await blue.getByRole("link", { name: "Jogador 1#BR1" }).click();
      await expect(page).toHaveURL(/\/jogador\/Jogador%201-BR1$/);
    });

    test("aviso de jogando agora no botão da página do jogador (CC-1213)", async ({ page }) => {
      await mockApi(page, {
        "POST /api/v1/players/live/status": () => ok({ state: "IN_GAME", gameName: "Jogador Teste", tagLine: "BR1", checkedAt: "2026-10-22T15:00:00Z",
          queueId: 440, gameMode: "CLASSIC", startedAt: new Date(Date.now() - 300_000).toISOString() }),
        "POST /api/v1/players/live": () => ok(inGame),
      });
      await page.goto("/jogador/Jogador%20Teste-BR1");
      await expect(page.getByText(/Este jogador está em partida neste momento · Ranqueada Flex · \d+:\d\d de jogo/)).toBeVisible();
      await noHorizontalScroll(page);
      await page.getByRole("link", { name: "Ao vivo agora" }).click();
      await expect(page.getByRole("region", { name: "Time azul" })).toBeVisible();
    });

    test("fora de partida, o botão fica neutro", async ({ page }) => {
      await mockApi(page);
      await page.goto("/jogador/Jogador%20Teste-BR1");
      await expect(page.getByRole("link", { name: "Partida ao vivo" })).toBeVisible();
      await expect(page.getByText(/em partida neste momento/)).toHaveCount(0);
    });

    test("fora de partida explica e não quebra a página", async ({ page }) => {
      await mockApi(page);
      await page.goto("/jogador/Jogador%20Teste-BR1/ao-vivo");
      await expect(page.getByText("Jogador Teste#BR1 não está em partida agora.")).toBeVisible();
      await expect(page.getByRole("button", { name: "Verificar de novo" })).toBeEnabled();
      await noHorizontalScroll(page);
    });
  });
}
