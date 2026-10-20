import { expect, test } from "@playwright/test";
import { mockApi, matchSummary, ok } from "./support/mock-api";

const evidence = "../.private/validation/sprint-3/CC-308";

for (const width of [1280, 1440, 360, 390]) {
  test(`conceito C em ${width}px: navegação, Histórico e layout`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 700 ? 780 : 900 });
    await mockApi(page, {
      "GET /api/v1/matches": ({ url }) => ok({
        items: [
          matchSummary({ matchId: "BR1_123", patch: "16.17", championsPresent: [{ championId: 51, name: "Caitlyn" }] }),
          matchSummary({ matchId: "DEMO-0008" }),
        ].filter((match) => {
          const scope = url.searchParams.get("scope");
          const demo = match.matchId.startsWith("DEMO-");
          return scope === "account" ? !demo : scope === "demo" ? demo : true;
        }),
        nextCursor: null,
      }),
    });

    await page.goto("/painel");
    await expect(page.getByRole("heading", { name: /Entenda a partida/ })).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Áreas principais" });
    await expect(nav.getByRole("link", { name: /Painel/ })).toHaveAttribute("aria-current", "page");
    await expect(nav.getByRole("link", { name: /Painel/ })).toContainText("área atual");
    await page.screenshot({ path: `${evidence}/painel-${width}.png`, fullPage: true });

    for (const [label, heading] of [
      ["Campeões", "Padrões de compra por campeão"],
      ["Minha posição", "Classificação da conta própria"],
      ["Histórico", "Partidas para estudar"],
    ]) {
      const link = nav.getByRole("link", { name: new RegExp(label) });
      await link.focus();
      expect(await link.evaluate((element) => element.matches(":focus-visible"))).toBe(true);
      expect(await link.evaluate((element) => parseFloat(getComputedStyle(element).outlineWidth))).toBeGreaterThanOrEqual(2);
      await page.keyboard.press("Enter");
      await expect(page.getByRole("heading", { name: heading })).toBeVisible();
      await expect(nav.getByRole("link", { name: new RegExp(label) })).toHaveAttribute("aria-current", "page");
      await expect(page.locator("#conteudo")).toBeFocused();
    }

    await expect(page.getByText("1 partida carregada", { exact: true })).toBeVisible();
    await expect(page.getByText("1 RIOT · 0 DEMO")).toBeVisible();
    await expect(page.locator(".match-card")).toHaveCount(1);
    await expect(page.getByRole("link", { name: "Contexto da partida DEMO-0008" })).toHaveCount(0);
    await page.getByRole("button", { name: "Minha conta e DEMO", exact: true }).click();
    await expect(page.getByText("2 partidas carregadas")).toBeVisible();
    await expect(page.getByText("1 RIOT · 1 DEMO")).toBeVisible();
    await expect(page.locator(".match-card")).toHaveCount(2);
    await expect(page.locator(".match-card").getByText("DEMO", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Vitórias" })).toHaveCount(0);
    await expect(page.getByText(/Filtro de resultado indisponível/)).toBeVisible();
    await page.screenshot({ path: `${evidence}/historico-${width}.png`, fullPage: true });

    const metrics = await page.evaluate(() => ({
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
      navBottom: document.querySelector(".main-nav")?.getBoundingClientRect().bottom,
      viewportHeight: window.innerHeight,
      mainBottomPadding: parseFloat(getComputedStyle(document.querySelector("main")!).paddingBottom),
    }));
    expect(metrics.documentWidth).toBeLessThanOrEqual(metrics.viewportWidth);
    if (width < 700) {
      expect(metrics.navBottom).toBe(metrics.viewportHeight);
      expect(metrics.mainBottomPadding).toBeGreaterThanOrEqual(100);
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      const clearOfNav = await page.evaluate(() => {
        const footerEnd = document.querySelector(".app__footer p:last-child")!.getBoundingClientRect().bottom;
        const navTop = document.querySelector(".main-nav")!.getBoundingClientRect().top;
        return footerEnd <= navTop;
      });
      expect(clearOfNav).toBe(true);
    }

    await page.getByLabel("Buscar campeão ou partida").fill("Caitlyn");
    await expect(page.getByRole("link", { name: "Contexto da partida BR1_123" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Contexto da partida DEMO-0008" })).toHaveCount(0);
    await page.getByLabel("Buscar campeão ou partida").clear();
    await page.getByRole("button", { name: "DEMO", exact: true }).click();
    await expect(page.getByRole("link", { name: "Contexto da partida DEMO-0008" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Contexto da partida BR1_123" })).toHaveCount(0);
    await page.getByRole("link", { name: "Contexto da partida DEMO-0008" }).click();
    await expect(page).toHaveURL(/\/historico\/DEMO-0008$/);
    await expect(page.getByRole("heading", { name: /^Partida DEMO-0008/ })).toBeVisible();
    await page.getByRole("link", { name: /^Participante 1 —/ }).click();
    await expect(page.getByRole("heading", { name: /^Decisões de Campeão #9000/ })).toBeVisible();
    await page.getByRole("link", { name: "Decisão em 7:52" }).click();
    await expect(page).toHaveURL(/\/decisions\//);
    await page.goBack();
    await expect(page).toHaveURL(/\/historico\/DEMO-0008\?participant=1$/);
  });
}
