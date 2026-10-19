import { expect, test, type Page } from "@playwright/test";
import { DECISION_1, matchReview, matchSummary, mockApi, ok } from "./support/mock-api";

async function noHorizontalScroll(page: Page) {
  const widths = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
}

const review = matchReview({
  purchases: [
    { decisionId: DECISION_1, timestampMs: 472_000, ordinal: 1, itemId: 3031, status: "READY", label: "COMMON", share: 0.4, rank: 1, support: 50, referenceTop: [{ itemId: 3031, share: 0.4 }, { itemId: 3006, share: 0.2 }] },
  ],
  skills: { priority: "Q>W>E", label: "UNCOMMON", share: 0.05, support: 60, mostCommon: [{ priority: "Q>E>W", share: 0.8 }] },
  lane: { opponentId: 6, minutes: [{ minute: 10, own: { gold: 3500, xp: 4000, cs: 80, level: 8 }, opponent: { gold: 3200, xp: 4200, cs: 85, level: 8 } }] },
  winProbability: {
    modelVersion: "win-probability-16.19-v1",
    team: 100,
    points: [60_000, 120_000, 180_000, 240_000].map((timestampMs, i) => ({ timestampMs, probability: [0.5, 0.55, 0.3, 0.35][i] })),
    keyMoments: [{ minute: 3, delta: -0.25, probabilityAfter: 0.3, events: [{ kind: "DEATH", team: 200, involvesParticipant: true }, { kind: "BARON", team: 200, involvesParticipant: false }] }],
  },
  winProbabilityUnavailable: null,
});

for (const width of [360, 1280]) {
  test.describe(`revisão da partida em ${width} px`, () => {
    test.beforeEach(async ({ page }) => {
      await page.route("https://**/*", (route) => route.abort());
      await page.setViewportSize({ width, height: 900 });
    });

    test("compras rotuladas, habilidades, rota e momentos-chave, com linguagem de popularidade", async ({ page }) => {
      let participant: string | null = null;
      await mockApi(page, {
        "GET /api/v1/matches/:id/review": ({ url }) => {
          participant = url.searchParams.get("participantId");
          return ok(review);
        },
      });
      await page.goto("/matches/DEMO-0008?participant=1");
      const section = page.getByRole("region", { name: "Revisão da partida" });
      await expect(section.getByText("1 compra analisada de 1: 1 comum, 0 alternativas, 0 incomuns.")).toBeVisible();
      await expect(section).toContainText("Comum não quer dizer certo");
      await expect(section).toContainText("pouco comum (5% dos jogadores). A mais comum é Q › E › W (80%)");
      await expect(section.getByRole("table")).toContainText("+300 vs");
      await expect(section).toContainText("3 min: −25 pontos percentuais (você morreu, Barão do adversário).");
      await expect(section.getByRole("img", { name: /Chance estimada de vitória/ })).toBeVisible();
      expect(participant).toBe("1");
      await noHorizontalScroll(page);
    });

    test("o Histórico mostra o selo com as compras analisadas", async ({ page }) => {
      await mockApi(page, {
        "GET /api/v1/matches": () => ok({ items: [matchSummary({ localParticipantId: 1, reviewPurchases: { ready: 3, total: 4 } })], nextCursor: null }),
      });
      await page.goto("/historico");
      await expect(page.getByText("Revisão: 3 de 4 compras")).toBeVisible();
      await noHorizontalScroll(page);
    });
  });
}
