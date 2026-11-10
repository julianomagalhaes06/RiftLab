import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";

/**
 * Roteiro da demonstração (CC-605) contra a stack de demonstração já semeada
 * (`scripts/demo.sh up`, projeto `riftlab-demo`). Só roda no projeto `demo` com E2E_BASE_URL.
 * Cenários: o contexto muda as alternativas (DEMO-0009 × DEMO-0010), evidência insuficiente
 * (DEMO-0011), elo fictício marcado no card do Painel e relatório verificável. O cenário do
 * interpretador indisponível exige reiniciar a API (`scripts/demo.sh parser-offline`) e roda
 * só com DEMO_PARSER_OFFLINE=1.
 */
const evidence = "../.private/validation/sprint-6/CC-605";

async function openDecision(page: Page, matchId: string) {
  await page.goto(`/historico/${matchId}?participant=1`);
  await expect(page.getByRole("heading", { name: "Decisões do participante 1" })).toBeVisible();
  await page.getByRole("link", { name: /^Decisão em / }).first().click();
  await expect(page.getByRole("heading", { name: /^Decisão em .* — participante 1$/ })).toBeVisible();
}

async function revealByForm(page: Page): Promise<string[]> {
  await page.getByRole("button", { name: "Praticar decisão", exact: true }).click();
  await page.getByRole("button", { name: "Confirmar objetivo e começar" }).click();
  await page.getByText("Não sei", { exact: true }).click();
  await page.getByRole("button", { name: "Responder" }).click();
  await page.getByRole("button", { name: "Revelar comparação" }).click();
  await expect(page.getByRole("heading", { name: "Comparação" })).toBeFocused();
  return page.locator(".comparison-list > li > strong").allInnerTexts();
}

test.describe("Roteiro da demonstração (stack de demonstração semeada)", () => {
  test.beforeAll(() => {
    if (!process.env.E2E_BASE_URL) {
      throw new Error("demo exige E2E_BASE_URL (scripts/demo.sh up).");
    }
  });

  test("o contexto muda as alternativas: espadas × colete e gema", async ({ page }) => {
    await openDecision(page, "DEMO-0009");
    const damage = await revealByForm(page);
    await page.screenshot({ path: `${evidence}/contexto-dano.png`, fullPage: true });
    await openDecision(page, "DEMO-0010");
    const defense = await revealByForm(page);
    await page.screenshot({ path: `${evidence}/contexto-defesa.png`, fullPage: true });
    expect(damage.join(" ")).toMatch(/Lâmina Crítica Sintética/);
    expect(defense.join(" ")).toMatch(/Couraça Sintética/);
    expect(damage.filter((item) => defense.includes(item))).toEqual([]);
  });

  test("evidência insuficiente: inventário incerto explica e não oferece alternativas", async ({ page }) => {
    await openDecision(page, "DEMO-0011");
    await expect(page.getByText("Inelegível para análise")).toBeVisible();
    await expect(page.getByText(/incerto/).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Estudar esta decisão" })).toHaveCount(0);
    await page.screenshot({ path: `${evidence}/evidencia-insuficiente.png`, fullPage: true });
  });

  test("card de elo do Painel mostra elo fictício marcado e nunca a Riot", async ({ page }) => {
    await page.goto("/painel");
    await expect(page.getByText(/Elo fictício de demonstração/)).toBeVisible();
    await expect(page.getByText(/Riot League-V4/)).toHaveCount(0);
    await page.screenshot({ path: `${evidence}/painel-elo-ficticio.png`, fullPage: true });
  });

  test("relatório exportado abre sem rede e não traz identificadores", async ({ page }) => {
    await openDecision(page, "DEMO-0009");
    await revealByForm(page);
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("link", { name: /Baixar HTML/ }).click(),
    ]);
    const html = await readFile(await download.path(), "utf-8");
    expect(html).toContain("default-src 'none'");
    expect(html).not.toContain("<script");
    expect(html).not.toContain("DEMO-0009");
    expect(html).not.toMatch(/https?:\/\//);
  });

  test("interpretador indisponível devolve ao formulário (DEMO_PARSER_OFFLINE=1)", async ({ page }) => {
    test.skip(process.env.DEMO_PARSER_OFFLINE !== "1", "rode depois de scripts/demo.sh parser-offline");
    await openDecision(page, "DEMO-0010");
    await page.getByLabel(/descreva em uma frase/).fill("quero aguentar mais");
    await page.getByRole("button", { name: "Interpretar texto" }).click();
    await expect(page.getByText(/O interpretador não respondeu/)).toBeVisible();
    await page.screenshot({ path: `${evidence}/interpretador-indisponivel.png`, fullPage: true });
    await page.getByLabel(/Sobrevivência/).check();
    const alternatives = await revealByForm(page);
    expect(alternatives.length).toBeGreaterThan(0);
  });
});
