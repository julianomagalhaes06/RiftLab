import { describe, expect, it } from "vitest";
import { count } from "./count";

describe("count", () => {
  it("concorda o substantivo e formata o milhar em pt-BR", () => {
    expect(count(1, "partida", "partidas")).toBe("1 partida");
    expect(count(0, "partida", "partidas")).toBe("0 partidas");
    expect(count(2, "partida carregada", "partidas carregadas")).toBe("2 partidas carregadas");
    expect(count(1250, "vitória", "vitórias")).toBe("1.250 vitórias");
  });
});
