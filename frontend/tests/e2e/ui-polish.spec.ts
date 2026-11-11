import { expect, test, type Locator, type Page } from "@playwright/test";
import { championPatterns } from "../../src/test/championPatterns";
import { mockApi, ok } from "./support/mock-api";

/**
 * Polimento visual (revisão better-ui + ui-ux-pro-max, 09/11/2026): alvos de toque de 44 px
 * medidos pelo ponto que o navegador realmente acerta, escala 0,96 ao clicar, saída curta do
 * aviso de conta criada, contorno dos ícones de item e nenhuma transição de cor. Só tema escuro
 * (o claro saiu em 11/11/2026); capturas em 360, 1280 e 1440 px para conferência visual.
 */
const evidence = "../.private/validation/ui-polish";
const USER = { id: "11111111-1111-4111-8111-111111111111", email: "jogadora@example.test" };
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=", "base64");

async function offline(page: Page) {
  await page.route("https://ddragon.leagueoflegends.com/**", (route) =>
    /\.png$/.test(route.request().url()) ? route.fulfill({ contentType: "image/png", body: PNG }) : route.abort());
}

/** A área clicável chega a 44 px: os pontos a 1 px da borda dessa área ainda acertam o controle. */
async function expectTarget44(control: Locator) {
  await control.scrollIntoViewIfNeeded();
  const result = await control.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const padY = Math.max(0, (44 - rect.height) / 2);
    const padX = Math.max(0, (44 - rect.width) / 2);
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const points: Array<[number, number]> = [
      [cx, rect.top - padY + 1], [cx, rect.bottom + padY - 1],
      [rect.left - padX + 1, cy], [rect.right + padX - 1, cy],
    ];
    const hits = points.map(([x, y]) => {
      const target = document.elementFromPoint(x, y);
      return target !== null && (target === element || element.contains(target));
    });
    return { size: [Math.round(rect.width), Math.round(rect.height)], hits };
  });
  expect(result.hits, `alvo ${result.size.join("×")}`).toEqual([true, true, true, true]);
}

/** Segurando o clique, o controle encolhe para 0,96; ao soltar, volta. Um link navega ao soltar,
 * então nele só se confere o estado pressionado. */
async function expectPressScale(page: Page, control: Locator, { navigates = false } = {}) {
  await control.scrollIntoViewIfNeeded();
  const box = (await control.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await expect.poll(() => control.evaluate((element) => getComputedStyle(element).scale)).toBe("0.96");
  await page.mouse.up();
  if (navigates) return;
  await expect.poll(() => control.evaluate((element) => getComputedStyle(element).scale)).not.toBe("0.96");
}

async function noHorizontalScroll(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
}

test("alvos de 44 px e escala ao clicar nos controles compactos", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await offline(page);
  await mockApi(page);

  await page.goto("/painel");
  await expectTarget44(page.locator(".app__topbar .player-search input"));
  await expectTarget44(page.locator(".app__topbar .player-search__submit"));
  await expectTarget44(page.locator(".app__topbar .player-search__region select"));
  // Seletor de servidor, campo e botão com a mesma altura no topo.
  const heights = await page.locator(".app__topbar .player-search").evaluate((form) =>
    [".player-search__region", "input", ".player-search__submit"].map((selector) => Math.round(form.querySelector(selector)!.getBoundingClientRect().height)));
  expect(new Set(heights).size, heights.join(" / ")).toBe(1);
  await expectTarget44(page.locator(".panel-filters select").first());
  await expectPressScale(page, page.locator(".app__topbar .player-search__submit"));

  await page.goto("/historico");
  const segmented = page.locator(".segmented > button").first();
  await expectTarget44(segmented);
  await expectPressScale(page, segmented);

  await page.goto("/historico/DEMO-0008?participant=1&aba=compras");
  const chip = page.locator(".decision-chip").first();
  await expectTarget44(chip);
  await expectPressScale(page, chip, { navigates: true });
  await expect(page).toHaveURL(/\/decisions\//);
  await page.goBack();
  // Contorno de 1 px por cima do ícone de item, como no retrato de campeão.
  const item = page.locator(".item-icon:visible").first();
  await expect(item).toBeVisible();
  expect(await item.evaluate((element) => getComputedStyle(element, "::after").boxShadow)).toMatch(/inset/);
});

test("mostrar senha tem alvo de 44 px e o botão primário encolhe ao clicar", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await mockApi(page, { "GET /api/v1/auth/session": () => ok({ authRequired: true, user: null }) });
  await page.goto("/entrar");
  await expectTarget44(page.getByRole("button", { name: /Mostrar senha/ }));
  await expectPressScale(page, page.getByRole("button", { name: "Entrar", exact: true }));
});

