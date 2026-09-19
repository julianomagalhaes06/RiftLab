import { describe, expect, it } from "vitest";
import {
  describeBudget,
  describeBudgetShort,
  formatAge,
  formatGameTime,
  itemLabel,
  ordinalLabel,
  reasonText,
  roleLabel,
  subReasonText,
  trinketLabel,
} from "./format";

const base = { immediatePurchaseAssertable: true, staleFrame: false } as const;

describe("formatGameTime / formatAge", () => {
  it("formata mm:ss do jogo e a idade do quadro", () => {
    expect(formatGameTime(472_000)).toBe("7:52");
    expect(formatGameTime(0)).toBe("0:00");
    expect(formatGameTime(3_605_000)).toBe("60:05");
    expect(formatAge(20_000)).toBe("há 20 s");
    expect(formatAge(472_000)).toBe("há 7 min 52 s");
    expect(formatAge(120_000)).toBe("há 2 min");
  });
});

describe("describeBudget: nunca inventa valor", () => {
  it("valor desconhecido é 'desconhecido', NUNCA 0", () => {
    const result = describeBudget({ ...base, value: null, source: "UNKNOWN", frameAgeMs: null });

    expect(result.tone).toBe("unknown");
    expect(result.headline).toBe("Orçamento desconhecido");
    expect(JSON.stringify(result)).not.toMatch(/\b0 de ouro|\b0\b/);
  });

  it("valor ausente com fonte FRAME_ESTIMATE também é desconhecido", () => {
    const result = describeBudget({ ...base, value: undefined, source: "FRAME_ESTIMATE", frameAgeMs: 5000 });

    expect(result.headline).toBe("Orçamento desconhecido");
  });

  it("zero legítimo é mostrado como zero (é diferente de ausente)", () => {
    const result = describeBudget({ ...base, value: 0, source: "FRAME_ESTIMATE", frameAgeMs: 10_000 });

    expect(result.headline).toBe("Aproximadamente 0 de ouro");
  });

  it("estimativa de quadro é 'aproximado' e informa a idade do quadro", () => {
    const result = describeBudget({ ...base, value: 1640, source: "FRAME_ESTIMATE", frameAgeMs: 20_000 });

    expect(result.tone).toBe("approximate");
    expect(result.headline).toBe(`Aproximadamente ${(1640).toLocaleString("pt-BR")} de ouro`);
    expect(result.notes).toEqual(["Estimado a partir do quadro observado há 20 s."]);
  });

  it("quadro antigo: 'snapshot antigo; orçamento não confirmável', com a idade", () => {
    const result = describeBudget({
      value: 640,
      source: "FRAME_ESTIMATE",
      frameAgeMs: 472_000,
      staleFrame: true,
      immediatePurchaseAssertable: false,
    });

    expect(result.tone).toBe("stale");
    expect(result.headline).toBe("Snapshot antigo; orçamento não confirmável");
    expect(result.notes.join(" ")).toContain("há 7 min 52 s");
    expect(result.notes.join(" ")).toContain("não confirmável");
    expect(result.notes).toContain("Não é possível afirmar que a compra imediata era possível.");
  });

  it("quadro antigo sem valor não mostra número nenhum", () => {
    const result = describeBudget({
      value: null,
      source: "UNKNOWN",
      frameAgeMs: null,
      staleFrame: true,
      immediatePurchaseAssertable: false,
    });

    expect(result.tone).toBe("stale");
    expect(result.notes.join(" ")).not.toMatch(/de ouro/);
  });

  it("valor informado pelo usuário é rotulado como tal", () => {
    const result = describeBudget({ ...base, value: 900, source: "USER_PROVIDED", frameAgeMs: null });

    expect(result.tone).toBe("provided");
    expect(result.headline).toMatch(/Informado por você/);
  });
});

describe("describeBudgetShort (lista)", () => {
  it("cobre estimativa, snapshot antigo, desconhecido e informado", () => {
    expect(describeBudgetShort("FRAME_ESTIMATE", 20_000, false)).toEqual({
      tone: "approximate",
      text: "Orçamento aproximado (quadro há 20 s)",
    });
    expect(describeBudgetShort("FRAME_ESTIMATE", 472_000, true).text).toBe(
      "Snapshot antigo; orçamento não confirmável",
    );
    expect(describeBudgetShort("UNKNOWN", null, false).text).toBe("Orçamento desconhecido");
    expect(describeBudgetShort("USER_PROVIDED", null, false).tone).toBe("provided");
  });
});

describe("motivos em português", () => {
  it("traduz os motivos de contexto do contrato", () => {
    expect(reasonText("MISSING_CATALOG")).toMatch(/Catálogo/);
    expect(reasonText("OUT_OF_SCOPE")).toMatch(/recorte/);
    expect(reasonText("INVENTORY_UNCERTAIN")).toMatch(/incerto/);
  });

  it("TARGET_NOT_ELIGIBLE é neutro: não revela nada sobre o item nem sobre a compra", () => {
    const text = reasonText("TARGET_NOT_ELIGIBLE");

    expect(text).toBe("Esta decisão não é elegível para análise.");
    expect(text).not.toMatch(/item|compra|desfe|classe|revert|undo/i);
  });

  it("motivo desconhecido mostra o código, sem inventar significado", () => {
    expect(reasonText("MOTIVO_NOVO")).toBe("Motivo não classificado (MOTIVO_NOVO).");
    expect(subReasonText("CODIGO_NOVO")).toBe("CODIGO_NOVO");
    expect(subReasonText("PATCH_OUT_OF_SCOPE")).toBe("patch fora do recorte");
  });
});

describe("rótulos honestos", () => {
  it("item sem nome mostra o ID com aviso; com nome mostra o nome", () => {
    expect(itemLabel({ itemId: 3006, name: null })).toBe("Item #3006, nome indisponível");
    expect(itemLabel({ itemId: 3006, name: undefined })).toBe("Item #3006, nome indisponível");
    expect(itemLabel({ itemId: 3006, name: "Botas" })).toBe("Botas");
  });

  it("trinket vazio é 'Vazio'; presente mostra o ID", () => {
    expect(trinketLabel(null)).toBe("Vazio");
    expect(trinketLabel(3340)).toBe("Item #3340");
  });

  it("função ausente é 'não informada'; ordinal ausente é null", () => {
    expect(roleLabel(null)).toBe("Função não informada");
    expect(roleLabel("UTILITY")).toBe("Suporte");
    expect(roleLabel("OUTRA")).toBe("Função OUTRA");
    expect(ordinalLabel(2)).toBe("2º item principal");
    expect(ordinalLabel(null)).toBeNull();
  });
});
