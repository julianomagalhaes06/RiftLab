import { expect, test, type Page } from "@playwright/test";
import {
  DECISION_1,
  DECISION_2,
  JOB_ID,
  LONG_MATCH_ID,
  decisionState,
  decisionSummary,
  job,
  matchDetail,
  matchSummary,
  mockApi,
  ok,
  preparation,
} from "./support/mock-api";

/**
 * 360 px (CC-205, critério "360 px sem overflow"): em cada página do fluxo o
 * documento não rola na horizontal e as ações principais ficam visíveis dentro da
 * largura. Conteúdo deliberadamente hostil: IDs longos, nomes de item longos, dez
 * participantes, vários motivos e o bloco de detalhes técnicos aberto.
 */
test.use({ viewport: { width: 360, height: 740 } });

const LONG_ITEM = "Componente Sintético Extremamente Longo Para Estressar O Layout Em Trezentos E Sessenta Pixels";

async function expectNoHorizontalOverflow(page: Page, label: string) {
  const metrics = await page.evaluate(() => {
    const client = document.documentElement.clientWidth;
    const offenders = [...document.querySelectorAll<HTMLElement>("body *")]
      .filter((el) => {
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.right > client + 0.5 && !el.closest(".skip-link");
      })
      .slice(0, 5)
      .map((el) => `${el.tagName.toLowerCase()}.${el.className}`);
    return {
      client,
      doc: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
      offenders,
    };
  });
  expect(metrics.doc, `${label}: documento mais largo que a tela; ${JSON.stringify(metrics)}`).toBeLessThanOrEqual(
    metrics.client,
  );
  expect(metrics.body, `${label}: body mais largo que a tela; ${JSON.stringify(metrics)}`).toBeLessThanOrEqual(
    metrics.client,
  );
}

async function expectInsideViewport(page: Page, name: string | RegExp, role: "button" | "link" = "button") {
  const target = page.getByRole(role, { name }).first();
  await expect(target).toBeVisible();
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  expect(box, `${String(name)} sem caixa`).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(360);
}

test("entrada: formulário, demonstração e lista de partidas cabem em 360 px", async ({ page }) => {
  await mockApi(page, {
    "GET /api/v1/matches": () =>
      ok({
        items: [
          matchSummary({ matchId: LONG_MATCH_ID, patch: "16.17.1234.5678", preparationStatus: "READY" }),
          matchSummary({ matchId: "DEMO-0008", preparationStatus: "RUNNING" }),
          matchSummary({ matchId: "BR1_9", preparationStatus: "FAILED" }),
        ],
        nextCursor: "MAIS",
      }),
  });

  await page.goto("/");
  await expect(page.getByRole("link", { name: new RegExp(LONG_MATCH_ID) })).toBeVisible();

  await expectNoHorizontalOverflow(page, "entrada");
  await expectInsideViewport(page, "Analisar partida");
  await expectInsideViewport(page, "Explorar demonstração");
  await expectInsideViewport(page, "Carregar mais partidas");

  await page.getByRole("button", { name: "Analisar partida" }).click(); // vazio: erro de validação
  await expect(page.getByRole("alert")).toBeVisible();
  await expectNoHorizontalOverflow(page, "entrada com erro de validação");
});

test("progresso da importação (em andamento, falha e sucesso) cabe em 360 px", async ({ page }) => {
  await mockApi(page, {
    "GET /api/v1/imports/:id": () => ok(job({ status: "RUNNING", attempts: 2 })),
  });
  await page.goto(`/imports/${JOB_ID}`);
  await expect(page.getByRole("status")).toContainText("Importando a partida");
  await expectNoHorizontalOverflow(page, "importação em andamento");

  await mockApi(page, {
    "GET /api/v1/imports/:id": () => ok(job({ status: "FAILED", attempts: 1, errorCode: "RIOT_AUTH_FAILED" })),
  });
  await page.goto(`/imports/${JOB_ID}`);
  await expect(page.getByText(/chave da Riot é inválida ou expirou/)).toBeVisible();
  await expectNoHorizontalOverflow(page, "importação com falha");
  await expectInsideViewport(page, "Tentar importar novamente");
});