test("aviso de conta criada sai com animação curta e some", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await offline(page);
  await mockApi(page, { "GET /api/v1/auth/session": () => ok({ authRequired: true, user: USER, demoEnabled: true }) });
  await page.goto("/campeoes?conta=nova");
  const toast = page.locator(".account-toast");
  await expect(toast).toBeVisible();
  await expectTarget44(page.getByRole("button", { name: "Agora não" }));
  await page.getByRole("button", { name: "Agora não" }).click();
  await expect(toast).toHaveClass(/account-toast--leaving/);
  expect(await toast.evaluate((element) => getComputedStyle(element).animationName)).toBe("account-toast-out");
  await expect(toast).toHaveCount(0);
});

test("só tema escuro: preferência antiga de tema claro é ignorada e não há botão de tema", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.addInitScript(() => window.localStorage.setItem("riftlab:theme", "light"));
  await offline(page);
  await mockApi(page);
  await page.goto("/painel");
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);
  await expect(page.getByRole("switch", { name: /Tema/ })).toHaveCount(0);
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe("rgb(15, 16, 19)");
});

test("nenhum elemento transiciona cor, fundo, borda ou sombra (só scale, opacity e translate)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await offline(page);
  await mockApi(page);
  await page.goto("/historico/DEMO-0008?participant=1");
  const colorTransitions = await page.evaluate(() => [...document.querySelectorAll("*")].flatMap((element) => {
    const style = getComputedStyle(element);
    const animated = style.transitionDuration.split(",").some((value) => parseFloat(value) > 0);
    return animated && /all|color|background|border|shadow/.test(style.transitionProperty)
      ? [`${element.tagName.toLowerCase()}.${element.className}: ${style.transitionProperty}`] : [];
  }));
  expect(colorTransitions).toEqual([]);
});

{
  for (const width of [360, 1280, 1440]) {
    test(`capturas em ${width}px sem rolagem lateral`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await offline(page);
      await mockApi(page);
      for (const [name, path] of [["painel", "/painel"], ["historico", "/historico"], ["partida", "/historico/DEMO-0008?participant=1"]]) {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        await noHorizontalScroll(page);
        await page.screenshot({ path: `${evidence}/${name}-${width}.png`, fullPage: true });
      }
    });
  }
}

// Vitrine do início (revisão ui-ux-pro-max): o texto lê sobre qualquer splash. Pior caso possível:
// a arte trocada por uma imagem toda branca. O texto fica transparente para fotografar só o fundo
// atrás de cada linha; o contraste usa o pixel claro do percentil 95 (o brilho do texto não conta).
const WHITE_PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGP4DwABAQEAG7buVgAAAABJRU5ErkJggg==", "base64");
/** Contraste do texto (cor já lida) contra o pixel claro do percentil 95 do fundo atrás dele;
 * o texto precisa estar transparente antes da captura. */
