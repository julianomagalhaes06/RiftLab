import { expect, test } from "@playwright/test";
import { mockApi, ok } from "./support/mock-api";

const evidence = "../.private/validation/sprint-3/CC-310";
const rank = {
  status: "READY", reason: null,
  position: { queue: "RANKED_SOLO_5x5", season: "2026-3", observedAt: "2026-09-28T12:00:00Z",
    origin: "RIOT_LEAGUE_V4", state: "READY", tier: "GOLD", division: "III",
    leaguePoints: 76, wins: 20, losses: 10 },
  change: { since: "2026-09-27T12:00:00Z", until: "2026-09-28T12:00:00Z",
    kind: "SAME_DIVISION", leaguePointsDelta: 12, ownMatchAssociation: "MULTIPLE" },
};

for (const width of [360, 1280]) {
  test(`seletor Solo/Duo e Flex em ${width}px preserva dados, teclado e fila no Painel`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page, { "GET /api/v1/ranked/me": ({ url }) => ok(url.searchParams.get("queue") === "RANKED_FLEX_SR"
      ? { ...rank, position: { ...rank.position, queue: "RANKED_FLEX_SR", tier: "DIAMOND", division: "IV", leaguePoints: 29, wins: 55, losses: 22 },
        change: { ...rank.change, leaguePointsDelta: -8 } }
      : rank) });
    await page.goto("/minha-posicao");
    await expect(page.getByText("Elo Diamante IV, 29 de 100 PDL")).toBeVisible();
    const selector = page.getByLabel("Fila ranqueada");
    await expect(selector).toHaveValue("RANKED_FLEX_SR");
    await expect(page.getByText("Seleção automática · melhor classificação disponível")).toBeVisible();
    await selector.selectOption("RANKED_SOLO_5x5");
    await selector.focus();
    await page.keyboard.press("End");
    await page.keyboard.press("Enter");
    await expect(selector).toHaveValue("RANKED_FLEX_SR");
    await expect(selector).toBeFocused();
    await expect(page.getByText("Elo Diamante IV, 29 de 100 PDL")).toBeVisible();
    await expect(page.getByText("55 vitórias · 22 derrotas na fila")).toBeVisible();
    await expect(page.getByText("-8 PDL desde a observação anterior, na mesma divisão.")).toBeVisible();
    await expect(page.getByText("Elo Ouro III, 76 de 100 PDL")).toHaveCount(0);
    expect(await selector.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `../.private/validation/ranked-flex/flex-${width}.png`, fullPage: true });
    await selector.selectOption("RANKED_SOLO_5x5");
    await expect(page.getByText("Elo Ouro III, 76 de 100 PDL")).toBeVisible();
    await page.goto("/painel");
    await page.getByLabel("Fila ranqueada").selectOption("RANKED_FLEX_SR");
    await expect(page.locator(".my-rank--compact").getByText("Elo Diamante IV, 29 de 100 PDL")).toBeVisible();
  });
}

test("Flex sem observação e não classificado não herda a posição Solo/Duo", async ({ page }) => {
  let unranked = false;
  await mockApi(page, { "GET /api/v1/ranked/me": ({ url }) => ok(url.searchParams.get("queue") !== "RANKED_FLEX_SR" ? rank
    : unranked ? { ...rank, status: "UNRANKED", change: null, position: { ...rank.position,
      queue: "RANKED_FLEX_SR", state: "UNRANKED", tier: null, division: null, leaguePoints: null, wins: null, losses: null } }
      : { status: "UNAVAILABLE", reason: "NO_OBSERVATION", position: null, change: null }) });
  await page.goto("/minha-posicao");
  await expect(page.getByText("Elo Ouro III, 76 de 100 PDL")).toBeVisible();
  await page.getByLabel("Fila ranqueada").selectOption("RANKED_FLEX_SR");
  await expect(page.getByText(/Ainda não há observação/)).toBeVisible();
  await expect(page.getByText("Elo Ouro III, 76 de 100 PDL")).toHaveCount(0);
  unranked = true;
  await page.reload();
  await page.getByLabel("Fila ranqueada").selectOption("RANKED_FLEX_SR");
  await expect(page.getByText("Não classificado na Flex nesta observação válida.")).toBeVisible();
});