test("partida (preparando, falha, pronta) e decisões cabem em 360 px", async ({ page }) => {
  await mockApi(page, {
    "GET /api/v1/matches/:id": () => ok(matchDetail({ matchId: LONG_MATCH_ID, preparation: preparation({ status: "RUNNING" }) })),
  });
  await page.goto(`/matches/${LONG_MATCH_ID}`);
  await expect(page.getByRole("status")).toContainText("Preparando a partida");
  await expectNoHorizontalOverflow(page, "partida preparando");

  await mockApi(page, {
    "GET /api/v1/matches/:id": () =>
      ok(matchDetail({ matchId: LONG_MATCH_ID, preparation: preparation({ status: "FAILED", errorCode: "TIMELINE_EVENT_MALFORMED" }) })),
  });
  await page.goto(`/matches/${LONG_MATCH_ID}`);
  await expect(page.getByText(/evento da linha do tempo está malformado/)).toBeVisible();
  await expectNoHorizontalOverflow(page, "partida com falha de preparação");
  await expectInsideViewport(page, "Tentar preparar novamente");

  await mockApi(page, {
    "GET /api/v1/matches/:id": () => ok(matchDetail({ matchId: LONG_MATCH_ID })),
    "GET /api/v1/matches/:id/decisions": () =>
      ok({
        items: [
          decisionSummary(),
          decisionSummary({
            decisionId: DECISION_2,
            timestampMs: 3_605_000,
            eligible: false,
            reasonCodes: ["MISSING_CATALOG", "OUT_OF_SCOPE", "INVENTORY_UNCERTAIN"],
            budgetSource: "FRAME_ESTIMATE",
            frameAgeMs: 472_000,
            staleFrame: true,
            mainItemOrdinal: 2,
          }),
        ],
        nextCursor: "MAIS",
      }),
  });
  await page.goto(`/matches/${LONG_MATCH_ID}?participant=10`);
  await expect(page.getByText("Decisão em 7:52")).toBeVisible();
  await expect(page.getByRole("link", { name: /Participante 10/ })).toBeVisible();
  await expectNoHorizontalOverflow(page, "partida pronta com decisões");
  await expectInsideViewport(page, "Carregar mais decisões");
  await expectInsideViewport(page, /Decisão em 7:52/, "link");
});

test("detalhe da decisão (nome longo, orçamento antigo, técnicos abertos) cabe em 360 px", async ({ page }) => {
  await mockApi(page, {
    "GET /api/v1/decisions/:id": () =>
      ok(
        decisionState({
          decisionId: DECISION_1,
          matchId: LONG_MATCH_ID,
          eligible: false,
          reasons: [
            { code: "INVENTORY_UNCERTAIN", subReasons: ["ITEM_UNKNOWN", "OWNED_ITEM_UNCLASSIFIABLE"] },
            { code: "OUT_OF_SCOPE", subReasons: ["PATCH_OUT_OF_SCOPE", "MAIN_ITEM_ORDINAL_BEYOND_SECOND"] },
          ],
          inventory: {
            items: [
              { itemId: 100001, quantity: 2, name: LONG_ITEM },
              { itemId: 3006123456, quantity: 1, name: null },
            ],
            trinketItemId: 3340,
          },
          budget: {
            value: 640,
            source: "FRAME_ESTIMATE",
            frameAgeMs: 472_000,
            staleFrame: true,
            codes: ["STALE_FRAME"],
            immediatePurchaseAssertable: false,
          },
        }),
      ),
  });

  await page.goto(`/decisions/${DECISION_1}`);
  await expect(page.getByText(LONG_ITEM)).toBeVisible();
  await page.getByText("Detalhes técnicos").click(); // abre o bloco recolhível
  await expect(page.getByText("decision_selection_v1")).toBeVisible();

  await expectNoHorizontalOverflow(page, "detalhe da decisão");
  await expectInsideViewport(page, /Voltar às decisões da partida/, "link");
});

test("página não encontrada e protótipo ilustrativo cabem em 360 px", async ({ page }) => {
  await mockApi(page);
  await page.goto("/rota/que/nao/existe");
  await expect(page.getByRole("heading", { name: "Página não encontrada" })).toBeVisible();
  await expectNoHorizontalOverflow(page, "página não encontrada");

  await page.goto("/prototipo");
  await expect(page.locator("#entrada-heading")).toBeVisible();
  await expectNoHorizontalOverflow(page, "protótipo (entrada)");
});
