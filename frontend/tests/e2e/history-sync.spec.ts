import { expect, test, type Page } from "@playwright/test";
import { historySync, matchSummary, mockApi, ok, sequence } from "./support/mock-api";

const evidence = "../.private/validation/CC-901";
const stages = "../.private/validation/demo-real/etapas";
const later = () => new Date(Date.now() + 10 * 60_000).toISOString();

async function noHorizontalScroll(page: Page) {
  const widths = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
}

for (const width of [360, 1280]) {
  test.describe(`CC-901 em ${width} px`, () => {
    test.beforeEach(async ({ page }) => {
      await page.route("https://**/*", (route) => route.abort());
      await page.setViewportSize({ width, height: 900 });
    });

    test("abrir o Histórico atualiza com a Riot e a partida nova aparece sem recarregar a página", async ({ page }) => {
      const calls = await mockApi(page, {
        "POST /api/v1/history/sync": () => ok(historySync({ state: "QUEUED", requestedAt: new Date().toISOString() }), 202),
        "GET /api/v1/history/sync": sequence(
          () => ok(historySync({ state: "RUNNING" })),
          () =>
            ok(
              historySync({
                state: "SUCCEEDED",
                listed: 3,
                newEnqueued: 1,
                alreadyKnown: 2,
                finishedAt: new Date().toISOString(),
                nextAllowedAt: later(),
              }),
            ),
        ),
        "GET /api/v1/matches": sequence(
          () => ok({ items: [matchSummary()], nextCursor: null }),
          () => ok({ items: [matchSummary({ matchId: "BR1_9100000001", patch: "16.20" }), matchSummary()], nextCursor: null }),
        ),
      });
      await page.goto("/historico");
      const sync = page.getByRole("region", { name: "Atualização do histórico com a Riot" });
      await expect(sync.getByRole("status")).toHaveText("Atualizando o histórico com a Riot…");
      await expect(sync.getByRole("button", { name: "Atualizando…" })).toBeDisabled();
      await expect(sync.getByRole("status")).toHaveText(/1 partida nova encontrada/, { timeout: 8000 });
      await expect(page.getByRole("link", { name: "Contexto da partida BR1_9100000001" })).toBeVisible({ timeout: 10_000 });
      await expect(page.getByText("Carregando partidas…")).toHaveCount(0);
      expect(calls.count("POST /api/v1/history/sync")).toBe(1);
      await expect(sync.getByRole("button", { name: "Atualizar" })).toBeDisabled();
      await noHorizontalScroll(page);
      await page.screenshot({ path: `${evidence}/novas-${width}.png`, fullPage: true });
    });

    test("as etapas mostram cada consulta à Riot e o download das partidas novas", async ({ page }) => {
      const finished = new Date().toISOString();
      const done = {
        state: "SUCCEEDED",
        listed: 20,
        newEnqueued: 8,
        alreadyKnown: 12,
        finishedAt: finished,
        nextAllowedAt: later(),
        steps: { accountMs: 182, listMs: 240, leagueMs: 95 },
      };
      await mockApi(page, {
        "POST /api/v1/history/sync": () => ok(historySync({ ...done, imports: { total: 8, succeeded: 3, failed: 0, pending: 5 }, evidence: { matches: 3, participants: 30, timelineEvents: 4210, itemPurchases: 236 } })),
        "GET /api/v1/history/sync": () =>
          ok(historySync({ ...done, imports: { total: 8, succeeded: 8, failed: 0, pending: 0 }, evidence: { matches: 8, participants: 80, timelineEvents: 11384, itemPurchases: 641 } })),
        "GET /api/v1/matches": () => ok({ items: [matchSummary()], nextCursor: null }),
      });
      await page.goto("/historico");
      const pipeline = page.locator(".sync-pipeline");
      await expect(pipeline.getByText("Como esses dados chegaram")).toBeVisible();
      await expect(pipeline.getByRole("listitem").nth(0)).toContainText("182 ms");
      await expect(pipeline.getByRole("listitem").nth(3)).toContainText("3 de 8 partidas");
      await page.screenshot({ path: `${stages}/baixando-${width}.png`, fullPage: true });
      await expect(pipeline.getByRole("listitem").nth(3)).toContainText("8 de 8 partidas", { timeout: 8000 });
      await expect(pipeline.getByRole("listitem").nth(4)).toContainText("11.384 eventos · 641 compras de itens");
      await noHorizontalScroll(page);
      await page.screenshot({ path: `${stages}/concluido-${width}.png`, fullPage: true });
    });

    test("histórico em dia, intervalo mínimo e falhas explicam o que aconteceu", async ({ page }) => {
      let reply = historySync();
      await mockApi(page, { "POST /api/v1/history/sync": () => ok(reply) });
      const sync = page.getByRole("region", { name: "Atualização do histórico com a Riot" });

      reply = historySync({ state: "SUCCEEDED", reason: "COOLDOWN", listed: 4, alreadyKnown: 4, finishedAt: new Date().toISOString(), nextAllowedAt: later() });
      await page.goto("/historico");
      await expect(sync.getByRole("status")).toHaveText(/^Histórico em dia: nenhuma partida nova desde a verificação das .*Próxima verificação a partir das/);
      await expect(sync.getByRole("button", { name: "Atualizar" })).toBeDisabled();
      await noHorizontalScroll(page);
      await page.screenshot({ path: `${evidence}/em-dia-${width}.png`, fullPage: true });

      reply = historySync({ state: "FAILED", reason: "RIOT_AUTH_FAILED", finishedAt: new Date().toISOString(), nextAllowedAt: later() });
      await page.goto("/historico");
      await expect(sync.getByRole("status")).toHaveText(/A chave da Riot foi recusada .*Renove-a e reinicie o servidor/);
      await expect(sync).toHaveClass(/history-sync--warning/);
      await noHorizontalScroll(page);
      await page.screenshot({ path: `${evidence}/chave-recusada-${width}.png`, fullPage: true });

      reply = historySync({ state: "FAILED", reason: "RIOT_RATE_LIMITED", finishedAt: new Date().toISOString(), nextAllowedAt: later() });
      await page.goto("/historico");
      await expect(sync.getByRole("status")).toHaveText(/^Limite de requisições da Riot atingido\. Nova tentativa a partir das/);
      await noHorizontalScroll(page);
    });

    test("sem chave da Riot a importação manual continua e não há botão de atualizar", async ({ page }) => {
      await mockApi(page);
      await page.goto("/historico");
      const sync = page.getByRole("region", { name: "Atualização do histórico com a Riot" });
      await expect(sync.getByRole("status")).toHaveText(/o servidor está sem chave da Riot\. Importe partidas pelo ID\./);
      await expect(sync.getByRole("button")).toHaveCount(0);
      await page.getByRole("button", { name: "DEMO", exact: true }).click();
      await expect(page.getByRole("link", { name: "Contexto da partida DEMO-0008" })).toBeVisible();
      await noHorizontalScroll(page);
      await page.screenshot({ path: `${evidence}/sem-chave-${width}.png`, fullPage: true });
    });
  });
}
