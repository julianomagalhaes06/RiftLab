import { expect, test, type Page } from "@playwright/test";
import { expectFocusVisible, focusedLabel, tabTo } from "./support/focus";
import {
  DECISION_1,
  DECISION_2,
  HANG,
  decisionSummary,
  matchSummary,
  mockApi,
  ok,
  problem,
} from "./support/mock-api";

/**
 * Estudo de uma decisão com objetivo por formulário ou por texto (CC-504/505/506), com API
 * simulada: interpretador de regras, modelo local ativo e indisponível, timeout do cliente,
 * relatório, 360 px, teclado e a jornada do conceito C. O fluxo contra a stack real fica em
 * real-stack.spec.ts.
 */
const evidence = "../.private/validation/sprint-5/CC-506";
const decisionUrl = `/decisions/${DECISION_1}?mode=practice`;

/** Registra e bloqueia qualquer requisição para fora da máquina (demo sem rede externa). */
async function blockExternal(page: Page): Promise<string[]> {
  const external: string[] = [];
  await page.route("**/*", (route) => {
    const host = new URL(route.request().url()).hostname;
    if (host === "127.0.0.1" || host === "localhost") {
      return route.fallback();
    }
    external.push(route.request().url());
    return route.abort();
  });
  return external;
}

async function tabToId(page: Page, id: string, maxTabs = 80) {
  for (let step = 0; step < maxTabs; step += 1) {
    await page.keyboard.press("Tab");
    if ((await page.evaluate(() => document.activeElement?.id)) === id) {
      return;
    }
  }
  throw new Error(`Foco não chegou a #${id} em ${maxTabs} Tab`);
}

async function noHorizontalScroll(page: Page) {
  const widths = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
}

test("texto confirmado → estudo → comparação → reflexão → relatório, sem rede externa", async ({ page }) => {
  const external = await blockExternal(page);
  const api = await mockApi(page);
  await page.goto(decisionUrl);
  await expect(page.getByRole("heading", { name: "Estudar esta decisão" })).toBeVisible();

  await page.getByLabel(/descreva em uma frase/).fill("não quero dano, quero aguentar mais");
  await page.getByRole("button", { name: "Interpretar texto" }).click();
  await expect(page.getByText(/Entendi:/)).toContainText("Sobrevivência");
  await expect(page.getByText(/regras fixas, sem modelo de linguagem, versão rules_v1/)).toBeVisible();
  await expect(page.getByText(/não diz qual item é melhor/)).toBeVisible();
  await page.screenshot({ path: `${evidence}/texto-proposta-1280.png`, fullPage: true });
  await page.getByRole("button", { name: "Usar este objetivo" }).click();
  await expect(page.getByLabel(/Sobrevivência/)).toBeChecked();
  await expect(page.getByText(/de texto confirmado \(interpretador rules_v1\)/)).toBeVisible();

  await page.getByRole("button", { name: "Confirmar objetivo e começar" }).click();
  await expect(page.getByText(/Qual destas você compraria/)).toBeFocused();
  await expect(page.locator("body")).not.toContainText(/Faltam \d+ de ouro|histórico/);
  await page.getByRole("button", { name: "Pista: observar ameaças" }).click();
  await expect(page.getByText(/dois personagens de dano físico/)).toBeVisible();
  await page.getByLabel("Item Composto Sintético").check();
  await page.getByRole("button", { name: "Responder" }).click();
  await expect(page.getByRole("button", { name: "Revelar comparação" })).toBeFocused();
  await page.getByRole("button", { name: "Revelar comparação" }).click();
  await expect(page.getByRole("heading", { name: "Comparação" })).toBeFocused();
  await expect(page.getByText(/Objetivo: Sobrevivência \(de texto confirmado \(interpretador rules_v1\)\)/)).toBeVisible();
  await expect(page.getByText(/— sua escolha/)).toBeVisible();
  await expect(page.getByText(/compra feita na partida \(histórico\)/)).toBeVisible();
  await expect(page.getByText(/não é um gabarito/)).toBeVisible();
  await page.screenshot({ path: `${evidence}/comparacao-1280.png`, fullPage: true });

  await page.getByLabel(/O que mudaria na sua escolha/).fill("Com mais magos eu iria de resistência mágica");
  await page.getByRole("button", { name: "Registrar reflexão" }).click();
  await expect(page.getByText(/Sua reflexão registrada/)).toBeFocused();

  // O download real (arquivo salvo e aberto) é coberto pelo real-stack; aqui, link e conteúdo.
  const htmlLink = page.getByRole("link", { name: /Baixar HTML/ });
  await expect(htmlLink).toHaveAttribute("download", "");
  const href = (await htmlLink.getAttribute("href")) ?? "";
  expect(href).toMatch(/\/report\?format=html&attemptId=/);
  const report = await page.evaluate(async (url) => (await fetch(url)).text(), href);
  expect(report).toContain("Relatório de análise de compra");
  await expect(page.getByRole("link", { name: "Baixar JSON" })).toHaveAttribute(
    "href",
    /\/report\?format=json&attemptId=/,
  );

  const created = api.calls.indexOf("POST /api/v1/learning-attempts");
  expect(created).toBeGreaterThan(api.calls.indexOf("POST /api/v1/objectives/interpret"));
  expect(external).toEqual([]);
});

