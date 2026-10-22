import { expect, test, type Page } from "@playwright/test";
import { matchDetail, mockApi, ok } from "./support/mock-api";

async function noHorizontalScroll(page: Page) {
  const widths = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
}

const MATCH_ID = "BR1_3333333333";
const participants = Array.from({ length: 10 }, (_, index) => ({
  participantId: index + 1, championId: 51 + index, team: index < 5 ? 100 : 200, role: "BOTTOM", champion: null,
  ...(index === 6 ? { gameName: "Nome Bem Comprido Aqui", tagLine: "BR1" } : index === 2 ? { gameName: "Rival", tagLine: "TAG9" } : {}),
}));

for (const width of [360, 1280]) {
  test(`partida em ${width} px: Riot ID embaixo do campeão leva ao perfil (CC-1210)`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page, { "GET /api/v1/matches/:id": () => ok(matchDetail({ matchId: MATCH_ID, patch: "16.20", participants })) });
    await page.goto(`/historico/${MATCH_ID}`);
    const link = page.getByRole("link", { name: "Perfil de Rival#TAG9" });
    await expect(link).toBeVisible();
    await expect(page.getByRole("link", { name: /^Perfil de / })).toHaveCount(2);
    await expect(page.getByText(/Riot ID como estava na partida/)).toBeVisible();
    await noHorizontalScroll(page);
    await link.click();
    await expect(page).toHaveURL(/\/jogador\/Rival-TAG9$/);
  });
}
