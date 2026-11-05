import { expect, test } from "@playwright/test";
import { mockApi, ok, problem } from "./support/mock-api";

// Sprint 2 (#10/#14): login exigido pelo servidor, em desktop e celular.
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
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Entrar no RiftLab" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Áreas principais" })).toHaveCount(0);
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
    await expect(page.getByRole("heading", { name: "Entrar no RiftLab" })).toBeVisible();
  });
}

test("entrar como visitante pelo teclado em 360px", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  let guest = false;
  await mockApi(page, {
    "GET /api/v1/auth/session": () => ok({ authRequired: true, user: guest ? { ...USER, username: "visitante-1a2b3c4d", guest: true } : null }),
    "POST /api/v1/auth/guest": () => {
      guest = true;
      return ok({ authRequired: true, user: { ...USER, username: "visitante-1a2b3c4d", guest: true } }, 201);
    },
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Entrar como visitante" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("link", { name: "Minha conta (visitante)" })).toBeVisible();
  const scroll = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(scroll).toBeLessThanOrEqual(0);
});
