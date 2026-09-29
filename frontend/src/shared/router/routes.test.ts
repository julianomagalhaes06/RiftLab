import { describe, expect, it } from "vitest";
import { isDemoMatchId, isMatchId, isRealMatchId, parseRoute, routeToPath } from "./routes";

const UUID = "688bd44d-39b7-4f7b-956b-0fd4a63cfafd";

describe("parseRoute", () => {
  it("reconhece as quatro rotas da jornada e o protótipo", () => {
    expect(parseRoute("/")).toEqual({ name: "home" });
    expect(parseRoute("/painel")).toEqual({ name: "panel" });
    expect(parseRoute("/campeoes")).toEqual({ name: "champions" });
    expect(parseRoute("/minha-posicao")).toEqual({ name: "position" });
    expect(parseRoute("/historico")).toEqual({ name: "history" });
    expect(parseRoute("/historico/DEMO-0008", "?participant=3")).toEqual({
      name: "historyMatch", matchId: "DEMO-0008", participantId: 3,
    });
    expect(parseRoute(`/imports/${UUID}`)).toEqual({ name: "import", jobId: UUID });
    expect(parseRoute("/matches/DEMO-0008", "?participant=3")).toEqual({
      name: "match",
      matchId: "DEMO-0008",
      participantId: 3,
    });
    expect(parseRoute("/matches/BR1_3120345678")).toEqual({
      name: "match",
      matchId: "BR1_3120345678",
      participantId: null,
    });
    expect(parseRoute(`/decisions/${UUID}`)).toEqual({ name: "decision", decisionId: UUID });
    expect(parseRoute("/prototipo")).toEqual({ name: "prototype" });
  });

  it("tolera barra final e normaliza o UUID para minúsculas", () => {
    expect(parseRoute("/matches/DEMO-0008/")).toMatchObject({ name: "match" });
    expect(parseRoute(`/imports/${UUID.toUpperCase()}`)).toEqual({ name: "import", jobId: UUID });
  });

  it.each([
    "/imports/nao-e-uuid",
    "/matches/br1_1",
    "/matches/BR1_",
    "/matches/DEMO-1",
    "/matches/BR1_1/extra",
    "/decisions/123",
    "/api/v1/matches",
    "/qualquer-coisa",
    "/prototipo/x",
  ])("%s não é rota válida (notFound)", (path) => {
    expect(parseRoute(path).name).toBe("notFound");
  });

  it.each(["", "?participant=0", "?participant=11", "?participant=abc", "?participant=-1", "?participant=1.5"])(
    "participant inválido (%s) vira null",
    (search) => {
      expect(parseRoute("/matches/DEMO-0008", search)).toMatchObject({ participantId: null });
    },
  );

  it("segmento com escape inválido não lança", () => {
    expect(parseRoute("/matches/%E0%A4%A").name).toBe("notFound");
  });
});

describe("routeToPath", () => {
  it("faz o caminho inverso de parseRoute (ida e volta)", () => {
    const paths = [
      "/",
      "/painel",
      "/campeoes",
      "/minha-posicao",
      "/historico",
      "/historico/DEMO-0008?participant=3",
      `/imports/${UUID}`,
      "/matches/DEMO-0008",
      "/matches/DEMO-0008?participant=7",
      `/decisions/${UUID}`,
      "/prototipo",
    ];
    for (const path of paths) {
      const [pathname, search = ""] = path.split("?");
      expect(routeToPath(parseRoute(pathname, search ? `?${search}` : ""))).toBe(path);
    }
  });
});

describe("validadores de ID de partida", () => {
  it("distingue real, demonstração e inválido", () => {
    expect(isRealMatchId("BR1_1234567890")).toBe(true);
    expect(isRealMatchId("BR1_")).toBe(false);
    expect(isRealMatchId("DEMO-0008")).toBe(false);
    expect(isRealMatchId(`BR1_${"9".repeat(21)}`)).toBe(false);
    expect(isDemoMatchId("DEMO-0008")).toBe(true);
    expect(isMatchId("DEMO-0008") && isMatchId("BR1_7")).toBe(true);
    expect(isMatchId("http://evil")).toBe(false);
  });
});
