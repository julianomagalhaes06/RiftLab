import { expect, test } from "@playwright/test";
import {
  DECISION_1,
  JOB_ID,
  job,
  mockApi,
  ok,
  preparation,
  matchDetail,
  problem,
  sequence,
} from "./support/mock-api";

const SERVER_DETAIL = "detalhe-do-servidor-que-nao-pode-aparecer";

test.describe("Importação com falha recuperável (API simulada)", () => {
  test("chave da Riot inválida ou expirada: texto útil sem segredo, tentar novamente e sucesso", async ({
    page,
  }) => {
    await mockApi(page, {
      "POST /api/v1/imports": () =>
        ok({ jobId: JOB_ID, status: "QUEUED", statusUrl: `/api/v1/imports/${JOB_ID}` }, 202),
      "GET /api/v1/imports/:id": (request) =>
        request.url.pathname.endsWith(JOB_ID)
          ? ok(job({ status: "FAILED", attempts: 1, errorCode: "RIOT_AUTH_FAILED" }))
          : ok(job({ status: "SUCCEEDED", attempts: 1, matchId: "BR1_3120345678" })),
      "GET /api/v1/matches/:id": () => ok(matchDetail({ matchId: "BR1_3120345678" })),
    });

    await page.goto("/");
    await page.getByLabel(/ID da partida/).fill("BR1_3120345678");
    await page.getByRole("button", { name: "Analisar partida" }).click();

    await expect(page).toHaveURL(new RegExp(`/imports/${JOB_ID}$`));
    await expect(
      page.getByText(
        "A chave da Riot é inválida ou expirou; renove a chave Development no ambiente local.",
      ),
    ).toBeVisible();
    const body = page.locator("body");
    await expect(body).not.toContainText("RGAPI");
    await expect(body).not.toContainText(SERVER_DETAIL);

    await page.getByRole("button", { name: "Tentar importar novamente" }).click();

    await expect(page.getByRole("heading", { name: "Partida BR1_3120345678" })).toBeVisible();
    await expect(page).toHaveURL(/\/matches\/BR1_3120345678$/);
  });

  test("chave da Riot ausente no servidor: mensagem útil e a demonstração continua disponível", async ({
    page,
  }) => {
    let calls = 0;
    await mockApi(page, {
      "POST /api/v1/imports": (request) => {
        calls += 1;
        return (request.body as { mode: string }).mode === "RIOT"
          ? problem(422, "RIOT_KEY_MISSING")
          : ok({ jobId: JOB_ID, status: "QUEUED", statusUrl: `/api/v1/imports/${JOB_ID}` }, 202);
      },
    });

    await page.goto("/");
    await page.getByLabel(/ID da partida/).fill("BR1_3120345678");
    await page.getByRole("button", { name: "Analisar partida" }).click();

    await expect(page.getByRole("alert")).toContainText("este ambiente não tem chave da Riot configurada");
    await expect(page.locator("body")).not.toContainText(SERVER_DETAIL);
    await expect(page).toHaveURL(/\/$/); // continua na entrada, sem job criado

    // recuperação: explorar a demonstração, que não precisa de chave
    await page.getByRole("button", { name: "Explorar demonstração" }).click();
    await expect(page.getByRole("heading", { name: "Partida DEMO-0008" })).toBeVisible();
    expect(calls).toBe(2);
  });

  test("Riot indisponível: RETRY_WAIT informa a nova tentativa e termina em sucesso sozinho", async ({
    page,
  }) => {
    const retryAt = new Date(Date.now() + 2_000).toISOString();
    await mockApi(page, {
      "GET /api/v1/imports/:id": sequence(
        () => ok(job({ status: "RETRY_WAIT", attempts: 1, errorCode: "UPSTREAM_UNAVAILABLE", retryAt })),
        () => ok(job({ status: "SUCCEEDED", attempts: 2, matchId: "DEMO-0008" })),
      ),
    });

    await page.goto(`/imports/${JOB_ID}`);

    await expect(page.getByRole("status")).toContainText("Nova tentativa agendada");
    await expect(page.getByText(/A Riot está indisponível/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Partida DEMO-0008" })).toBeVisible({
      timeout: 15_000,
    });
  });

  test("falha de preparação: motivo, 'Tentar preparar novamente' e partida pronta", async ({ page }) => {
    await mockApi(page, {
      "GET /api/v1/matches/:id": sequence(
        () => ok(matchDetail({ preparation: preparation({ status: "FAILED", errorCode: "RAW_MATCH_NOT_CACHED" }) })),
        () => ok(matchDetail({ preparation: preparation({ status: "PENDING", attempts: 0 }) })),
        () => ok(matchDetail()),
      ),
    });

    await page.goto("/matches/DEMO-0008");
    await expect(page.getByText(/Os dados brutos dessa partida não estão no armazenamento local/)).toBeVisible();

    await page.getByRole("button", { name: "Tentar preparar novamente" }).click();

    await expect(page.getByRole("status")).toContainText("Partida pronta", { timeout: 15_000 });
    await expect(page.getByRole("link", { name: /^Participante 1 —/ })).toBeVisible();
  });

  test("decisão inexistente e API fora do ar têm texto útil e caminho de volta", async ({ page }) => {
    await mockApi(page, {
      "GET /api/v1/decisions/:id": () => problem(404, "RESOURCE_NOT_FOUND"),
    });

    await page.goto(`/decisions/${DECISION_1}`);

    await expect(page.getByText(/Não encontramos o que você procurou/)).toBeVisible();
    await expect(page.locator("body")).not.toContainText(/andamento/);
    await page.getByRole("link", { name: "Voltar ao início" }).click();
    await expect(page.getByRole("heading", { name: "Analisar uma partida" })).toBeVisible();
  });

  test("API fora do ar ao importar: mensagem de rede e botão continua utilizável", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/v1/imports", (route) => route.abort("failed"));

    await page.goto("/");
    await page.getByRole("button", { name: "Explorar demonstração" }).click();

    await expect(page.getByText(/Não foi possível falar com a API do RiftLab/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Explorar demonstração" })).toBeEnabled();

  });
});
