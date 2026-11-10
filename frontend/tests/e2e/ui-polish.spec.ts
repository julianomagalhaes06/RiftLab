import { expect, test, type Locator, type Page } from "@playwright/test";
import { mockApi, ok } from "./support/mock-api";

/**
 * Polimento visual (revisão better-ui + ui-ux-pro-max, 09/11/2026): alvos de toque de 44 px
 * medidos pelo ponto que o navegador realmente acerta, escala 0,96 ao clicar, saída curta do
 * aviso de conta criada, contorno dos ícones de item e troca de tema sem transição de cor.
 * As capturas dos dois temas em 360, 1280 e 1440 px ficam para conferência visual.
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
  const toggle = page.getByRole("switch", { name: "Tema claro" });
  await expectTarget44(toggle);
  await expectTarget44(page.locator(".app__topbar .player-search input"));
  await expectTarget44(page.locator(".app__topbar .player-search__submit"));
  await expectTarget44(page.locator(".app__topbar .player-search__region select"));
  // Seletor de servidor, campo e botão com a mesma altura no topo.
  const heights = await page.locator(".app__topbar .player-search").evaluate((form) =>
    [".player-search__region", "input", ".player-search__submit"].map((selector) => Math.round(form.querySelector(selector)!.getBoundingClientRect().height)));
  expect(new Set(heights).size, heights.join(" / ")).toBe(1);
  await expectTarget44(page.locator(".panel-filters select").first());
  await expectPressScale(page, page.locator(".app__topbar .player-search__submit"));
  await expectPressScale(page, toggle);

  await page.goto("/historico");
  const segmented = page.locator(".segmented > button").first();
  await expectTarget44(segmented);
  await expectPressScale(page, segmented);

  await page.goto("/historico/DEMO-0008?participant=1");
  const chip = page.locator(".decision-chip").first();
  await expectTarget44(chip);
  await expectPressScale(page, chip, { navigates: true });
  await expect(page).toHaveURL(/\/decisions\//);
  await page.goBack();
  // Contorno de 1 px por cima do ícone de item, como no retrato de campeão.
  const item = page.locator(".item-icon").first();
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

test("troca de tema sem transição de cor em nenhum elemento", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await offline(page);
  await mockApi(page);
  await page.goto("/historico/DEMO-0008?participant=1");
  await page.getByRole("switch", { name: "Tema claro" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  const colorTransitions = await page.evaluate(() => [...document.querySelectorAll("*")].flatMap((element) => {
    const style = getComputedStyle(element);
    const animated = style.transitionDuration.split(",").some((value) => parseFloat(value) > 0);
    return animated && /all|color|background|border|shadow/.test(style.transitionProperty)
      ? [`${element.tagName.toLowerCase()}.${element.className}: ${style.transitionProperty}`] : [];
  }));
  expect(colorTransitions).toEqual([]);
});

test("no tema claro, o seletor de fila do card de elo (que segue escuro) tem texto legível", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.addInitScript(() => window.localStorage.setItem("riftlab:theme", "light"));
  await offline(page);
  await mockApi(page);
  await page.goto("/painel");
  const select = page.locator(".rank-card__controls select");
  await expect(select).toBeVisible();
  const [color, background] = await select.evaluate((element) => [getComputedStyle(element).color, getComputedStyle(element).backgroundColor]);
  expect(color).toBe("rgb(236, 237, 239)");
  expect(background).toBe("rgb(22, 24, 29)");
});

for (const theme of ["dark", "light"] as const) {
  for (const width of [360, 1280, 1440]) {
    test(`capturas em ${theme} ${width}px sem rolagem lateral`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((value) => window.localStorage.setItem("riftlab:theme", value), theme);
      await offline(page);
      await mockApi(page);
      for (const [name, path] of [["painel", "/painel"], ["historico", "/historico"], ["partida", "/historico/DEMO-0008?participant=1"]]) {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        await noHorizontalScroll(page);
        await page.screenshot({ path: `${evidence}/${name}-${theme}-${width}.png`, fullPage: true });
      }
    });
  }
}
