import { expect, test } from "@playwright/test";
import { mockApi, ok, problem } from "./support/mock-api";

// Contas locais: leitura pública sem conta; conta para analisar e guardar. Desktop e celular.
const USER = { id: "11111111-1111-4111-8111-111111111111", email: "jogadora@example.test" };

for (const width of [1280, 360]) {
  test(`entrar, ver o menu do usuário e sair em ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    let signedIn = false;
    await mockApi(page, {
      "GET /api/v1/auth/session": () => ok({ authRequired: true, user: signedIn ? USER : null }),
      "POST /api/v1/auth/login": ({ body }) => {
        const { password } = body as { password: string };
        if (password !== "Senha-Correta-1") return problem(401, "INVALID_CREDENTIALS");
        signedIn = true;
        return ok({ authRequired: true, user: USER });
      },
      "POST /api/v1/auth/logout": () => {
        signedIn = false;
        return { status: 204, body: "" };
      },
    });
    await page.goto("/entrar");
    await expect(page.getByRole("heading", { name: "Entrar no RiftLab" })).toBeVisible();
    const scroll = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(scroll).toBeLessThanOrEqual(0);

    // Só teclado: usuário, senha e Enter.
    await page.getByLabel("E-mail").fill("jogadora@example.test");
    await page.getByLabel("Senha", { exact: true }).fill("Senha-Errada-1");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("alert")).toHaveText("E-mail ou senha incorretos.");
    await page.getByLabel("Senha", { exact: true }).fill("Senha-Correta-1");
    await page.keyboard.press("Enter");

    await expect(page.getByRole("link", { name: "Minha conta (jogadora@example.test)" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Áreas principais" })).toBeVisible();
    await page.getByRole("button", { name: "Sair" }).click();
    // Sair leva ao início, e o topo volta a oferecer Entrar.
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: /Entenda cada compra/ })).toBeVisible();
    await expect(page.locator(".user-menu").getByRole("link", { name: "Entrar" })).toBeVisible();
  });
}

for (const width of [1280, 360]) {
  test(`criar conta, confirmar pelo código e entrar em ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    let signedIn = false;
    await mockApi(page, {
      "GET /api/v1/auth/session": () => ok({ authRequired: true, user: signedIn ? USER : null }),
      "POST /api/v1/auth/register": () => ok({ status: "CODE_SENT" }, 202),
      "POST /api/v1/auth/verify-email": ({ body }) => {
        if ((body as { code: string }).code !== "123456") return problem(422, "INVALID_CODE");
        signedIn = true;
        return ok({ authRequired: true, user: USER });
      },
    });
    await page.goto("/criar-conta");
    await page.getByLabel("Nome", { exact: true }).fill("Jogadora");
    await page.getByLabel("E-mail").fill("jogadora@example.test");
    await page.getByLabel("Senha", { exact: true }).fill("Abcdef1!");
    await page.getByLabel("Confirmar senha", { exact: true }).fill("Abcdef1!");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Confirme seu e-mail" })).toBeFocused();
    const scroll = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(scroll).toBeLessThanOrEqual(0);
    await page.getByLabel("Código de 6 dígitos").fill("000000");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("alert")).toContainText("Código inválido ou vencido");
    await page.getByLabel("Código de 6 dígitos").fill("123456");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Conta criada" })).toBeVisible();
  });

  test(`esqueci minha senha em ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page, {
      "GET /api/v1/auth/session": () => ok({ authRequired: true, user: null }),
    });
    await page.goto("/entrar");
    await page.getByRole("link", { name: "Esqueci minha senha" }).click();
    await page.getByLabel("E-mail").fill("jogadora@example.test");
    await page.getByRole("button", { name: "Enviar código" }).click();
    await page.getByLabel("Código de 6 dígitos").fill("123456");
    await page.getByLabel("Senha nova", { exact: true }).fill("Senha-Nova-2");
    const scroll = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(scroll).toBeLessThanOrEqual(0);
    await page.getByRole("button", { name: "Salvar senha nova" }).click();
    await expect(page.getByText("Senha alterada. Entre com a senha nova.")).toBeVisible();
    await expect(page).toHaveURL(/\/entrar$/);
  });
}

test("sem conta em 360px: início com a busca e convite nas áreas da conta", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await mockApi(page, {
    "GET /api/v1/auth/session": () => ok({ authRequired: true, user: null }),
  });
  // Sem rede externa no teste: a splash art cai no fundo neutro, sem quebrar a vitrine.
  await page.route("https://ddragon.leagueoflegends.com/**", (route) => route.abort("failed"));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Entenda cada compra/ })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Buscar jogador pelo Riot ID" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Servidor" })).toHaveValue("BR");
  // A vitrine corta o que passa da largura (overflow hidden): o conteúdo tem de caber nela.
  for (const selector of [".home-hero__inner", ".player-search--hero"]) {
    const box = await page.locator(selector).boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(360);
  }
  const nav = page.getByRole("navigation", { name: "Áreas principais" });
  await expect(nav).toContainText("Início");
  await expect(nav).not.toContainText("Painel");
  let scroll = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(scroll).toBeLessThanOrEqual(0);

  await page.goto("/minha-posicao");
  await expect(page.getByText("Disponível com conta")).toBeVisible();
  await expect(page.locator(".account-gate").getByRole("link", { name: "Criar conta" })).toHaveAttribute("href", "/criar-conta?next=%2Fminha-posicao");
  scroll = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(scroll).toBeLessThanOrEqual(0);
});
