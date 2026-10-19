import { expect, test } from "@playwright/test";
import { ATTEMPT_ID, DECISION_1, STUDY_OPTIONS, mockApi, ok } from "./support/mock-api";

const SOURCE_ID = "55555555-5555-4555-8555-555555555555";

function comparison(kind: "OWN" | "INHERITED" | "UNAVAILABLE") {
  const inherited = kind === "INHERITED";
  const unavailable = kind === "UNAVAILABLE";
  return {
    analysisId: "a7a10000-0000-4000-8000-000000000901",
    decisionId: DECISION_1,
    status: "AVAILABLE",
    engine: unavailable ? "RULES" : "POPULARITY",
    objective: { priority: "BALANCED" },
    versions: {
      model: null,
      catalog: "16.20/16.20.1",
      features: "feature_schema_v1",
      candidates: "analysis_v1",
      ranking: inherited || unavailable ? "popularity_16_19_v2" : "popularity_16_20_v1",
      objective: "objective_v1",
      itemRules: "16.20.json",
    },
    quality: { budgetSource: "UNKNOWN", frameAgeMs: null, warnings: [] },
    support: {
      level: inherited || unavailable ? "LIMITED" : "SUPPORTED",
      reasonCodes: inherited
        ? ["POPULARITY_INHERITED"]
        : unavailable
          ? ["POPULARITY_SOURCE_CATALOG_IDENTITY_UNBOUND"]
          : [],
      legalCandidates: 3,
      referenceDecisions: inherited ? 19 : 0,
      referenceSource: {
        type: kind,
        patch: inherited || unavailable ? "16.19" : "16.20",
        rankingVersion: inherited || unavailable ? "popularity_16_19_v2" : "popularity_16_20_v1",
        datasetSha256: "c".repeat(64),
        catalogId: inherited ? SOURCE_ID : null,
        catalogSha256: inherited ? "d".repeat(64) : null,
        dataDragonVersion: inherited ? "16.19.1" : null,
        mappingVersion: inherited ? "mapping-v1" : null,
        rawHash: inherited ? "e".repeat(64) : null,
        curationRulesVersion: inherited ? "data_dragon_v5" : null,
      },
    },
    candidates: STUDY_OPTIONS.map((candidate, index) => ({
      ...candidate,
      rank: index + 1,
      baseRank: index + 1,
      score: 1 - index / 10,
      remainingCost: 100 + index * 100,
      affordability: "UNKNOWN",
      evidenceCodes: [],
      tags: [],
      explanations: [],
    })),
    scoreNote: "Pontuações são relativas a esta decisão; não são probabilidades.",
  };
}

function revealed(kind: "OWN" | "INHERITED" | "UNAVAILABLE") {
  return {
    attemptId: ATTEMPT_ID,
    decisionId: DECISION_1,
    status: "REVEALED",
    mode: "STUDY",
    objective: { priority: "BALANCED" },
    context: {},
    options: STUDY_OPTIONS,
    hintLevel: 0,
    hints: [],
    reasonCodes: [],
    selectedItemId: STUDY_OPTIONS[0].itemId,
    skipped: false,
    rationale: null,
    analysisId: comparison(kind).analysisId,
    comparison: comparison(kind),
    historicalItemId: STUDY_OPTIONS[1].itemId,
    reflection: null,
  };
}

test("origens OWN, INHERITED e indisponível permanecem claras em 360 e 1280 px", async ({ page }) => {
  await page.route("https://**/*", (route) => route.abort());
  let activeKind: "OWN" | "INHERITED" | "UNAVAILABLE" = "OWN";
  const calls = await mockApi(page, {
    "POST /api/v1/learning-attempts/:id/reveal": () => ok(revealed(activeKind)),
  });
  for (const width of [360, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    for (const kind of ["OWN", "INHERITED", "UNAVAILABLE"] as const) {
      activeKind = kind;
      await page.goto(`/decisions/${DECISION_1}?mode=practice`);
      await page.getByRole("button", { name: "Confirmar objetivo e começar" }).click();
      await page.getByLabel("Item Composto Sintético").check();
      await page.getByRole("button", { name: "Responder" }).click();
      await page.getByRole("button", { name: "Revelar comparação" }).click();
      expect(calls.count("POST /api/v1/learning-attempts/:id/reveal")).toBeGreaterThan(0);
      const source = page.getByTestId("reference-source");
      if (kind === "INHERITED") {
        await expect(source).toContainText("patch 16.19 (herdada");
        await expect(source).toContainText("filtrada pelo catálogo desta decisão");
        await expect(page.getByText(/As contagens de referência foram herdadas/)).toBeVisible();
        await expect(
          page.getByText(/Critério: Popularidade nas decisões de referência do patch 16\.19 deste campeão/),
        ).toBeVisible();
      } else if (kind === "OWN") {
        await expect(source).toHaveText(/patch 16.20 \(própria\)/);
      } else {
        await expect(source).toHaveText(/patch 16.19 não foi usada/);
        await expect(page.getByLabel("Motivo da referência indisponível")).toContainText(
          /não identifica exatamente o catálogo de origem/,
        );
      }
      const widths = await page.evaluate(() => ({
        document: document.documentElement.scrollWidth,
        viewport: document.documentElement.clientWidth,
      }));
      expect(widths.document).toBeLessThanOrEqual(widths.viewport);
    }
  }
});