async function contrastOverBackground(page: Page, selector: string, color: string): Promise<number> {
  const png = (await page.locator(selector).first().screenshot()).toString("base64");
  return page.evaluate(async ({ png, color }) => {
    const image = new Image();
    image.src = `data:image/png;base64,${png}`;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.width;
    canvas.height = image.height;
    const context = canvas.getContext("2d")!;
    context.drawImage(image, 0, 0);
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const channel = (value: number) => { const v = value / 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    const luminance = (r: number, g: number, b: number) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
    const values: number[] = [];
    for (let offset = 0; offset < data.length; offset += 4) values.push(luminance(data[offset], data[offset + 1], data[offset + 2]));
    values.sort((a, b) => a - b);
    const background = values[Math.floor(values.length * 0.95)];
    const [r, g, b] = color.match(/\d+/g)!.map(Number);
    const text = luminance(r, g, b);
    return (Math.max(text, background) + 0.05) / (Math.min(text, background) + 0.05);
  }, { png, color });
}
const HERO_TEXTS: Array<[string, string, number]> = [
  ["selo", ".home-hero__kicker > span:last-child", 4.5],
  ["título", ".home-hero__title", 3],
  ["título roxo", ".home-hero__title span", 3],
  ["frase", ".home-hero__lead", 4.5],
  ["crédito", ".home-hero__credit", 4.5],
];
for (const width of [360, 1280]) {
  test(`vitrine do início: texto legível sobre arte branca em ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("https://ddragon.leagueoflegends.com/**", (route) => route.fulfill({ contentType: "image/png", body: WHITE_PNG }));
    await mockApi(page);
    await page.goto("/");
    await page.locator('.home-hero[data-art="ready"]').waitFor();
    const colors = await page.evaluate((texts) => texts.map(([, selector]) => getComputedStyle(document.querySelector(selector)!).color), HERO_TEXTS);
    await page.addStyleTag({ content: ".home-hero__inner *, .home-hero__credit { color: transparent !important; text-shadow: none !important; } .player-search--hero { visibility: hidden; } .home-hero__art { transition: none !important; transform: none !important; opacity: 1 !important; }" });
    const ratios: Record<string, number> = {};
    for (const [index, [name, selector, minimum]] of HERO_TEXTS.entries()) {
      const ratio = await contrastOverBackground(page, selector, colors[index]);
      ratios[name] = Math.round(ratio * 100) / 100;
      expect(ratio, `${name}: ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(minimum);
    }
    test.info().annotations.push({ type: "contraste", description: JSON.stringify(ratios) });
  });
}

// Histórico refeito (11/11/2026): cartão tingido pelo resultado, placar que abre no próprio cartão
// e coluna de resumo. Campeões: cabeçalho sobre a splash art do campeão.
const CHAMPS: Array<[number, string]> = [[51, "Caitlyn"], [222, "Jinx"], [412, "Thresh"], [64, "LeeSin"], [103, "Ahri"], [21, "MissFortune"], [117, "Lulu"], [86, "Garen"], [202, "Jhin"], [145, "Kaisa"]];
function richMatch(index: number) {
  const win = index % 3 !== 1;
  const participants = CHAMPS.map(([championId, file], position) => ({
    participantId: position + 1, championId, team: position < 5 ? 100 : 200, win: position < 5 ? win : !win,
    champion: { name: file, dataDragonVersion: "16.20.1", imageFile: `${file}.png` },
    role: ["BOTTOM", "UTILITY", "JUNGLE", "MIDDLE", "TOP"][position % 5],
    kills: 2 + position, deaths: 1 + (position % 4), assists: 3 + index, totalMinionsKilled: 150 + position, neutralMinionsKilled: 4,
    finalItems: [3031, 3036, 3006, 3094, 3072, 6672], finalTrinketItemId: 3363,
  }));
  return { matchId: `BR1_70${index}`, queue: 420, map: 11, patch: "16.20", startAt: new Date(Date.now() - index * 7_200_000).toISOString(),
    durationSeconds: 1700, preparationStatus: "READY", supportLevel: "LIMITED", supportReason: "PATCH_NOT_VALIDATED",
    championsPresent: participants.map((p) => ({ championId: p.championId, name: p.champion.name })),
    participants, localParticipantId: 1, reviewPurchases: { ready: 2, total: 5 } };
}

