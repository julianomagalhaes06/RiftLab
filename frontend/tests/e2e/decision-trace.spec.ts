import { expect, test } from "@playwright/test";
import { DECISION_1, mockApi } from "./support/mock-api";

const evidence = "../.private/validation/demo-real/da-riot-ate-a-analise";

for (const width of [360, 1280]) {
  test(`decisão em ${width}px: caminho da Riot até a análise, sem revelar a compra`, async ({ page }) => {
    await page.route("https://**/*", (route) => route.abort());
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await page.goto(`/decisions/${DECISION_1}`);
    const trace = page.getByRole("region", { name: "Da Riot até esta análise" });
    await expect(trace.getByText(/1\.384 eventos registrados pela Riot/)).toBeVisible();
    await expect(trace.getByLabel("Evento bruto da Riot")).toContainText("oculto até a resposta");
    await expect(trace.getByText(/6\.450 de ouro acumulado/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await trace.scrollIntoViewIfNeeded();
    await trace.screenshot({ path: `${evidence}/trilha-${width}.png` });
  });
}
