import { expect, test } from "@playwright/test";
import { championPatterns } from "../../src/test/championPatterns";
import { mockApi, ok } from "./support/mock-api";

const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGP4DwABAQEAG7buVgAAAABJRU5ErkJggg==", "base64");

test("trocar de campeão e voltar: texto sem piscar, arte do escolhido e fade em toda troca", async ({ page }) => {
  await page.route("https://ddragon.leagueoflegends.com/**", (route) => route.fulfill({ contentType: "image/png", body: PNG }));
  await mockApi(page, {
    "GET /api/v1/champions/patterns": ({ url }) => ok(championPatterns(
      url.searchParams.get("championId") === "222" ? { championId: 222 } : {})),
  });
  await page.goto("/campeoes?champion=51&patch=16.19");
  const art = page.locator(".champion-overview__art");
  await expect(art.last()).toHaveAttribute("data-state", "ready");
  // Ida, volta (arte já em cache) e ida de novo: o fade da arte não pode depender do cache.
  for (const [name, file] of [["Jinx", "Jinx_0.jpg"], ["Caitlyn", "Caitlyn_0.jpg"], ["Jinx", "Jinx_0.jpg"]]) {
    // Em cada quadro da troca: o texto do cabeçalho não cai (era o piscar) e a arte nova passa por
    // opacidades intermediárias (o fade rodou).
    const frames = page.evaluate(() => new Promise<{ lowestText: number; artFaded: boolean }>((done) => {
      const inner = document.querySelector(".champion-overview__inner")!;
      let lowestText = 1;
      let artFaded = false;
      const start = performance.now();
      const tick = () => {
        lowestText = Math.min(lowestText, Number(getComputedStyle(inner).opacity));
        const top = [...document.querySelectorAll(".champion-overview__art")].at(-1);
        const art = top ? Number(getComputedStyle(top).opacity) : 1;
        if (art > 0.05 && art < 0.95) artFaded = true;
        if (performance.now() - start < 900) requestAnimationFrame(tick); else done({ lowestText, artFaded });
      };
      requestAnimationFrame(tick);
    }));
    await page.getByRole("button", { name: new RegExp(name) }).click();
    expect(await frames).toEqual({ lowestText: 1, artFaded: true });
    await expect(page.locator(".champion-overview h3 .champion__name")).toHaveText(name);
    // A camada de cima é a arte do campeão escolhido e chega a aparecer (voltar não a deixa presa).
    await expect(art.last()).toHaveAttribute("src", new RegExp(`${file}$`));
    await expect(art.last()).toHaveAttribute("data-state", "ready");
  }
});

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
