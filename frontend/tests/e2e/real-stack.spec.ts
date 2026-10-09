import { expect, test } from "@playwright/test";

/**
 * Demonstração ponta a ponta contra a stack REAL (api + worker + PostgreSQL +
 * frontend, migrations aplicadas): nada simulado. Só roda no projeto `real-stack`
 * (`npm run test:e2e:stack`) com E2E_BASE_URL apontando para o frontend da stack
 * (ex.: http://127.0.0.1:5173). Sem E2E_BASE_URL falha em vez de passar em branco.
 */
test.describe("Demonstração ponta a ponta na stack real", () => {
  test.beforeAll(() => {
    if (!process.env.E2E_BASE_URL) {
      throw new Error("real-stack exige E2E_BASE_URL (frontend da stack compose com migrations).");
    }
  });

  test("explorar demonstração → preparação → participante → decisões → detalhe → deep links → voltar", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const pageErrors: string[] = [];
    const consoleErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") {
        consoleErrors.push(message.text());
      }
    });

    // Entrada
    await page.goto("/");
    await expect(page.getByText("Serviço disponível")).toBeVisible();
    await expect(
      page.getByText("Analise partidas concluídas. O RiftLab não acompanha jogos em andamento."),
    ).toBeVisible();

    // Importação e preparação automática (o worker faz tudo sozinho)
    await page.getByRole("button", { name: "Explorar demonstração" }).click();
    await expect(page.getByRole("heading", { name: "Partida DEMO-0008" })).toBeVisible({ timeout: 90_000 });
    await expect(page.getByRole("status")).toContainText("Partida pronta", { timeout: 90_000 });
    await expect(page).toHaveURL(/\/matches\/DEMO-0008$/);
    const matchUrl = "/matches/DEMO-0008";

    // Participante → decisões
    await page.getByRole("link", { name: /^Participante 1 —/ }).click();
    await expect(page).toHaveURL(/\/matches\/DEMO-0008\?participant=1$/);
    await expect(page.getByRole("heading", { name: "Decisões do participante 1" })).toBeVisible();
    const decisionLink = page.getByRole("link", { name: /^Decisão em / }).first();
    await expect(decisionLink).toBeVisible();
    await expect(page.getByText("Elegível", { exact: true }).first()).toBeVisible();

    // Detalhe: inventário com nomes do catálogo fixado, orçamento e elegibilidade
    await decisionLink.click();
    await expect(page).toHaveURL(/\/decisions\/[0-9a-f-]{36}$/);
    await expect(page.getByRole("heading", { name: /^Decisão em .* — participante 1$/ })).toBeVisible();
    await expect(page.getByText("Componente Sintético A")).toBeVisible();
    await expect(page.getByText("Componente Sintético B")).toBeVisible();
    await expect(
      page.getByText(/^(Aproximadamente .* de ouro|Snapshot antigo; orçamento não confirmável|Orçamento desconhecido)$/),
    ).toBeVisible();
    await expect(page.getByText("Elegível para análise")).toBeVisible();
    await expect(page.locator("body")).not.toContainText(/item comprado|nome indisponível/);

    // Estudo (CC-304/305): escolher antes de revelar; sem custo nem histórico antes.
    await page.getByRole("button", { name: "Confirmar objetivo e começar" }).click();
    await expect(page.getByLabel("Item Composto Sintético")).toBeVisible();
    await expect(page.locator("body")).not.toContainText(/Faltam \d+ de ouro|histórico/);
    await page.getByLabel("Item Composto Sintético").check();
    await page.getByRole("button", { name: "Responder" }).click();
    await page.getByRole("button", { name: "Revelar comparação" }).click();
    await expect(page.getByRole("heading", { name: "Comparação" })).toBeVisible();
    await expect(page.getByText("Faltam 150 de ouro para completar o item.")).toBeVisible();
    await expect(page.getByText(/compra feita na partida \(histórico\)/)).toBeVisible();
    await expect(page.getByText(/sua escolha/)).toBeVisible();
    const detailUrl = page.url();

    // Retomada por URL: recarregar o detalhe e abrir o deep link da partida
    await page.reload();
    await expect(page.getByText("Componente Sintético A")).toBeVisible();
    expect(page.url()).toBe(detailUrl);
    await page.goto(`${matchUrl}?participant=1`);
    await expect(page.getByRole("heading", { name: "Decisões do participante 1" })).toBeVisible();
    await expect(page.getByRole("link", { name: /^Decisão em / }).first()).toBeVisible();

    // Voltar / avançar do navegador
    await page.goBack(); // do deep link para o detalhe recarregado
    await expect(page.getByText("Componente Sintético A")).toBeVisible();
    await page.goForward();
    await expect(page.getByRole("heading", { name: "Decisões do participante 1" })).toBeVisible();

    // Outro participante da partida (o 6, do time 200): 200 com lista vazia, que é
    // diferente de "não preparado" (409)
    await page.getByRole("link", { name: /^Participante 6 —/ }).click();
    await expect(page).toHaveURL(/\?participant=6$/);
    await expect(
      page.getByText("Nenhuma decisão de compra foi encontrada para este participante nesta partida."),
    ).toBeVisible();

    // Retomar pela lista da entrada
    await page.goto("/");
    const row = page.getByRole("link", { name: /DEMO-0008/ });
    await expect(row).toBeVisible();
    await expect(page.getByText("Pronta", { exact: true }).first()).toBeVisible();
    await row.click();
    await expect(page.getByRole("heading", { name: "Partida DEMO-0008" })).toBeVisible();

    expect(pageErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
  });
});