test("modelo local ativo e depois indisponível: o formulário segue e a comparação sai do mesmo jeito", async ({
  page,
}) => {
  let calls = 0;
  const api = await mockApi(page, {
    "POST /api/v1/objectives/interpret": () => {
      calls += 1;
      return calls === 1
        ? ok({
            status: "INTERPRETED",
            objective: { priority: "UTILITY", parserVersion: "qwen2.5-1.5b-lora-v1" },
            parserVersion: "qwen2.5-1.5b-lora-v1",
            provider: "LOCAL_MODEL",
            reasonCodes: [],
          })
        : ok({
            status: "NEEDS_INPUT",
            objective: null,
            parserVersion: "qwen2.5-1.5b-lora-v1",
            provider: "LOCAL_MODEL",
            reasonCodes: ["PROVIDER_ERROR"],
          });
    },
  });
  await page.goto(decisionUrl);
  const text = page.getByLabel(/descreva em uma frase/);
  await text.fill("fico sem mana na rota");
  await page.getByRole("button", { name: "Interpretar texto" }).click();
  await expect(page.getByText(/modelo de linguagem local, versão qwen2.5-1.5b-lora-v1/)).toBeVisible();
  await page.getByRole("button", { name: "Prefiro escolher no formulário" }).click();
  await expect(page.getByRole("group", { name: "Objetivo", exact: true }).locator("legend")).toBeFocused();
  await expect(page.getByLabel(/Equilibrado/)).toBeChecked();

  await text.fill("fico sem mana, o que eu faço?");
  await page.getByRole("button", { name: "Interpretar texto" }).click();
  await expect(page.getByText(/O interpretador não respondeu\. Escolha o objetivo no formulário/)).toBeVisible();
  await expect(page.getByRole("group", { name: "Objetivo", exact: true }).locator("legend")).toBeFocused();
  await page.screenshot({ path: `${evidence}/modelo-indisponivel-1280.png`, fullPage: true });

  await page.getByLabel(/Utilidade/).check();
  await page.getByRole("button", { name: "Confirmar objetivo e começar" }).click();
  await page.getByLabel("Outro Composto Sintético").check();
  await page.getByRole("button", { name: "Responder" }).click();
  await page.getByRole("button", { name: "Revelar comparação" }).click();
  await expect(page.getByText(/Objetivo: Utilidade \(escolhido no formulário\)/)).toBeVisible();
  expect(api.count("POST /api/v1/objectives/interpret")).toBe(2);
});

