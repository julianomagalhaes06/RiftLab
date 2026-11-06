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
  mockApi,
  ok,
  preparation,
} from "./support/mock-api";
import { dashboard } from "../../src/test/dashboard";

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

test("importar partida: formulário e demonstração cabem em 360 px", async ({ page }) => {
  await mockApi(page);

  await page.goto("/importar");
  await expect(page.getByRole("heading", { name: "Trazer uma partida para o RiftLab" })).toBeVisible();

  await expectNoHorizontalOverflow(page, "importar");
  await expectInsideViewport(page, "Analisar partida");
  await expectInsideViewport(page, "Explorar demonstração");

  await page.getByRole("button", { name: "Analisar partida" }).click(); // vazio: erro de validação
  await expect(page.getByRole("alert")).toBeVisible();
  await expectNoHorizontalOverflow(page, "importar com erro de validação");
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
    "GET /api/v1/matches/:id/timeline": () =>
      ok({
        matchId: LONG_MATCH_ID,
        dataDragonVersion: null,
        participants: [
          {
            participantId: 10,
            items: [{ timestampMs: 472_000, itemId: 3031, kind: "PURCHASE", itemClass: "MAIN" }],
            skills: [],
          },
        ],
        kills: [],
        objectives: [],
        goldFrames: [],
      }),
    "GET /api/v1/matches/:id/decisions": () =>
      ok({
        items: [
          decisionSummary({ participantId: 10 }),
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
  await expect(page.getByRole("link", { name: "Decisão em 7:52" })).toBeVisible();
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

test("Painel em 360 px: métricas → partidas → card de elo → campeões, como o protótipo (CC-1203)", async ({ page }) => {
  await mockApi(page, {
    "GET /api/v1/dashboard": () => ok(dashboard()),
    "GET /api/v1/ranked/me": () =>
      ok({
        status: "READY", reason: null, change: null,
        position: { queue: "RANKED_SOLO_5x5", state: "READY", tier: "PLATINUM", division: "III", leaguePoints: 41, wins: 10, losses: 9,
          season: "2026", observedAt: "2026-10-16T12:00:00Z", origin: "RIOT_LEAGUE_V4" },
      }),
  });
  await page.goto("/painel");
  await expect(page.getByRole("region", { name: "Seu recorte de partidas" })).toBeVisible();
  const order = await Promise.all([".panel-metrics", ".panel-recent", ".rank-card", ".panel-champions"].map(async (selector) => (await page.locator(selector).boundingBox())?.y ?? -1));
  expect(order.every((y, index) => y >= 0 && (index === 0 || y > order[index - 1]))).toBe(true);
  // O estudo fica na faixa secundária das métricas, sem bloco recolhido.
  await expect(page.getByText(/de 12 tentativas, 3 com reflexão/)).toBeVisible();
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  expect(height).toBeLessThan(3000);
  await expectNoHorizontalOverflow(page, "painel");
});

test("ordem de habilidades em 360 px: níveis de dois dígitos não quebram e a grade rola sozinha (CC-905)", async ({ page }) => {
  const skills = Array.from({ length: 18 }, (_, i) => ({ timestampMs: i * 60_000, skillSlot: [1, 2, 3, 1, 1, 4, 1, 3, 1, 3, 4, 3, 3, 2, 2, 4, 2, 2][i], levelUpType: "NORMAL" }));
  await mockApi(page, {
    "GET /api/v1/matches/:id": () => ok(matchDetail({ localParticipantId: 1 })),
    "GET /api/v1/matches/:id/timeline": () =>
      ok({ matchId: "DEMO-0008", dataDragonVersion: null, participants: [{ participantId: 1, items: [], skills }], kills: [], objectives: [], goldFrames: [] }),
  });
  await page.goto("/historico/DEMO-0008?participant=1");
  const header = page.locator(".skill-order__grid thead th").filter({ hasText: /^18$/ });
  await expect(header).toBeVisible();
  const lineHeights = await page.locator(".skill-order__grid thead th").evaluateAll((cells) =>
    cells.map((cell) => cell.getBoundingClientRect().height),
  );
  expect(Math.max(...lineHeights)).toBeLessThan(26);
  await expectNoHorizontalOverflow(page, "ordem de habilidades");
});
