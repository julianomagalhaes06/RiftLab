import { expect, test } from "@playwright/test";
import { championPatterns } from "../../src/test/championPatterns";
import { DECISION_1, comparison, decisionState, mockApi, ok } from "./support/mock-api";

for (const locale of ["pt-BR", "en-US"]) {
  test.describe(`itens em português com navegador ${locale}`, () => {
    test.use({ locale });
    test("inventário, amuleto, escolha e comparação preservam IDs e custos", async ({ page }) => {
      await page.setViewportSize({ width: locale === "pt-BR" ? 1280 : 360, height: 900 });
      let catalogRequests = 0;
      let answeredItemId: unknown = null;
      await page.route("https://ddragon.leagueoflegends.com/**", (route) => {
        if (/\/16\.19\.1\/img\/item\/\d+\.png$/.test(route.request().url())) {
          return route.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=", "base64") });
        }
        if (!route.request().url().endsWith("/16.19.1/data/pt_BR/item.json")) return route.abort();
        catalogRequests += 1;
        return route.fulfill({ json: { version: "16.19.1", data: {
          "3031": { name: "Gume do Infinito" }, "3036": { name: "Lembranças do Lorde Dominik" },
          "3340": { name: "Sentinela Invisível" },
        } } });
      });
      const state = decisionState();
      const attempt = {
        attemptId: "11111111-1111-4111-8111-111111111111", decisionId: DECISION_1, status: "OPEN",
        objective: { priority: "BALANCED" }, context: {}, mode: "STUDY",
        options: [{ itemId: 3031, name: "Infinity Edge" }], hints: [], hintLevel: 0, reasonCodes: [],
        selectedItemId: null, skipped: false, rationale: null, analysisId: null, comparison: null, historicalItemId: null,
      };
      const revealed = comparison(attempt.objective);
      revealed.candidates = [{ ...revealed.candidates[0], itemId: 3031, name: "Infinity Edge" }];
      const calls = await mockApi(page, {
        "GET /api/v1/decisions/:id": () => ok(decisionState({
          versions: { ...state.versions, dataDragonVersion: "16.19.1" },
          inventory: { items: [{ itemId: 3031, quantity: 2, name: "Infinity Edge" }], trinketItemId: 3340 },
        })),
        "POST /api/v1/learning-attempts": () => ok(attempt, 201),
        "POST /api/v1/learning-attempts/:id/answer": ({ body }) => {
          answeredItemId = (body as { itemId: unknown }).itemId;
          return ok({ ...attempt, status: "ANSWERED", selectedItemId: answeredItemId });
        },
        "POST /api/v1/learning-attempts/:id/reveal": () => ok({
          ...attempt, status: "REVEALED", comparison: revealed, historicalItemId: 3031,
        }),
        "GET /api/v1/champions/patterns": () => ok(championPatterns()),
      });
      await page.goto(`/decisions/${DECISION_1}?mode=practice`);
      await expect(page.getByText("Gume do Infinito", { exact: true })).toBeVisible();
      await expect(page.getByText("×2")).toBeVisible();
      await expect(page.getByText("Amuleto: Sentinela Invisível")).toBeVisible();
      const inventoryIcon = page.locator("section[aria-labelledby='inventory-heading'] img").first();
      await expect(inventoryIcon).toHaveAttribute("src", "https://ddragon.leagueoflegends.com/cdn/16.19.1/img/item/3031.png");
      await expect(inventoryIcon).toHaveAttribute("alt", "");
      expect(await inventoryIcon.evaluate((image) => image.getBoundingClientRect().width)).toBe(44); // slot do inventário
      await page.getByRole("button", { name: "Confirmar objetivo e começar" }).click();
      await expect(page.getByRole("radio", { name: "Gume do Infinito" })).toBeVisible();
      await expect(page.locator("label.radio-option img")).toHaveAttribute("src", "https://ddragon.leagueoflegends.com/cdn/16.19.1/img/item/3031.png");
      await page.getByRole("radio", { name: "Gume do Infinito" }).check();
      await page.getByRole("button", { name: "Responder", exact: true }).click();
      await expect(page.getByRole("radio", { name: "Gume do Infinito" })).toBeDisabled();
      expect(answeredItemId).toBe(3031);
      expect(calls.count("POST /api/v1/learning-attempts/:id/reveal")).toBe(0);
      await page.getByRole("button", { name: "Revelar comparação" }).click();
      await expect(page.getByText("1. Gume do Infinito", { exact: true })).toBeVisible();
      await expect(page.getByText(/Faltam 150 de ouro/)).toBeVisible();
      await expect(page.locator(".comparison-list img")).toHaveAttribute("src", "https://ddragon.leagueoflegends.com/cdn/16.19.1/img/item/3031.png");
      await inventoryIcon.evaluate((image) => image.dispatchEvent(new Event("error")));
      await expect(page.locator("section[aria-labelledby='inventory-heading'] .item-icon").first()).toHaveAttribute("data-state", "fallback");
      await expect(page.locator("section[aria-labelledby='inventory-heading']").getByText("Gume do Infinito", { exact: true })).toBeVisible();
      expect(catalogRequests).toBe(1);
      await page.screenshot({ path: `../.private/validation/item-names/inventario-${locale}.png`, fullPage: true });
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(locale === "pt-BR" ? 1280 : 360);
      await page.goto("/campeoes?champion=51&patch=16.19");
      await expect(page.getByText("Gume do Infinito", { exact: true })).toBeVisible();
      await expect(page.getByText("Lembranças do Lorde Dominik", { exact: true })).toBeVisible();
    });
  });
}