test("interpretador sem resposta: o cliente desiste em 16 s e o objetivo não muda", async ({ page }) => {
  test.setTimeout(45_000);
  await mockApi(page, { "POST /api/v1/objectives/interpret": () => HANG });
  await page.goto(decisionUrl);
  await page.getByLabel(/Dano/).check();
  await page.getByLabel(/descreva em uma frase/).fill("quero aguentar o burst");

  await page.getByRole("button", { name: "Interpretar texto" }).click();
  await expect(page.getByText("Interpretando…")).toBeVisible();
  await page.getByRole("button", { name: "Cancelar" }).click();
  await expect(page.getByText("Interpretando…")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Interpretar texto" })).toBeEnabled();

  const started = Date.now();
  await page.getByRole("button", { name: "Interpretar texto" }).click();
  await expect(page.getByText(/A interpretação demorou demais/)).toBeVisible({ timeout: 25_000 });
  expect(Date.now() - started).toBeGreaterThanOrEqual(15_000);
  await expect(page.getByLabel(/Dano/)).toBeChecked();
  await expect(page.getByText(/Selecionado: Dano, escolhido no formulário/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirmar objetivo e começar" })).toBeEnabled();
});

test("erro ao revelar: mensagem acessível e nova tentativa", async ({ page }) => {
  let reveals = 0;
  await mockApi(page, {
    "POST /api/v1/learning-attempts/:id/reveal": () => {
      reveals += 1;
      return problem(503, "SERVICE_UNAVAILABLE");
    },
  });
  await page.goto(decisionUrl);
  await page.getByRole("button", { name: "Confirmar objetivo e começar" }).click();
  await page.getByRole("button", { name: "Revelar comparação" }).click();
  const alert = page.getByRole("alert");
  await expect(alert).toBeVisible();
  await expect(alert).not.toContainText("detalhe-do-servidor-que-nao-pode-aparecer");
  await expect(page.getByRole("button", { name: "Revelar comparação" })).toBeEnabled();
  expect(reveals).toBe(1);
});

test("360 px: objetivo por texto, estudo, comparação, reanálise e exportação sem rolagem horizontal", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await mockApi(page);
  await page.goto(decisionUrl);
  await page.getByLabel(/descreva em uma frase/).fill("quero vida e ataque");
  await page.getByRole("button", { name: "Interpretar texto" }).click();
  await expect(page.getByText(/o objetivo selecionado não mudou/)).toBeVisible();
  await noHorizontalScroll(page);
  await page.screenshot({ path: `${evidence}/texto-ambiguo-360.png`, fullPage: true });

  await page.getByLabel(/Sobrevivência/).check();
  await page.getByRole("button", { name: "Confirmar objetivo e começar" }).click();
  await page.getByRole("button", { name: "Pista: custo e atributos" }).click();
  await expect(page.getByText(/faltam 150 de ouro/)).toBeVisible();
  await noHorizontalScroll(page);
  await page.getByLabel("Terceiro Composto Sintético").check();
  await page.getByRole("button", { name: "Responder" }).click();
  await page.getByRole("button", { name: "Revelar comparação" }).click();
  await expect(page.getByRole("heading", { name: "Comparação" })).toBeVisible();
  await noHorizontalScroll(page);

  await page.locator("#reanalysis-text").fill("prefiro aguentar o burst do que bater mais");
  await page.getByRole("button", { name: "Interpretar texto" }).last().click();
  await page.getByRole("button", { name: "Usar este objetivo" }).click();
  await page.getByRole("button", { name: "Reanalisar" }).click();
  await expect(page.getByText(/Nova análise — objetivo Sobrevivência \(de texto confirmado/)).toBeVisible();
  await expect(page.getByRole("link", { name: /Baixar HTML/ })).toBeVisible();
  await noHorizontalScroll(page);
  await page.screenshot({ path: `${evidence}/comparacao-reanalise-360.png`, fullPage: true });
});

test("teclado: texto → confirmar → escolher → responder → revelar, com foco visível em cada passo", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto(decisionUrl);
  await expect(page.getByRole("heading", { name: "Estudar esta decisão" })).toBeVisible();

  await tabToId(page, "study-text");
  await page.keyboard.type("quero aguentar mais");
  await tabTo(page, "Interpretar texto");
  await expectFocusVisible(page);
  await page.keyboard.press("Enter");
  await tabTo(page, "Usar este objetivo");
  await expectFocusVisible(page);
  await page.keyboard.press("Enter");
  expect(await focusedLabel(page)).toBe("Objetivo");
  await expect(page.getByLabel(/Sobrevivência/)).toBeChecked();

  await tabTo(page, "Confirmar objetivo e começar");
  await page.keyboard.press("Enter");
  await expect(page.getByText(/Qual destas você compraria/)).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Space");
  await expect(page.getByLabel("Item Composto Sintético")).toBeChecked();
  await tabTo(page, "Responder");
  await expectFocusVisible(page);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Revelar comparação" })).toBeFocused();
  await expectFocusVisible(page);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Comparação" })).toBeFocused();
  await tabTo(page, /^Versões usadas$/);
  await expectFocusVisible(page);
});

for (const width of [1280, 360]) {
  test(`conceito C em ${width}px: Painel → Histórico → Contexto → Estudo → Comparação → Transferência, só formulário`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: width < 700 ? 780 : 900 });
    const api = await mockApi(page, {
      "GET /api/v1/matches": () => ok({ items: [matchSummary()], nextCursor: null }),
      "GET /api/v1/matches/:id/decisions": () =>
        ok({
          items: [decisionSummary(), decisionSummary({ decisionId: DECISION_2, timestampMs: 900_000, mainItemOrdinal: 2 })],
          nextCursor: null,
        }),
    });
    await page.goto("/painel");
    const nav = page.getByRole("navigation", { name: "Áreas principais" });
    await nav.getByRole("link", { name: /Histórico/ }).click();
    await expect(page.getByRole("heading", { name: "Partidas para estudar" })).toBeVisible();
    await page.getByRole("button", { name: "DEMO", exact: true }).click();
    await page.getByRole("link", { name: "Contexto da partida DEMO-0008" }).click();
    await expect(page.getByRole("heading", { name: /^Partida DEMO-0008/ })).toBeVisible();
    await page.getByRole("link", { name: /^Participante 1 —/ }).click();
    await page.getByRole("tab", { name: "Compras" }).click();
    await page.getByRole("link", { name: "Decisão em 7:52" }).click();
    await page.getByRole("button", { name: "Praticar decisão", exact: true }).click();

    // Estudo só pelo formulário: o interpretador de texto nunca é chamado
    await page.getByLabel(/Sobrevivência/).check();
    await page.getByRole("button", { name: "Confirmar objetivo e começar" }).click();
    await page.getByLabel("Item Composto Sintético").check();
    await page.getByRole("button", { name: "Responder" }).click();
    await page.getByRole("button", { name: "Revelar comparação" }).click();
    await expect(page.getByText(/Objetivo: Sobrevivência \(escolhido no formulário\)/)).toBeVisible();
    await noHorizontalScroll(page);
    await page.screenshot({ path: `${evidence}/jornada-comparacao-${width}.png`, fullPage: true });

    // Transferência numa decisão diferente: sem pistas
    await page.goBack();
    await page.goBack();
    await page.getByRole("link", { name: "Decisão em 15:00" }).click();
    await page.getByRole("button", { name: "Praticar decisão", exact: true }).click();
    await page.getByLabel(/Praticar sem pistas/).check();
    await page.getByRole("button", { name: "Confirmar objetivo e começar" }).click();
    await expect(page.getByText("Modo sem pistas (transferência).")).toBeVisible();
    await expect(page.getByRole("button", { name: /^Pista/ })).toHaveCount(0);
    await page.getByLabel("Outro Composto Sintético").check();
    await page.getByRole("button", { name: "Responder" }).click();
    await page.getByRole("button", { name: "Revelar comparação" }).click();
    await expect(page.getByRole("heading", { name: "Comparação" })).toBeVisible();
    await noHorizontalScroll(page);
    await page.screenshot({ path: `${evidence}/jornada-transferencia-${width}.png`, fullPage: true });

    expect(api.count("POST /api/v1/objectives/interpret")).toBe(0);
  });
}
