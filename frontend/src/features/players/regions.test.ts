import { describe, expect, it } from "vitest";
import { DEFAULT_REGION, REGIONS, regionById, resolveRiotId } from "./regions";

describe("servidores da busca", () => {
  it("só o BR está ativo, com a TAG padrão BR1; os demais esperam suporte no servidor", () => {
    expect(DEFAULT_REGION).toMatchObject({ id: "BR", defaultTag: "BR1", enabled: true });
    expect(REGIONS.filter((region) => region.enabled).map((region) => region.id)).toEqual(["BR"]);
    expect(REGIONS.every((region) => region.enabled || region.defaultTag === null)).toBe(true);
    expect(new Set(REGIONS.map((region) => region.id)).size).toBe(REGIONS.length);
  });

  it("servidor desabilitado ou desconhecido cai no BR", () => {
    expect(regionById("KR")).toBe(DEFAULT_REGION);
    expect(regionById("XYZ")).toBe(DEFAULT_REGION);
  });

  it("sem TAG usa a do servidor; com TAG, vale a digitada", () => {
    expect(resolveRiotId("Fate Sealed")).toEqual({ gameName: "Fate Sealed", tagLine: "BR1" });
    expect(resolveRiotId("  Fate Sealed#  ")).toEqual({ gameName: "Fate Sealed", tagLine: "BR1" });
    expect(resolveRiotId("Fate Sealed#Kai1")).toEqual({ gameName: "Fate Sealed", tagLine: "Kai1" });
    expect(resolveRiotId("Fate#X")).toBeNull(); // TAG curta demais
    expect(resolveRiotId("x".repeat(17))).toBeNull(); // nome acima de 16 caracteres
    expect(resolveRiotId("   ")).toBeNull();
    expect(resolveRiotId("#")).toBeNull();
    // Servidor sem TAG padrão conhecida: sem TAG, não adivinha.
    expect(resolveRiotId("Fate", { ...DEFAULT_REGION, defaultTag: null })).toBeNull();
  });
});