for (const width of [360, 390, 1280, 1440]) {
  test(`Minha posição e card no Painel em ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 700 ? 780 : 900 });
    await mockApi(page, { "GET /api/v1/ranked/me": () => ok(rank) });
    await page.goto("/minha-posicao");
    await expect(page.getByRole("heading", { name: "Classificação da conta própria" })).toBeVisible();
    await expect(page.getByText("Elo Ouro III, 76 de 100 PDL")).toBeVisible();
    await expect(page.getByText("Faltam 24 PDL para Ouro II")).toBeVisible();
    await expect(page.getByText("+12 PDL desde a observação anterior, na mesma divisão.")).toBeVisible();
    await expect(page.getByText(/sem atribuição a uma partida/)).toBeVisible();
    await expect(page.locator(".my-rank__emblem img")).toHaveAttribute("src", "/ranked-emblems/gold.webp");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `${evidence}/posicao-${width}.png`, fullPage: true });
    const panelLink = page.getByRole("navigation", { name: "Áreas principais" }).getByRole("link", { name: /Painel/ });
    await panelLink.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator(".my-rank--compact").getByText("Elo Ouro III, 76 de 100 PDL")).toBeVisible();
    await page.screenshot({ path: `${evidence}/painel-${width}.png`, fullPage: true });
  });
}

test("estados não classificado e atualização indisponível", async ({ page }) => {
  let response: Record<string, unknown> = { ...rank, status: "UNRANKED", position: { ...rank.position, state: "UNRANKED", tier: null, division: null, leaguePoints: null, wins: null, losses: null }, change: null };
  await mockApi(page, { "GET /api/v1/ranked/me": () => ok(response) });
  await page.goto("/minha-posicao");
  await expect(page.getByText("Não classificado na Solo/Duo nesta observação válida.")).toBeVisible();
  response = { ...rank, status: "STALE", reason: "RIOT_KEY_MISSING" };
  await page.reload();
  await expect(page.getByText(/A chave da Riot está ausente/)).toBeVisible();
  await expect(page.getByText("Elo Ouro III, 76 de 100 PDL")).toBeVisible();
});

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16) / 255);
  const linear = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}
function contrast(first: string, second: string): number {
  const values = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

test("todos os elos preservam rótulo e contraste AA", async ({ page }) => {
  let currentTier = "IRON";
  await mockApi(page, { "GET /api/v1/ranked/me": () => ok({ ...rank, position: {
    ...rank.position, tier: currentTier, division: ["MASTER", "GRANDMASTER", "CHALLENGER"].includes(currentTier) ? null : "IV",
  } }) });
  for (const tier of ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]) {
    currentTier = tier;
    await page.goto("/minha-posicao");
    const card = page.locator(".my-rank");
    await expect(card).toHaveAttribute("data-tier", tier);
    await expect(card.locator(".my-rank__emblem img")).toHaveJSProperty("complete", true);
    const colors = await card.evaluate((element) => {
      const style = getComputedStyle(element);
      return { accent: style.getPropertyValue("--tier-accent").trim(), glow: style.getPropertyValue("--tier-glow").trim() };
    });
    for (const background of ["#171a20", colors.glow]) {
      for (const foreground of [colors.accent, "#b3bac6", "#f4f5f7"]) {
        expect(contrast(foreground, background), `${tier}: ${foreground} em ${background}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  }
});

test("demonstração: elo fictício aparece marcado e sem atribuição à Riot", async ({ page }) => {
  await mockApi(page, {
    "GET /api/v1/ranked/me": () =>
      ok({ ...rank, position: { ...rank.position, origin: "DEMO_FICTITIOUS", season: "DEMO" } }),
  });
  await page.goto("/minha-posicao");
  await expect(page.getByText(/Elo fictício de demonstração: não foi consultado na Riot/)).toBeVisible();
  await expect(page.getByText(/Demonstração \(fictício\)/)).toBeVisible();
  await expect(page.getByText("Riot League-V4", { exact: false })).toHaveCount(0);
});
