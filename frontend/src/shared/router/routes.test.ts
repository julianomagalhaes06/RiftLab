import { describe, expect, it } from "vitest";
import { isDemoMatchId, isMatchId, isRealMatchId, loginPath, parseRiotId, parseRoute, routeToPath, safeNext } from "./routes";

const UUID = "688bd44d-39b7-4f7b-956b-0fd4a63cfafd";

describe("parseRoute", () => {
  it("reconhece as quatro rotas da jornada e o protótipo", () => {
    expect(parseRoute("/")).toEqual({ name: "home" });
    expect(parseRoute("/painel")).toEqual({ name: "panel" });
    expect(parseRoute("/entrar")).toEqual({ name: "login", next: null });
    expect(parseRoute("/criar-conta", "?next=%2Fpainel")).toEqual({ name: "register", next: "/painel" });
    expect(parseRoute("/importar")).toEqual({ name: "importEntry" });
    expect(parseRoute("/campeoes")).toEqual({ name: "champions" });
    expect(parseRoute("/minha-posicao")).toEqual({ name: "position" });
    expect(parseRoute("/conta")).toEqual({ name: "account" });
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
      "/entrar",
      "/criar-conta?next=%2Fdecisions%2Fabc",
      "/importar",
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

describe("volta depois de entrar", () => {
  it("aceita só caminhos internos", () => {
    expect(safeNext("/jogador/Ana-BR1")).toBe("/jogador/Ana-BR1");
    expect(safeNext("//outro.site/x")).toBeNull();
    expect(safeNext("/\\outro.site")).toBeNull();
    expect(safeNext("https://outro.site")).toBeNull();
    expect(safeNext(null)).toBeNull();
    expect(parseRoute("/entrar", "?next=https%3A%2F%2Foutro.site")).toEqual({ name: "login", next: null });
  });

  it("monta o caminho de entrar com o retorno", () => {
    expect(loginPath("/decisions/x?y=1")).toBe("/entrar?next=%2Fdecisions%2Fx%3Fy%3D1");
    expect(loginPath("/", "register")).toBe("/criar-conta");
    expect(loginPath(null)).toBe("/entrar");
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

describe("perfil de jogador por Riot ID (CC-1102)", () => {
  it("lê Nome#TAG digitado, com espaço e acento no nome", () => {
    expect(parseRiotId("  Pão de Queijo #BR1 ")).toEqual({ gameName: "Pão de Queijo", tagLine: "BR1" });
    expect(parseRiotId("a#b#9999")).toEqual({ gameName: "a#b", tagLine: "9999" });
    for (const invalid of ["SemTag", "#BR1", "Nome#B", "Nome#BR-1", "Nome#123456", `${"x".repeat(17)}#BR1`]) {
      expect(parseRiotId(invalid)).toBeNull();
    }
  });

  it("/jogador/Nome-TAG ida e volta, com o último hífen separando a TAG", () => {
    const route = { name: "player" as const, gameName: "Meu-Nome Legal", tagLine: "BR1" };
    const path = routeToPath(route);
    expect(path).toBe("/jogador/Meu-Nome%20Legal-BR1");
    expect(parseRoute(path)).toEqual(route);
    expect(parseRoute("/jogador/SemTag").name).toBe("notFound");
  });

  it("/jogador/Nome-TAG/ao-vivo ida e volta (CC-1211)", () => {
    const route = { name: "live" as const, gameName: "Meu-Nome Legal", tagLine: "BR1" };
    const path = routeToPath(route);
    expect(path).toBe("/jogador/Meu-Nome%20Legal-BR1/ao-vivo");
    expect(parseRoute(path)).toEqual(route);
    expect(parseRoute("/jogador/SemTag/ao-vivo").name).toBe("notFound");
    expect(parseRoute("/jogador/Nome-BR1/outra").name).toBe("notFound");
  });
});
