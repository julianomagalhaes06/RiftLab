import { expect, test } from "@playwright/test";
import { mockApi, ok, problem } from "./support/mock-api";

// Contas locais: leitura pública sem conta; conta para analisar e guardar. Desktop e celular.
const USER = { id: "11111111-1111-4111-8111-111111111111", username: "jogadora" };

for (const width of [1280, 360]) {
  test(`entrar, ver o menu do usuário e sair em ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    let signedIn = false;
    await mockApi(page, {
      "GET /api/v1/auth/session": () => ok({ authRequired: true, user: signedIn ? USER : null }),
      "POST /api/v1/auth/login": ({ body }) => {
        const { password } = body as { password: string };
        if (password !== "senha-correta-1") return problem(401, "INVALID_CREDENTIALS");
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
    await page.getByLabel("Usuário").fill("jogadora");
    await page.getByLabel("Senha", { exact: true }).fill("senha-errada-1");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("alert")).toHaveText("Usuário ou senha incorretos.");
    await page.getByLabel("Senha", { exact: true }).fill("senha-correta-1");
    await page.keyboard.press("Enter");

    await expect(page.getByRole("link", { name: "Minha conta (jogadora)" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Áreas principais" })).toBeVisible();
    await page.getByRole("button", { name: "Sair" }).click();
    // Sair não tranca o site: o Painel vira o convite e o topo volta a oferecer Entrar.
    await expect(page.getByText("Disponível com conta")).toBeVisible();
    await expect(page.locator(".user-menu").getByRole("link", { name: "Entrar" })).toBeVisible();
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
