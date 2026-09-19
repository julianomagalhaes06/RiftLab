import { describe, expect, it } from "vitest";
import { problem } from "../../test/mockApi";
import { ApiError, isAbortError, isApiError, parseProblem } from "./errors";

describe("parseProblem", () => {
  it("extrai {status, code, requestId} de um problem+json", async () => {
    const error = await parseProblem(problem(409, "DECISIONS_NOT_PREPARED"));

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.code).toBe("DECISIONS_NOT_PREPARED");
    expect(error.requestId).toBe("req-123");
  });

  it("nunca guarda o detail livre nem o corpo do servidor", async () => {
    const error = await parseProblem(problem(500, "INTERNAL_ERROR", "SELECT * FROM segredo"));

    expect(error.message).toBe("500 INTERNAL_ERROR");
    expect(JSON.stringify(error)).not.toContain("segredo");
  });

  it("corpo que não é JSON vira HTTP_<status>", async () => {
    const error = await parseProblem(new Response("<html>Bad Gateway</html>", { status: 502 }));

    expect(error.code).toBe("HTTP_502");
    expect(error.requestId).toBeNull();
  });

  it("code fora do formato MAIÚSCULAS_COM_UNDERSCORE não é repassado", async () => {
    const response = new Response(JSON.stringify({ code: "<script>alert(1)</script>" }), {
      status: 400,
    });

    expect((await parseProblem(response)).code).toBe("HTTP_400");
  });

  it("requestId com formato estranho é descartado", async () => {
    const response = new Response(JSON.stringify({ code: "X_Y", requestId: "a b<c>" }), {
      status: 422,
    });

    expect((await parseProblem(response)).requestId).toBeNull();
  });

  it("problem+json de tipo inesperado (array, número) cai no fallback", async () => {
    expect((await parseProblem(new Response("[1,2]", { status: 404 }))).code).toBe("HTTP_404");
    expect((await parseProblem(new Response("7", { status: 404 }))).code).toBe("HTTP_404");
  });
});

describe("isApiError / isAbortError", () => {
  it("reconhece ApiError e cancelamento", () => {
    expect(isApiError(new ApiError(0, "NETWORK_ERROR"))).toBe(true);
    expect(isApiError(new Error("x"))).toBe(false);
    expect(isAbortError(new DOMException("cancelado", "AbortError"))).toBe(true);
    expect(isAbortError(new Error("x"))).toBe(false);
    expect(isAbortError(null)).toBe(false);
  });
});
