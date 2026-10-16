import { expect, test } from "@playwright/test";
import { DECISION_1, mockApi } from "./support/mock-api";

for (const width of [1280, 360]) {
  test(`glossário em ${width}px: teclado, estudo e prática sem ajuda`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const api = await mockApi(page);
    await page.goto(`/decisions/${DECISION_1}`);
    const glossary = page.locator("details.glossary");
    const summary = glossary.locator("summary");
    await expect(summary).toBeVisible();
    await expect(glossary).not.toHaveAttribute("open");
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(glossary).toHaveAttribute("open", "");
    await expect(glossary.getByText("Composição", { exact: true })).toBeVisible();
    await expect(glossary.getByText("Custo restante", { exact: true })).toBeVisible();
    await expect(glossary.getByText(/Uma compra frequente não é necessariamente/)).toBeVisible();
    expect(await summary.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
    await page.screenshot({ path: `../.private/validation/sprint-3/CC-305/glossario-${width}.png`, fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.keyboard.press("Space");
    await expect(glossary).not.toHaveAttribute("open");
    await page.getByLabel(/Praticar sem pistas/).check();
    await expect(summary).toHaveCount(0);
    await page.getByLabel(/Praticar sem pistas/).uncheck();
    await expect(summary).toBeVisible();
    await page.getByRole("button", { name: "Confirmar objetivo e começar" }).click();
    await expect(page.getByText("Qual destas você compraria? (ordem sem significado)")).toBeVisible();
    await expect(summary).toBeVisible();
    await summary.click();
    expect(api.count("POST /api/v1/learning-attempts/:id/hint")).toBe(0);
    expect(api.count("POST /api/v1/learning-attempts/:id/reveal")).toBe(0);
    await page.getByRole("button", { name: "Revelar comparação" }).click();
    await expect(page.getByRole("heading", { name: "Comparação", exact: true })).toBeVisible();
    await expect(summary).toBeVisible();
  });
}
