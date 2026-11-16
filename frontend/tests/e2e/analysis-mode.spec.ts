import { expect, test } from "@playwright/test";
import { DECISION_1, DECISION_2, decisionState, decisionSummary, mockApi, ok } from "./support/mock-api";

for (const width of [360, 1280]) {
  test(`análise direta e prática separadas em ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const calls = await mockApi(page);
    await page.goto(`/decisions/${DECISION_1}`);
    await expect(page.getByRole("heading", { name: "Comparação", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Analisar decisão", exact: true })).toHaveAttribute("aria-pressed", "true");
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

test("trocar de momento mantém decisão e análise na tela, sem piscar nem voltar ao topo", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const later = { decisionId: DECISION_2, timestampMs: 905_000 };
  await mockApi(page, {
    "GET /api/v1/decisions/:id": ({ url }) => ok(url.pathname.endsWith(DECISION_2) ? decisionState(later) : decisionState()),
    "GET /api/v1/matches/:id/decisions": () => ok({ items: [decisionSummary(), decisionSummary(later)], nextCursor: null }),
  });
  // Conexão lenta: decisão, rastro e análise do momento novo demoram 400 ms cada.
  await page.route("**/api/v1/**", async (route) => {
    if (/\/decisions\/|\/analyses$/.test(new URL(route.request().url()).pathname)) await new Promise((r) => setTimeout(r, 400));
    await route.fallback();
  });
  await page.goto(`/decisions/${DECISION_1}`);
  await expect(page.getByRole("heading", { name: "Comparação", exact: true })).toBeVisible();
  const strip = page.getByRole("link", { name: "Ir para a decisão em 15:05" });
  await strip.scrollIntoViewIfNeeded();
  const scrollBefore = await page.evaluate(() => window.scrollY);
  const frames = page.evaluate(() => new Promise<{ blank: boolean; noComparison: boolean; lowest: number; shortest: number; tallest: number }>((done) => {
    const out = { blank: false, noComparison: false, lowest: 1, shortest: Infinity, tallest: 0 };
    const start = performance.now();
    const tick = () => {
      const pageEl = document.querySelector(".decision-page");
      const hero = document.querySelector(".decision-hero");
      if (!hero || document.body.textContent?.includes("Carregando a decisão…")) out.blank = true;
      if (![...document.querySelectorAll("h3")].some((h) => h.textContent === "Comparação")) out.noComparison = true;
      if (hero) out.lowest = Math.min(out.lowest, Number(getComputedStyle(hero).opacity));
      if (pageEl) {
        const height = pageEl.getBoundingClientRect().height;
        out.shortest = Math.min(out.shortest, height);
        out.tallest = Math.max(out.tallest, height);
      }
      if (performance.now() - start < 2000) requestAnimationFrame(tick); else done(out);
    };
    requestAnimationFrame(tick);
  }));
  await strip.click();
  await expect(page.getByRole("link", { name: "Ir para a decisão em 15:05 (atual)" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: /em 15:05/ })).toBeVisible();
  await expect(page.locator(".study-panel")).toHaveAttribute("aria-busy", "false");
  const seen = await frames;
  // Nunca esvazia a página nem tira a comparação; esmaece sem cair abaixo de 0,6; a altura não
  // desaba (a página antiga encolhia para uma linha de "Carregando…").
  expect(seen.blank).toBe(false);
  expect(seen.noComparison).toBe(false);
  expect(seen.lowest).toBeGreaterThanOrEqual(0.59);
  expect(seen.shortest).toBeGreaterThan(seen.tallest * 0.8);
  expect(await page.evaluate(() => window.scrollY)).toBe(scrollBefore);
});

test("trocar de momento: o marcador desliza na faixa e o item que entrou brilha", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const later = {
    decisionId: DECISION_2,
    timestampMs: 905_000,
    inventory: { items: [{ itemId: 100003, quantity: 1, name: "Lâmina Sintética" }], trinketItemId: null },
  };
  await mockApi(page, {
    "GET /api/v1/decisions/:id": ({ url }) => ok(url.pathname.endsWith(DECISION_2) ? decisionState(later) : decisionState()),
    "GET /api/v1/matches/:id/decisions": () => ok({ items: [decisionSummary(), decisionSummary(later)], nextCursor: null }),
  });
  await page.goto(`/decisions/${DECISION_1}`);
  await expect(page.locator(".decision-strip__marker")).toHaveCount(1);
  await expect(page.locator(".inventory-grid__glow")).toHaveCount(0); // entrada direta: nada a comparar
  const motion = page.evaluate(() => new Promise<{ positions: number[]; glow: number[]; finalGlow: number }>((done) => {
    const positions = new Set<number>();
    const glow = new Set<number>();
    let finalGlow = 0;
    const start = performance.now();
    const tick = () => {
      const marker = document.querySelector(".decision-strip__marker");
      if (marker) positions.add(Math.round(new DOMMatrix(getComputedStyle(marker).transform).m41));
      const shine = document.querySelector(".inventory-grid__glow");
      if (shine) {
        finalGlow = Number(getComputedStyle(shine).opacity);
        glow.add(Number(finalGlow.toFixed(2)));
      }
      if (performance.now() - start < 1800) requestAnimationFrame(tick); else done({ positions: [...positions], glow: [...glow], finalGlow });
    };
    requestAnimationFrame(tick);
  }));
  await page.getByRole("link", { name: "Ir para a decisão em 15:05" }).click();
  const { positions, glow, finalGlow } = await motion;
  // Deslizou: passou por posições intermediárias entre o ponto de 7:52 e o de 15:05.
  expect(positions.length).toBeGreaterThan(4);
  expect(Math.max(...positions)).toBeGreaterThan(Math.min(...positions));
  // Brilhou: opacidades intermediárias e um contorno discreto no fim (0,35), não sumiu.
  expect(glow.some((value) => value > 0.05 && value < 0.95)).toBe(true);
  expect(finalGlow).toBeCloseTo(0.35, 2);
  await expect(page.locator(".inventory-grid__slot--new")).toHaveAttribute("title", "Lâmina Sintética");
  await expect(page.getByText(/^Em relação a 7:52: a mais Lâmina Sintética; a menos /)).toBeVisible();
});
