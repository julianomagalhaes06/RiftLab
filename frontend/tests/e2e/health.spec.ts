import { expect, test } from "@playwright/test";

test("mostra disponibilidade quando live e ready respondem 200", async ({ page }) => {
  await page.route("**/health/live", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"UP"}' }),
  );
  await page.route("**/health/ready", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: '{"status":"UP","database":"UP"}',
    }),
  );

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "RiftLab" })).toBeVisible();
  await expect(page.getByText("Serviço disponível")).toBeVisible();
});

test("mostra indisponibilidade quando a API nao responde", async ({ page }) => {
  await page.route("**/health/live", (route) => route.abort("failed"));

  await page.goto("/");

  await expect(page.getByText("Serviço indisponível")).toBeVisible();
});

test("mostra suporte limitado quando o banco esta indisponivel", async ({ page }) => {
  await page.route("**/health/live", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"UP"}' }),
  );
  await page.route("**/health/ready", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/problem+json",
      body: JSON.stringify({
        type: "https://riftlab.local/problems/database_unavailable",
        title: "Banco de dados indisponivel",
        status: 503,
        code: "DATABASE_UNAVAILABLE",
        detail: "banco indisponivel de teste e2e",
        requestId: "e2e-request-id",
      }),
    }),
  );

  await page.goto("/");

  await expect(page.getByText("Serviço com suporte limitado")).toBeVisible();
  await expect(page.getByText("banco indisponivel de teste e2e")).toBeVisible();
});
