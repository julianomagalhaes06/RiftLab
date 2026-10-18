import { expect, test, type Page } from "@playwright/test";
import {
  DECISION_1,
  decisionState,
  decisionSummary,
  matchDetail,
  mockApi,
  ok,
} from "./support/mock-api";

const MATCH_ID = "BR1_3120345678";

test.describe("saúde das regras herdadas", () => {
  async function openDecision(page: Page, health: "SUSPENDED" | "UNVERIFIED", retainedReject = false) {
    const calls = await mockApi(page, {
      "GET /api/v1/matches/:id": () => ok(matchDetail({ matchId: MATCH_ID, patch: "16.20" })),
      "GET /api/v1/matches/:id/decisions": () =>
        ok({
          items: [decisionSummary({ eligible: true, reconstructionRulesHealth: health })],
          nextCursor: null,
        }),
      "GET /api/v1/decisions/:id": () =>
        ok(
          decisionState({
            matchId: MATCH_ID,
            versions: {
              ...decisionState().versions,
              reconstructionRules: "INHERITED",
              reconstructionRulesPatch: "16.19",
              reconstructionRulesHealth: health,
              reconstructionRulesCleanParticipants: health === "SUSPENDED" ? 35 : 0,
              reconstructionRulesFailures: health === "SUSPENDED" ? 8 : 0,
              reconstructionRulesVerdict: health === "SUSPENDED" && !retainedReject ? "REJECT" : "NO_SAMPLE",
            },
          }),
        ),
    });
    await page.goto(`/matches/${MATCH_ID}?participant=1`);
    const blockedLink = page.getByRole("link", { name: /Decisão em 7:52/ });
    await expect(blockedLink).toBeVisible();
    await blockedLink.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`/decisions/${DECISION_1}`));
    return calls;
  }

  test("REJECT bloqueia o estudo sem ocultar o contexto, acessível pelo teclado", async ({ page }) => {
    const calls = await openDecision(page, "SUSPENDED");
    await expect(page.getByText(/herança das regras do patch 16\.19 foi suspensa/)).toBeVisible();
    await expect(page.getByText(/8 divergências em 35 participantes limpos/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Inventário antes da decisão" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Estudar esta decisão" })).toHaveCount(0);
    expect(calls.count("POST /api/v1/learning-attempts")).toBe(0);
    expect(calls.count("POST /api/v1/analyses")).toBe(0);
  });

  test("NO_SAMPLE atual mantém a suspensão anterior e explica que falta recontar", async ({ page }) => {
    const calls = await openDecision(page, "SUSPENDED", true);
    await expect(page.getByText(/amostra atual foi invalidada e precisa de recontagem/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Estudar esta decisão" })).toHaveCount(0);
    expect(calls.count("POST /api/v1/learning-attempts")).toBe(0);
    expect(calls.count("POST /api/v1/analyses")).toBe(0);
  });

  test("UNVERIFIED mantém o fluxo disponível em viewport móvel sem rolagem horizontal", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 740 });
    const calls = await openDecision(page, "UNVERIFIED");
    await expect(page.getByText(/Ainda sem amostra para conferir/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Análise da decisão" })).toBeVisible();
    await expect.poll(() => calls.count("POST /api/v1/analyses")).toBe(1);
    await page.getByRole("button", { name: "Praticar decisão" }).click();
    await expect(page.getByRole("heading", { name: "Estudar esta decisão" })).toBeVisible();
    await page.getByRole("button", { name: /Confirmar objetivo e começar/ }).click();
    await expect.poll(() => calls.count("POST /api/v1/learning-attempts")).toBe(1);
    const widths = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
    }));
    expect(widths.document).toBeLessThanOrEqual(widths.viewport);
  });
});
