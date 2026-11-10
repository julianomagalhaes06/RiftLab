import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";

/**
 * Demonstração ponta a ponta contra a stack REAL (api + worker + PostgreSQL +
 * frontend, migrations aplicadas): nada simulado. Só roda no projeto `real-stack`
 * (`npm run test:e2e:stack`) com E2E_BASE_URL apontando para o frontend da stack
 * (ex.: http://127.0.0.1:5173). Sem E2E_BASE_URL falha em vez de passar em branco.
 */
/** Código enviado ao e-mail, lido na API do Mailpit do compose. */
async function codeFromMailpit(page: Page, email: string): Promise<string> {
  const base = process.env.E2E_MAILPIT_URL ?? "http://127.0.0.1:8025";
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const found = await (await page.request.get(`${base}/api/v1/search`, { params: { query: `to:"${email}"`, limit: 1 } })).json();
    if (found.messages?.length) {
      const message = await (await page.request.get(`${base}/api/v1/message/${found.messages[0].ID}`)).json();
      const code = /\b[0-9]{6}\b/.exec(message.Text as string);
      if (code) return code[0];
    }
    await page.waitForTimeout(500);
  }
  throw new Error("nenhum código chegou ao Mailpit");
}

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
    // CC-601: a demonstração não depende de nada fora da máquina (sem Riot, CDN ou nuvem)
    const baseHost = new URL(process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173").hostname;
    const externalRequests: string[] = [];
    page.on("request", (request) => {
      const host = new URL(request.url()).hostname;
      if (!["127.0.0.1", "localhost", baseHost].includes(host) && !request.url().startsWith("data:")) {
        externalRequests.push(host);
      }
    });

    // Login exigido por padrão: importar é da conta, então a página mostra o convite. Cria a
    // conta por ele e volta para /importar (o roteiro termina removendo a partida, que também
    // exige conta).
    await page.goto("/importar");
    const gate = page.getByRole("tab", { name: "Criar conta" });
    const ready = page.getByText("Serviço disponível");
    await expect(gate.or(ready)).toBeVisible();
    if (await gate.isVisible()) {
      await gate.click();
      const email = `e2e_${Date.now()}@example.test`;
      await page.getByLabel("Nome", { exact: true }).fill("Jogadora E2E");
      await page.getByLabel("E-mail").fill(email);
      await page.getByLabel("Senha", { exact: true }).fill("Senha-E2e-Stack-1");
      await page.getByLabel("Confirmar senha", { exact: true }).fill("Senha-E2e-Stack-1");
      await page.getByRole("button", { name: "Criar conta" }).click();
      await page.getByLabel("Código de 6 dígitos").fill(await codeFromMailpit(page, email));
      await page.getByRole("button", { name: "Confirmar e-mail" }).click();
      await page.getByRole("button", { name: "Voltar para onde eu estava" }).click();
    }

    // Entrada
    await expect(page.getByText("Serviço disponível")).toBeVisible();
    await expect(
      page.getByText("Analise partidas concluídas. O RiftLab não acompanha jogos em andamento."),
    ).toBeVisible();

    // Importação e preparação automática (o worker faz tudo sozinho)
    await page.getByRole("button", { name: "Explorar demonstração" }).click();
    await expect(page.getByRole("heading", { name: /^Partida DEMO-0008/ })).toBeVisible({ timeout: 90_000 });
    await expect(page.getByRole("status")).toContainText("Partida pronta", { timeout: 90_000 });
    await expect(page).toHaveURL(/\/matches\/DEMO-0008$/);
    const matchUrl = "/matches/DEMO-0008";

    // Participante → decisões
    await page.getByRole("link", { name: /^Participante 1 —/ }).click();
    await expect(page).toHaveURL(/\/matches\/DEMO-0008\?participant=1$/);
    await expect(page.getByRole("heading", { name: /^(Suas decisões — |Decisões de )/ })).toBeVisible();
    const decisionLink = page.getByRole("link", { name: /^Decisão em / }).first();
    await expect(decisionLink).toBeVisible();
    await expect(page.getByText("Analisar", { exact: true }).first()).toBeVisible();

    // Detalhe: inventário com nomes do catálogo fixado, orçamento e elegibilidade
    await decisionLink.click();
    await expect(page).toHaveURL(/\/decisions\/[0-9a-f-]{36}$/);
    await expect(page.getByRole("heading", { name: /^(Sua decisão|Decisão de .+) em / })).toBeVisible();
    await expect(page.getByText("Componente Sintético A")).toBeVisible();
    await expect(page.getByText("Componente Sintético B")).toBeVisible();
    await expect(
      page.getByText(/^(Aproximadamente .* de ouro|Snapshot antigo; orçamento não confirmável|Orçamento desconhecido)$/),
    ).toBeVisible();
    await expect(page.getByText("Elegível para análise")).toBeVisible();
    await page.getByRole("button", { name: "Praticar decisão", exact: true }).click();
    await expect(page.locator("body")).not.toContainText(/item comprado|nome indisponível/);

    // Objetivo por texto (CC-504): interpretado pelo provedor de regras e só vale confirmado.
    await page.getByLabel(/descreva em uma frase/).fill("não quero dano, quero aguentar mais");
    await page.getByRole("button", { name: "Interpretar texto" }).click();
    await expect(page.getByText(/Entendi:/)).toContainText("Sobrevivência");
    await expect(page.getByText(/regras fixas, sem modelo de linguagem/)).toBeVisible();
    await page.getByRole("button", { name: "Usar este objetivo" }).click();
    await expect(page.getByLabel(/Sobrevivência/)).toBeChecked();
    await expect(page.getByText(/de texto confirmado \(interpretador rules_v1\)/)).toBeVisible();

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
    await expect(page.getByText(/— sua escolha/)).toBeVisible();
    await expect(page.getByText(/Objetivo: Sobrevivência \(de texto confirmado/)).toBeVisible();

    // Reanalisar (CC-504): texto ambíguo cai no formulário sem mudar nada; o formulário reanalisa
    await page.getByLabel(/descreva em uma frase/).fill("quero dano e vida");
    await page.getByRole("button", { name: "Interpretar texto" }).click();
    await expect(page.getByText(/o objetivo selecionado não mudou/)).toBeVisible();
    await page.getByLabel(/Dano/).check();
    await page.getByRole("button", { name: "Reanalisar" }).click();
    await expect(page.getByText(/Nova análise — objetivo Dano \(escolhido no formulário\)/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Comparação" })).toBeVisible();

    // Reflexão, julgamento e relatório (CC-505): o HTML baixado abre sem rede e escapa texto
    const hostile = '<img src=x onerror="alert(1)">';
    await page.getByLabel(/O que mudaria na sua escolha/).fill(`Com mais magos ${hostile}`);
    await page.getByRole("button", { name: "Registrar reflexão" }).click();
    await expect(page.getByText(/Sua reflexão registrada/)).toBeVisible();
    await page.getByText("Avaliação de revisor (opcional)").click();
    await page.getByLabel("Nota").selectOption("abstain");
    await page.getByLabel("Motivo").fill("sem rubrica suficiente para este caso");
    await page.getByLabel("Pseudônimo do revisor").fill("revisor-e2e");
    await page.getByRole("button", { name: "Registrar julgamento" }).click();
    await expect(page.getByText(/abstenção \(revisor [0-9a-f]{12}\)/)).toBeVisible();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("link", { name: /Baixar HTML/ }).click(),
    ]);
    const reportHtml = await readFile(await download.path(), "utf-8");
    expect(reportHtml).toContain("Resposta inicial");
    expect(reportHtml).toContain("Item Composto Sintético");
    expect(reportHtml).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
    expect(reportHtml).not.toContain("<script");
    expect(reportHtml).not.toContain("DEMO-0008");
    expect(reportHtml).not.toContain("revisor-e2e");
    const reportPage = await page.context().newPage();
    await reportPage.setContent(reportHtml);
    await expect(reportPage.getByRole("heading", { name: "Relatório de análise de compra" })).toBeVisible();
    await reportPage.close();
    const detailUrl = page.url();

    // Retomada por URL: recarregar o detalhe e abrir o deep link da partida
    await page.reload();
    await expect(page.getByText("Componente Sintético A")).toBeVisible();
    expect(page.url()).toBe(detailUrl);
    await page.goto(`${matchUrl}?participant=1`);
    await expect(page.getByRole("heading", { name: /^(Suas decisões — |Decisões de )/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /^Decisão em / }).first()).toBeVisible();

    // Voltar / avançar do navegador
    await page.goBack(); // do deep link para o detalhe recarregado
    await expect(page.getByText("Componente Sintético A")).toBeVisible();
    await page.goForward();
    await expect(page.getByRole("heading", { name: /^(Suas decisões — |Decisões de )/ })).toBeVisible();

    // Outro participante da partida (o 6, do time 200): 200 com lista vazia, que é
    // diferente de "não preparado" (409)
    await page.getByRole("link", { name: /^Participante 6 —/ }).click();
    await expect(page).toHaveURL(/\?participant=6$/);
    await expect(
      page.getByText("Nenhuma decisão de compra foi encontrada para este participante nesta partida."),
    ).toBeVisible();

    // Retomar pelo Histórico (a entrada não lista mais as partidas, CC-903)
    await page.goto("/historico");
    await page.getByRole("button", { name: "DEMO", exact: true }).click();
    const row = page.getByRole("link", { name: "Contexto da partida DEMO-0008" }).first();
    await expect(row).toBeVisible();
    await row.click();
    await expect(page.getByRole("heading", { name: /^Partida DEMO-0008/ })).toBeVisible();

    // Remoção (CC-603): prévia, confirmação digitada e job PURGE processado pelo worker real
    await page.getByText("Remover esta partida do RiftLab").click();
    await page.getByRole("button", { name: "Ver o que será removido" }).click();
    await expect(page.getByText(/eventos da linha do tempo/)).toBeVisible();
    await page.getByLabel(/Digite o id da partida/).fill("DEMO-0008");
    await page.getByRole("button", { name: "Remover definitivamente" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Partida removida" })).toBeVisible({ timeout: 60_000 });
    const gone = await page.request.get("/api/v1/matches/DEMO-0008");
    expect(gone.status()).toBe(404);

    expect(pageErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
    expect(externalRequests).toEqual([]);
  });
});
