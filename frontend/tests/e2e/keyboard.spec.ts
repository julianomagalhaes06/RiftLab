import { expect, test } from "@playwright/test";
import { activeId, expectFocusVisible, focusedLabel, tabTo } from "./support/focus";
import { mockApi } from "./support/mock-api";

/**
 * Fluxo principal só com teclado (Tab/Shift+Tab/Enter/Espaço): skip link, foco
 * visível, entrada → demonstração → partida → participante → decisões (carregar mais)
 * → detalhe → volta. O único texto digitado é o do campo de ID (não há outro jeito de
 * preencher um campo de texto).
 */
test("skip link é o primeiro foco, aparece na tela e leva ao conteúdo", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Analisar uma partida" })).toBeVisible();

  await page.keyboard.press("Tab");

  expect(await focusedLabel(page)).toBe("Ir para o conteúdo");
  await expectFocusVisible(page);
  await page.keyboard.press("Enter");
  expect(await activeId(page)).toBe("conteudo");
  await expect(page).toHaveURL(/#conteudo$/);
});

test("fluxo principal completo só com teclado", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Analisar uma partida" })).toBeVisible();

  // Entrada: Espaço ativa o botão; foco visível nele.
  await tabTo(page, "Explorar demonstração");
  await expectFocusVisible(page);
  await page.keyboard.press("Space");

  // Importação → partida (redirecionamento automático); o foco vai para o conteúdo novo.
  await expect(page.getByRole("heading", { name: /^Partida DEMO-0008/ })).toBeVisible();
  await expect.poll(() => activeId(page)).toBe("conteudo");
  await expect(page.getByRole("status")).toContainText("Partida pronta");

  // Participante: Enter no link; trocar de participante NÃO tira o foco do link.
  const participantLabel = await tabTo(page, /^Participante 1 — Campeão #9000/);
  await expectFocusVisible(page);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\?participant=1$/);
  await expect(page.getByRole("heading", { name: /^Decisões de Campeão #9000/ })).toBeVisible();
  expect(await focusedLabel(page)).toBe(participantLabel);

  // Decisões: "Carregar mais" por Espaço.
  await tabTo(page, "Carregar mais decisões");
  await expectFocusVisible(page);
  await page.keyboard.press("Space");
  await expect(page.getByRole("link", { name: "Decisão em 15:00" })).toBeVisible();

  // Detalhe: Enter no link da decisão.
  await page.keyboard.press("Shift+Tab"); // volta para a lista (último link da decisão nova)
  await tabTo(page, "Decisão em 7:52", 60).catch(async () => {
    // a ordem de Tab depende de onde o foco ficou; recomeça do topo da lista
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await tabTo(page, "Decisão em 7:52", 120);
  });
  await expectFocusVisible(page);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: /em 7:52/ })).toBeVisible();
  await expect.poll(() => activeId(page)).toBe("conteudo");
  await expect(page.getByText("Componente Sintético A")).toBeVisible();

  // Volta às decisões do mesmo participante, ainda só com teclado.
  await tabTo(page, /^Voltar às decisões da partida DEMO-0008/);
  await expectFocusVisible(page);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: /^Decisões de Campeão #9000/ })).toBeVisible();
  await expect(page).toHaveURL(/\/matches\/DEMO-0008\?participant=1$/);
});

test("formulário por teclado: erro de validação anunciado e ligado ao campo", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Analisar uma partida" })).toBeVisible();

  await tabTo(page, /^$/); // o primeiro foco depois do skip link/logo é o campo (label vazio de input)
  const isInput = await page.evaluate(() => document.activeElement?.tagName === "INPUT");
  expect(isInput).toBe(true);
  await expectFocusVisible(page);
  await page.keyboard.type("DEMO-0001");
  await page.keyboard.press("Enter"); // Enter no campo envia o formulário

  const alert = page.getByRole("alert");
  await expect(alert).toContainText("formato BR1_ seguido de números");
  const input = page.getByLabel(/ID da partida/);
  await expect(input).toHaveAttribute("aria-invalid", "true");
  const describedBy = await input.getAttribute("aria-describedby");
  expect(describedBy).toBeTruthy();
  await expect(page.locator(`#${describedBy}`)).toContainText("formato BR1_");
});
