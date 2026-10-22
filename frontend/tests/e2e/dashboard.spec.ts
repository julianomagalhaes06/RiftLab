import { expect, test } from "@playwright/test";
import { dashboard } from "../../src/test/dashboard";
import { mockApi, ok } from "./support/mock-api";

for (const width of [360, 1280]) {
  test(`Painel: amostra, contexto, teclado e largura ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await mockApi(page, {
      "GET /api/v1/dashboard": () => ok(dashboard()),
      "GET /api/v1/ranked/me": () => ok({ status: "UNAVAILABLE", reason: "NO_OBSERVATION", position: null, change: null }),
    });
    await page.goto("/painel");
    await expect(page.getByText("57,14%")).toBeVisible();
    await expect(page.getByText(/4 vitórias em 7 partidas/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `../.private/validation/sprint-3/CC-309/dashboard-${width}.png`, fullPage: true });
    const context = page.getByRole("link", { name: "Contexto da partida BR1_3090001" });
    for (let step = 0; step < 30 && !(await context.evaluate((element) => element === document.activeElement)); step += 1) {
      await page.keyboard.press("Tab");
    }
    await expect(context).toBeFocused();
    await expect(page.getByRole("link", { name: "Ver as 8 partidas no Histórico" })).toHaveAttribute("href", "/historico");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/historico\/BR1_3090001\?participant=4/);
  });
}