for (const width of [360, 1280]) {
  test(`histórico em ${width}px: placar abre no cartão, alvo de 44 px e sem rolagem lateral`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await offline(page);
    await mockApi(page, { "GET /api/v1/matches": () => ok({ items: [0, 1, 2].map(richMatch), nextCursor: null }) });
    await page.goto("/historico");
    const card = page.locator(".match-card").first();
    await expect(card).toHaveClass(/match-card--win/);
    await expect(page.locator(".match-card--loss")).toHaveCount(1);
    await expect(page.getByRole("list", { name: /Resultados recentes/ }).getByRole("listitem")).toHaveCount(3);
    const toggle = card.getByRole("button", { name: "Mostrar placar da partida" });
    await expectTarget44(toggle);
    await toggle.click();
    await expect(card.getByRole("table", { name: "Time azul Vitória" })).toBeVisible();
    await expect(card.getByRole("table", { name: "Time vermelho Derrota" })).toBeVisible();
    await expect(card.getByRole("button", { name: "Ocultar placar da partida" })).toHaveAttribute("aria-expanded", "true");
    await noHorizontalScroll(page);
    await page.screenshot({ path: `${evidence}/historico-placar-${width}.png`, fullPage: true });
  });
}

for (const width of [360, 1000, 1280]) {
  test(`campeões em ${width}px: cabeçalho legível sobre splash branca e sem rolagem lateral`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("https://ddragon.leagueoflegends.com/**", (route) => route.fulfill({ contentType: "image/png", body: WHITE_PNG }));
    await mockApi(page, { "GET /api/v1/champions/patterns": () => ok(championPatterns()) });
    await page.goto("/campeoes?champion=51&patch=16.19");
    await page.locator('.champion-overview__art[data-state="ready"]').waitFor();
    await noHorizontalScroll(page);
    const texts: Array<[string, number]> = [[".champion-overview__meta", 4.5], [".champion-overview h3 .champion__name", 4.5], [".champion-stats dd", 4.5], [".champion-stats dt", 4.5]];
    const colors = await page.evaluate((list) => list.map(([selector]) => getComputedStyle(document.querySelector(selector)!).color), texts);
    await page.addStyleTag({ content: ".champion-overview__inner * { color: transparent !important; } .champion-overview__inner .champion__portrait, .champion-overview__inner .game-icon { visibility: hidden; } .champion-overview__art { transition: none !important; scale: 1 !important; }" });
    for (const [index, [selector, minimum]] of texts.entries()) {
      const ratio = await contrastOverBackground(page, selector, colors[index]);
      expect(ratio, `${selector}: ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(minimum);
    }
  });
}

for (const width of [360, 1280]) {
  test(`histórico em ${width}px: banner do perfil legível sobre splash branca`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("https://ddragon.leagueoflegends.com/**", (route) => route.fulfill({ contentType: "image/png", body: WHITE_PNG }));
    await mockApi(page, { "GET /api/v1/matches": () => ok({ items: [0, 2, 3].map(richMatch), nextCursor: null }) });
    await page.goto("/historico");
    await page.locator('.history-banner .splash-banner__art[data-state="ready"]').waitFor();
    await expect(page.locator(".history-banner .banner-streak")).toHaveText(/3 vitórias seguidas/);
    await noHorizontalScroll(page);
    const texts: Array<[string, number]> = [[".history-banner__context", 4.5], [".history-banner h2", 3], [".history-banner__who", 4.5], [".history-banner .banner-streak", 4.5], [".history-banner__import", 4.5]];
    const colors = await page.evaluate((list) => list.map(([selector]) => getComputedStyle(document.querySelector(selector)!).color), texts);
    await page.addStyleTag({ content: ".history-banner__inner * { color: transparent !important; } .history-banner__inner img, .history-banner__inner .game-icon, .history-banner__icon { visibility: hidden; } .splash-banner__art { transition: none !important; scale: 1 !important; }" });
    for (const [index, [selector, minimum]] of texts.entries()) {
      const ratio = await contrastOverBackground(page, selector, colors[index]);
      expect(ratio, `${selector}: ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(minimum);
    }
  });
}

