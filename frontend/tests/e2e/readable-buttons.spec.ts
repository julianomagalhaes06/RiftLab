import { expect, test, type Page } from "@playwright/test";
import { DECISION_1, mockApi } from "./support/mock-api";

/**
 * CC-902: todo botão visível precisa de texto legível sobre o próprio fundo. O defeito
 * original: `.button--link` herdava o fundo roxo do botão primário com texto roxo.
 */
async function unreadableButtons(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const channel = (value: number) => {
      const c = value / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    const parse = (color: string) => (color.match(/[\d.]+/g) ?? []).map(Number);
    const luminance = ([r, g, b]: number[]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
    const background = (node: Element | null): number[] => {
      for (let el = node; el; el = el.parentElement) {
        const [r, g, b, a = 1] = parse(getComputedStyle(el).backgroundColor);
        if (a > 0.5) return [r, g, b];
      }
      return [0, 0, 0];
    };
    const bad: string[] = [];
    for (const el of Array.from(document.querySelectorAll("button, a.button"))) {
      const box = el.getBoundingClientRect();
      const text = (el as HTMLElement).innerText.trim();
      if (!text || box.width === 0 || box.height === 0 || (el as HTMLButtonElement).disabled) continue;
      const fg = luminance(parse(getComputedStyle(el).color));
      const bg = luminance(background(el));
      const ratio = (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
      if (ratio < 3) bad.push(`${text} (${ratio.toFixed(2)})`);
    }
    return bad;
  });
}

for (const width of [360, 1280]) {
  test(`botões legíveis nas telas principais em ${width} px`, async ({ page }) => {
    await page.route("https://**/*", (route) => route.abort());
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    for (const path of ["/", "/painel", "/historico", "/campeoes", "/minha-posicao", "/historico/DEMO-0008?participant=1", `/decisions/${DECISION_1}`]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      expect(await unreadableButtons(page), path).toEqual([]);
    }
  });
}
