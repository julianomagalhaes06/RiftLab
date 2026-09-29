import { expect, test, type Page } from "@playwright/test";
import { DECISION_1, decisionState, matchDetail, mockApi, ok } from "./support/mock-api";

/**
 * CC-208: nome e retrato oficiais por patch. A API simulada devolve o campeão já
 * resolvido (nome, versão fixada, arquivo); o navegador busca o retrato direto do
 * CDN oficial, aqui interceptado (nenhuma rede real): um PNG mínimo para Kai'Sa e
 * falha de carga para Caitlyn. Id sem catálogo (999) fica com o rótulo numérico.
 */
const MATCH_ID = "BR1_3120345678";
const PNG_1X1 = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64",
);
const champion = (name: string, imageFile: string) => ({ name, dataDragonVersion: "16.17.1", imageFile });
const PARTICIPANTS = [
  { participantId: 1, championId: 145, team: 100, role: "BOTTOM", champion: champion("Kai'Sa", "Kaisa.png") },
  { participantId: 2, championId: 51, team: 100, role: "BOTTOM", champion: champion("Caitlyn", "Caitlyn.png") },
  { participantId: 3, championId: 999, team: 200, role: "UTILITY", champion: null },
];

async function setup(page: Page, cdn: "partial" | "down" = "partial") {
  const imageRequests: string[] = [];
  await page.route("https://ddragon.leagueoflegends.com/**", async (route) => {
    imageRequests.push(route.request().url());
    if (cdn === "partial" && route.request().url().endsWith("/Kaisa.png")) {
      await route.fulfill({ status: 200, contentType: "image/png", body: PNG_1X1 });
    } else {
      await route.abort("failed");
    }
  });
  await mockApi(page, {
    "GET /api/v1/matches/:id": () => ok(matchDetail({ matchId: MATCH_ID, patch: "16.17", participants: PARTICIPANTS })),
    "GET /api/v1/decisions/:id": () => ok(decisionState({ matchId: MATCH_ID, participantId: 1 })),
  });
  return imageRequests;
}

function portraitOf(page: Page, participantId: number) {
  return page
    .getByRole("link", { name: new RegExp(`^Participante ${participantId} —`) })
    .getByTestId("champion-portrait");
}

test("partida mostra nome e retrato por key, recua sem salto de layout e só usa o CDN oficial", async ({ page }) => {
  const imageRequests = await setup(page);
  await page.goto(`/matches/${MATCH_ID}`);

  await expect(page.getByRole("link", { name: "Participante 1 — Kai'Sa" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Participante 2 — Caitlyn" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Participante 3 — Campeão #999" })).toBeVisible();

  const kaisa = portraitOf(page, 1).locator("img");
  await expect(kaisa).toHaveAttribute("src", "https://ddragon.leagueoflegends.com/cdn/16.17.1/img/champion/Kaisa.png");
  await expect.poll(() => kaisa.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);

  // Caitlyn: a imagem falha ao carregar → caixa vazia do MESMO tamanho, nome mantido
  await expect(portraitOf(page, 2)).toHaveAttribute("data-state", "fallback");
  const boxes = await Promise.all([1, 2, 3].map((id) => portraitOf(page, id).boundingBox()));
  for (const box of boxes) {
    expect(box).not.toBeNull();
    expect([Math.round(box!.width), Math.round(box!.height)]).toEqual([32, 32]);
  }
  await expect(portraitOf(page, 3)).toHaveAttribute("data-state", "fallback");

  expect(imageRequests.length).toBeGreaterThan(0);
  for (const url of imageRequests) {
    expect(url).toMatch(/^https:\/\/ddragon\.leagueoflegends\.com\/cdn\/16\.17\.1\/img\/champion\/[A-Za-z0-9]+\.png$/);
  }
  await expect(page.getByText(/RiftLab isn't endorsed by Riot Games/)).toBeVisible();
  await expect(page.getByText(/Revisão de partidas concluídas/)).toBeVisible();
});

test("CDN fora do ar: navegação por teclado até a decisão continua, com rótulos de texto", async ({ page }) => {
  await setup(page, "down");
  await page.goto(`/matches/${MATCH_ID}`);
  const first = page.getByRole("link", { name: "Participante 1 — Kai'Sa" });
  await expect(first).toBeVisible();
  await expect(portraitOf(page, 1)).toHaveAttribute("data-state", "fallback");
  await first.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/matches/${MATCH_ID}\\?participant=1`));
  await expect(page.getByRole("link", { name: /Participante 1 — Kai'Sa/ })).toHaveAttribute("aria-current", "true");

  await page.goto(`/decisions/${DECISION_1}`);
  await expect(page.getByRole("heading", { name: /participante 1/ })).toBeVisible();
  await expect(page.getByText("Kai'Sa")).toBeVisible();
});

test.describe("360 px", () => {
  test.use({ viewport: { width: 360, height: 740 } });
  test("participantes com retrato e nome não criam rolagem horizontal", async ({ page }) => {
    await setup(page);
    await page.goto(`/matches/${MATCH_ID}`);
    await expect(page.getByRole("link", { name: "Participante 1 — Kai'Sa" })).toBeVisible();
    const widths = await page.evaluate(() => ({
      client: document.documentElement.clientWidth,
      doc: document.documentElement.scrollWidth,
    }));
    expect(widths.doc).toBeLessThanOrEqual(widths.client);
    await expect(page.getByText(/RiftLab isn't endorsed by Riot Games/)).toBeVisible();
  });
});
