import { expect, test } from "@playwright/test";
import { DECISION_1, mockApi } from "./support/mock-api";

for (const width of [360, 1280]) {
  test(`análise direta e prática separadas em ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const calls = await mockApi(page);
    await page.goto(`/decisions/${DECISION_1}`);
    await expect(page.getByRole("heading", { name: "Comparação", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Analisar partida", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText(/Qual destas você compraria/)).toHaveCount(0);
    expect(calls.count("POST /api/v1/learning-attempts")).toBe(0);
    expect(calls.count("POST /api/v1/analyses")).toBe(1);
    await expect(page.getByRole("link", { name: "Baixar análise HTML" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `../.private/validation/analysis-mode/analise-${width}.png`, fullPage: true });
    const practice = page.getByRole("button", { name: "Praticar decisão", exact: true });
    await practice.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/mode=practice/);
    await expect(page.getByRole("heading", { name: "Comparação", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Confirmar objetivo e começar" }).click();
    await expect(page.getByText(/Qual destas você compraria/)).toBeVisible();
    expect(calls.count("POST /api/v1/learning-attempts/:id/reveal")).toBe(0);
    await page.getByRole("button", { name: "Revelar comparação" }).click();
    await expect(page.getByRole("heading", { name: "Comparação", exact: true })).toBeVisible();
  });
}
