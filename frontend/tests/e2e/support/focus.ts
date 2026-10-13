import { expect, type Page } from "@playwright/test";

/** Ajudas de teclado compartilhadas pelos specs de navegação só com teclado. */
export async function focusedLabel(page: Page): Promise<string> {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el) {
      return "";
    }
    return (el.getAttribute("aria-label") ?? el.innerText ?? el.textContent ?? "").replace(/\s+/g, " ").trim();
  });
}

export async function tabTo(page: Page, name: RegExp | string, maxTabs = 60) {
  for (let step = 0; step < maxTabs; step += 1) {
    await page.keyboard.press("Tab");
    const label = await focusedLabel(page);
    if (typeof name === "string" ? label === name : name.test(label)) {
      return label;
    }
  }
  throw new Error(`Foco não chegou a ${String(name)} em ${maxTabs} Tab`);
}

export async function expectFocusVisible(page: Page) {
  const focus = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return {
      focusVisible: el.matches(":focus-visible"),
      outlineStyle: style.outlineStyle,
      outlineWidth: parseFloat(style.outlineWidth),
      onScreen: rect.right > 0 && rect.left < window.innerWidth && rect.bottom > 0 && rect.top < window.innerHeight,
    };
  });
  expect(focus.focusVisible).toBe(true);
  expect(focus.outlineStyle).not.toBe("none");
  expect(focus.outlineWidth).toBeGreaterThanOrEqual(2);
  expect(focus.onScreen).toBe(true);
}

export const activeId = (page: Page) => page.evaluate(() => document.activeElement?.id ?? "");
