import { expect, test } from "@playwright/test";
import { championPatterns } from "../../src/test/championPatterns";
import { mockApi, ok } from "./support/mock-api";

for (const width of [1280, 360]) {
  test(`padrões em ${width}px: filtros, teclado, amostra e fallback de imagens`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("https://ddragon.leagueoflegends.com/**", (route) => route.abort());
    await mockApi(page, {
      "GET /api/v1/champions/patterns": ({ url }) => ok(championPatterns(
        url.searchParams.get("championId") === "222" ? {
          championId: 222, patterns: [], status: "SMALL_SAMPLE", patternDenominator: 2,
        } : {})),
    });
    await page.goto("/campeoes?champion=51&patch=16.19");
    await expect(page.getByText("Infinity Edge")).toBeVisible();
    await expect(page.getByText("3 de 5")).toBeVisible();
    await page.screenshot({ path: `../.private/validation/sprint-3/CC-307/padroes-${width}.png`, fullPage: true });
    await expect(page.getByText(/Sem ranking de itens validado/)).toBeVisible();
    const jinx = page.getByRole("button", { name: /Jinx/ });
    await jinx.focus();
    await page.keyboard.press("Enter");
    await expect(jinx).toHaveAttribute("aria-pressed", "true");
    await expect(jinx).toBeFocused();
    await expect(page.getByText("Amostra insuficiente para publicar padrões")).toBeVisible();
    await expect(page.getByText("Infinity Edge")).toHaveCount(0);
    await page.getByLabel("Etapa da build").selectOption("second");
    await expect(page).toHaveURL(/phase=second/);
    await page.goBack();
    await expect(page.getByLabel("Etapa da build")).toHaveValue("sequence");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `../.private/validation/sprint-3/CC-307/campeoes-${width}.png`, fullPage: true });
    await expect(page.getByRole("link", { name: "Abrir contexto" })).toHaveAttribute("href", "/historico/BR1_123?participant=4");
  });
}
