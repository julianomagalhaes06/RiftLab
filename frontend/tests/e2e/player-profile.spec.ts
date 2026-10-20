import { expect, test, type Page } from "@playwright/test";
import { matchSummary, mockApi, ok, PLAYER_ID, problem } from "./support/mock-api";

async function noHorizontalScroll(page: Page) {
  const widths = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
}

for (const width of [360, 1280]) {
  test.describe(`perfil de jogador em ${width} px`, () => {
    test.beforeEach(async ({ page }) => {
      // O ícone vem do Data Dragon; sem rede externa no teste, o fallback é a inicial.
      await page.route("https://**/*", (route) => route.abort());
      await page.setViewportSize({ width, height: 900 });
    });

    test("busca Nome#TAG pelo topo e abre o perfil com elo e partidas do jogador", async ({ page }) => {
      let player: string | null = null;
      await mockApi(page, {
        "GET /api/v1/matches": ({ url }) => {
          player = url.searchParams.get("player");
          return ok({ items: player ? [matchSummary({ matchId: "BR1_42", patch: "16.20", localParticipantId: 3 })] : [], nextCursor: null });
        },
      });
      await page.goto("/historico");
      const search = page.getByRole("searchbox", { name: "Buscar jogador pelo Riot ID" });
      await search.fill("Jogador Teste");
      await search.press("Enter");
      await expect(page.getByRole("search", { name: "Buscar jogador" }).getByRole("alert")).toContainText("Nome#TAG");
      await search.fill("Jogador Teste#BR1");
      await search.press("Enter");
      await expect(page).toHaveURL(/\/jogador\/Jogador%20Teste-BR1$/);
      await expect(page.getByText(/Nível 321/)).toBeVisible();
      await expect(page.getByRole("heading", { name: /Jogador Teste #BR1/ })).toBeVisible();
      await expect(page.getByRole("list", { name: "Elo" })).toContainText("Ouro II");
      await expect(page.getByRole("link", { name: "Contexto da partida BR1_42" })).toBeVisible();
      expect(player).toBe(PLAYER_ID);
      await expect(page.locator(".player-header__icon img")).toHaveCount(0); // ícone sem rede: inicial
      await noHorizontalScroll(page);
    });

    test("Riot ID inexistente explica e não quebra a página", async ({ page }) => {
      await mockApi(page, { "POST /api/v1/players/lookup": () => problem(404, "PLAYER_NOT_FOUND") });
      await page.goto("/jogador/Ninguem-000");
      await expect(page.getByRole("alert")).toContainText("Nenhuma conta Riot com esse Riot ID");
      await noHorizontalScroll(page);
    });
  });
}
