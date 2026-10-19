import { expect, test } from "@playwright/test";

async function openPrototype(page: import("@playwright/test").Page) {
  await page.goto("/importar");
  await page.getByRole("button", { name: "Abrir protótipo (dados ilustrativos)" }).click();
  await expect(page.locator("#entrada-heading")).toBeVisible();
}

async function openNormalDecision(page: import("@playwright/test").Page, decisionLabel: string) {
  await openPrototype(page);
  await page.getByRole("button", { name: "Abrir demonstração (sem ID)" }).click();
  await page.getByText("Importação concluída").waitFor();
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.locator("#decisoes-heading")).toBeVisible();
  await page.getByRole("button", { name: decisionLabel }).click();
  await expect(page.locator("#contexto-heading")).toBeVisible();
}

test.describe("Entrada", () => {
  test("exige ID preenchido e valida o formato antes de simular a abertura", async ({ page }) => {
    await openPrototype(page);

    await page.getByRole("button", { name: "Abrir (simulado)" }).click();
    await expect(page.getByText("Campo obrigatório")).toBeVisible();

    await page.locator("#match-id-input").fill("id-invalido");
    await page.getByRole("button", { name: "Abrir (simulado)" }).click();
    await expect(page.getByText("ID inválido (protótipo)")).toBeVisible();
  });

  test("recusa partida em andamento sem navegar para progresso", async ({ page }) => {
    await openPrototype(page);

    await page.getByRole("button", { name: "Simular partida em andamento" }).click();

    await expect(page.getByText("RiftLab não acompanha partidas em andamento")).toBeVisible();
    await expect(page.locator("#entrada-heading")).toBeVisible();
  });
});

test.describe("Progresso", () => {
  test("falha simulada permite tentar novamente ou voltar à entrada", async ({ page }) => {
    await openPrototype(page);
    await page.getByRole("button", { name: "Simular acesso indisponível" }).click();
    await expect(page.locator("#progresso-heading")).toBeVisible();

    await expect(page.getByText("UPSTREAM_UNAVAILABLE")).toBeVisible();
    await page.getByRole("button", { name: "Tentar novamente" }).click();
    await expect(page.getByText("UPSTREAM_UNAVAILABLE")).toBeVisible();

    await page.getByRole("button", { name: "Voltar à entrada" }).click();
    await expect(page.locator("#entrada-heading")).toBeVisible();
  });
});

test.describe("Decisões", () => {
  test("participante sem decisões mostra lista vazia com motivo", async ({ page }) => {
    await openPrototype(page);
    await page.getByRole("button", { name: "Abrir demonstração (sem ID)" }).click();
    await page.getByRole("button", { name: "Continuar" }).click();

    await page.locator('input[name="participante"][value="participante-b"]').check();

    await expect(page.getByText("Lista vazia")).toBeVisible();
  });

  test("cenário incompatível e inventário incerto mostram aviso e não avançam", async ({ page }) => {
    await openPrototype(page);
    await page.getByRole("button", { name: "Abrir demonstração (sem ID)" }).click();
    await page.getByRole("button", { name: "Continuar" }).click();

    await page.getByRole("button", { name: "Decisão de patch fora do recorte suportado" }).click();
    await expect(page.getByText("OUT_OF_SCOPE")).toBeVisible();
    await expect(page.locator("#decisoes-heading")).toBeVisible();

    await page.getByRole("button", { name: "Decisão com inventário incerto" }).click();
    await expect(page.getByText("INVENTORY_UNCERTAIN")).toBeVisible();
    await expect(page.locator("#decisoes-heading")).toBeVisible();
  });
});