for (const width of [360, 1280]) {
  test(`perfil do jogador em ${width}px: banner com a splash do mais jogado, legível sobre arte branca`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const splashes: string[] = [];
    await page.route("https://ddragon.leagueoflegends.com/**", (route) => {
      if (route.request().url().includes("/splash/")) splashes.push(route.request().url());
      return route.fulfill({ contentType: "image/png", body: WHITE_PNG });
    });
    await mockApi(page, { "GET /api/v1/matches": ({ url }) => ok({ items: url.searchParams.get("player") ? [0, 2, 3].map(richMatch) : [], nextCursor: null }) });
    await page.goto("/historico");
    const search = page.getByRole("combobox", { name: "Buscar jogador pelo Riot ID" });
    await search.fill("Jogador Teste");
    await search.press("Enter");
    await expect(page.getByRole("heading", { name: /Jogador Teste #BR1/ })).toBeVisible();
    await page.locator('.player-header .splash-banner__art[data-state="ready"]').waitFor();
    // O jogador pesquisado é o participante 1 (Caitlyn) nas três partidas.
    expect(splashes.at(-1)).toMatch(/\/splash\/Caitlyn_0\.jpg$/);
    await expect(page.locator(".player-header .banner-streak")).toHaveText(/3 vitórias seguidas/);
    await noHorizontalScroll(page);
    const texts: Array<[string, number]> = [[".player-header__who h2", 4.5], [".player-header__meta", 4.5], [".player-header .banner-streak", 4.5]];
    const colors = await page.evaluate((list) => list.map(([selector]) => getComputedStyle(document.querySelector(selector)!).color), texts);
    await page.addStyleTag({ content: ".player-header__who * { color: transparent !important; } .player-header .game-icon, .player-header__icon { visibility: hidden; } .splash-banner__art { transition: none !important; scale: 1 !important; }" });
    for (const [index, [selector, minimum]] of texts.entries()) {
      const ratio = await contrastOverBackground(page, selector, colors[index]);
      expect(ratio, `${selector}: ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(minimum);
    }
  });
}

test("perfil do jogador: resumo, busca, filtros e conquistas como no Histórico da conta", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await offline(page);
  await mockApi(page, { "GET /api/v1/matches": ({ url }) => ok({ items: url.searchParams.get("player") ? [0, 1, 2, 3].map(richMatch) : [], nextCursor: null }) });
  await page.goto("/historico");
  const search = page.getByRole("combobox", { name: "Buscar jogador pelo Riot ID" });
  await search.fill("Jogador Teste");
  await search.press("Enter");
  const summary = page.getByRole("region", { name: "Resumo das partidas carregadas do jogador" });
  await expect(summary.getByText("Taxa de vitória")).toBeVisible();
  await expect(summary.getByRole("list", { name: /Resultados recentes/ }).getByRole("listitem")).toHaveCount(4);
  await expect(page.locator(".player .is-best").first()).toBeVisible();
  await expect(page.locator(".player .match-card")).toHaveCount(4);
  await page.getByRole("group", { name: "Resultado" }).getByRole("button", { name: "Derrotas" }).click();
  await expect(page.locator(".player .match-card")).toHaveCount(1);
  await expect(page.getByText("1 de 4 carregadas neste filtro")).toBeVisible();
  await page.getByRole("group", { name: "Resultado" }).getByRole("button", { name: "Todos" }).click();
  await page.getByRole("searchbox", { name: "Buscar campeão ou partida" }).fill("BR1_702");
  await expect(page.locator(".player .match-card")).toHaveCount(1);
  await noHorizontalScroll(page);
});
