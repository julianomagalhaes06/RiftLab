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
      // CC-1204: cards de elo Solo/Duo e Flex lado a lado (empilhados no celular).
      const ranks = page.getByRole("group", { name: "Elo" });
      await expect(ranks.locator(".rank-card")).toHaveCount(2);
      await expect(ranks.locator(".rank-card").first()).toContainText("Elo Ouro II, 41 de 100 PDL");
      const [solo, flex] = await ranks.locator(".rank-card").evaluateAll((cards) => cards.map((card) => card.getBoundingClientRect().top));
      expect(width > 900 ? solo === flex : flex > solo).toBe(true);
      await expect(page.getByRole("link", { name: "Contexto da partida BR1_42" })).toBeVisible();
      expect(player).toBe(PLAYER_ID);
      await expect(page.locator(".player-header__icon img")).toHaveCount(0); // ícone sem rede: inicial
      await noHorizontalScroll(page);
    });

    test("topo: chip do perfil da conta leva à página dela; faixa mostra o patch (CC-1202)", async ({ page }) => {
      await mockApi(page);
      await page.goto("/");
      const chip = page.getByRole("link", { name: "Seu perfil: Conta Própria#BR1, Ouro II · 41 PDL" });
      await expect(chip).toBeVisible();
      // No celular o chip fica só com o retrato (o nome e o elo vão para o rótulo acessível).
      await expect(page.locator(".profile-chip__name")).toBeVisible({ visible: width > 700 });
      if (width > 700) await expect(page.getByText("Patch 16.20 · Data Dragon 16.20.1")).toBeVisible();
      await noHorizontalScroll(page);
      await chip.click();
      await expect(page).toHaveURL(/\/jogador\/Conta%20Pr%C3%B3pria-BR1$/);
    });

    test("Riot ID inexistente explica e não quebra a página", async ({ page }) => {
      await mockApi(page, { "POST /api/v1/players/lookup": () => problem(404, "PLAYER_NOT_FOUND") });
      await page.goto("/jogador/Ninguem-000");
      await expect(page.getByRole("alert")).toContainText("Nenhuma conta Riot com esse Riot ID");
      await noHorizontalScroll(page);
    });
  });
}