test.describe("Escolha e pistas", () => {
  test("pistas não revelam custo/atributos antes de abertas explicitamente", async ({ page }) => {
    await openNormalDecision(page, "Meta de item após primeiro recuo");
    await page.locator('input[name="objetivo"][value="BALANCED"]').check();
    await page.getByRole("button", { name: "Continuar" }).click();
    await expect(page.locator("#escolha-heading")).toBeVisible();

    await expect(page.getByText("custo restante 1400")).not.toBeVisible();

    await page.getByText("Pista 2 — conferir custo/atributos").click();
    await expect(page.getByText("custo restante 1400")).toBeVisible();
  });

  test("exige alternativa ou não sei antes de registrar resposta", async ({ page }) => {
    await openNormalDecision(page, "Meta de item após primeiro recuo");
    await page.locator('input[name="objetivo"][value="BALANCED"]').check();
    await page.getByRole("button", { name: "Continuar" }).click();

    await expect(page.getByRole("button", { name: "Registrar resposta" })).toBeDisabled();
    await page.locator('input[name="alternativa"][value="nao-sei"]').check();
    await expect(page.getByRole("button", { name: "Registrar resposta" })).toBeEnabled();
  });
});

test.describe("Comparação", () => {
  test("reveal é monotônico: não há botão para ocultar a comparação novamente", async ({ page }) => {
    await openNormalDecision(page, "Meta de item após primeiro recuo");
    await page.locator('input[name="objetivo"][value="BALANCED"]').check();
    await page.getByRole("button", { name: "Continuar" }).click();
    await page.getByRole("button", { name: "Revelar sem responder" }).click();

    await expect(page.locator("#comparacao-heading")).toBeVisible();
    await expect(page.getByRole("button", { name: "Voltar" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Nova tentativa" })).toBeVisible();
  });

  test("cenário sem recomendação não apresenta nenhuma alternativa como vencedora", async ({ page }) => {
    await openNormalDecision(page, "Meta de item com quadro defasado");
    await page.locator('input[name="objetivo"][value="DAMAGE"]').check();
    await page.getByRole("button", { name: "Continuar" }).click();
    await page.getByRole("button", { name: "Revelar sem responder" }).click();

    await expect(page.getByText("Sem recomendação disponível")).toBeVisible();
    await expect(page.getByText(/gabarito universal/)).toBeVisible();
  });
});

test.describe("Transferência", () => {
  test("cenário de transferência não oferece pistas", async ({ page }) => {
    await openNormalDecision(page, "Meta de item após primeiro recuo");
    await page.locator('input[name="objetivo"][value="BALANCED"]').check();
    await page.getByRole("button", { name: "Continuar" }).click();
    await page.getByRole("button", { name: "Revelar sem responder" }).click();
    await page.getByRole("button", { name: "Continuar para cenário de transferência" }).click();

    await expect(page.locator("#transferencia-heading")).toBeVisible();
    await expect(page.getByText("Pista 1", { exact: false })).toHaveCount(0);
    await expect(page.locator("details")).toHaveCount(0);
  });
});

test.describe("Navegação por teclado", () => {
  test("percorre entrada → progresso → decisões → contexto → escolha → comparação só com teclado", async ({
    page,
  }) => {
    await page.goto("/importar");

    // Home -> abrir protótipo
    await page.getByRole("button", { name: "Abrir protótipo (dados ilustrativos)" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#entrada-heading")).toBeFocused();

    // Entrada -> abrir demonstração
    await page.getByRole("button", { name: "Abrir demonstração (sem ID)" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#progresso-heading")).toBeFocused();

    // Progresso -> aguardar habilitar e continuar
    const continueButton = page.getByRole("button", { name: "Continuar" });
    await expect(continueButton).toBeEnabled();
    await continueButton.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#decisoes-heading")).toBeFocused();

    // Decisões -> escolher decisão normal via teclado
    await page
      .getByRole("button", { name: "Meta de item após primeiro recuo" })
      .focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#contexto-heading")).toBeFocused();

    // Contexto -> selecionar objetivo com Espaço e continuar
    await page.locator('input[name="objetivo"][value="BALANCED"]').focus();
    await page.keyboard.press("Space");
    await page.getByRole("button", { name: "Continuar" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#escolha-heading")).toBeFocused();

    // Escolha -> selecionar alternativa e registrar resposta via teclado
    await page.locator('input[name="alternativa"][value="alt-dano"]').focus();
    await page.keyboard.press("Space");
    await page.getByRole("button", { name: "Registrar resposta" }).focus();
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Revelar comparação" }).focus();
    await page.keyboard.press("Enter");

    await expect(page.locator("#comparacao-heading")).toBeFocused();
    await expect(page.getByText("Alternativa A — sua escolha")).toBeVisible();
  });
});
