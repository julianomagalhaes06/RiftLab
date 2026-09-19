import { describe, expect, it } from "vitest";
import { ApiError, INVALID_RESPONSE, NETWORK_ERROR } from "./errors";
import { ERROR_MESSAGES, GENERIC_ERROR_MESSAGE, describeError, messageForCode } from "./messages";

const REQUIRED_CODES = [
  "RIOT_KEY_MISSING",
  "RIOT_AUTH_FAILED",
  "RIOT_RATE_LIMITED",
  "RIOT_NOT_FOUND",
  "UPSTREAM_UNAVAILABLE",
  "SCHEMA_UNSUPPORTED",
  "DECISIONS_NOT_PREPARED",
  "INVALID_CURSOR",
  "RESOURCE_NOT_FOUND",
  "VALIDATION_ERROR",
  "RETRY_LIMIT_EXCEEDED",
  "RAW_MATCH_NOT_CACHED",
  "TIMELINE_EVENT_MALFORMED",
  "PREPARATION_UNEXPECTED_ERROR",
  NETWORK_ERROR,
  INVALID_RESPONSE,
];

describe("tabela única de mensagens", () => {
  it.each(REQUIRED_CODES)("tem texto próprio para %s", (code) => {
    expect(ERROR_MESSAGES[code]).toBeTruthy();
    expect(messageForCode(code)).toBe(ERROR_MESSAGES[code]);
  });

  it("RIOT_AUTH_FAILED orienta a renovar a chave Development no ambiente local", () => {
    expect(messageForCode("RIOT_AUTH_FAILED")).toBe(
      "A chave da Riot é inválida ou expirou; renove a chave Development no ambiente local.",
    );
  });

  it("RIOT_NOT_FOUND nunca afirma que a partida está em andamento", () => {
    const text = messageForCode("RIOT_NOT_FOUND").toLowerCase();

    expect(text).not.toContain("andamento");
    expect(text).not.toContain("em curso");
    expect(text).not.toContain("ao vivo");
  });

  it("nenhum texto contém segredo, URL, caminho local, SQL ou stack trace", () => {
    for (const [code, text] of Object.entries(ERROR_MESSAGES)) {
      expect(text, code).not.toMatch(/RGAPI|https?:\/\/|\/app\/|\/home\/|SELECT |Traceback|\.py\b/i);
      expect(text.length, code).toBeGreaterThan(15);
    }
  });

  it("família de códigos sem entrada própria usa o texto da família", () => {
    expect(messageForCode("DATA_DRAGON_VERSION_NOT_FOUND")).toMatch(/catálogo público/i);
    expect(messageForCode("CATALOG_RECIPE_CYCLE")).toMatch(/catálogo/i);
    expect(messageForCode("SCOPE_FILE_MISSING")).toMatch(/recorte/i);
    expect(messageForCode("STATE_MALFORMED")).toMatch(/inventário/i);
  });

  it("5xx genérico vira erro interno; 404 genérico vira recurso não encontrado", () => {
    expect(messageForCode("HTTP_503")).toBe(ERROR_MESSAGES.INTERNAL_ERROR);
    expect(messageForCode("HTTP_404")).toBe(ERROR_MESSAGES.RESOURCE_NOT_FOUND);
  });

  it("código desconhecido, vazio ou ausente cai no fallback genérico", () => {
    expect(messageForCode("ALGO_NUNCA_VISTO")).toBe(GENERIC_ERROR_MESSAGE);
    expect(messageForCode("")).toBe(GENERIC_ERROR_MESSAGE);
    expect(messageForCode(null)).toBe(GENERIC_ERROR_MESSAGE);
    expect(messageForCode(undefined)).toBe(GENERIC_ERROR_MESSAGE);
  });

  it("describeError traduz ApiError e usa o fallback para qualquer outra coisa", () => {
    expect(describeError(new ApiError(409, "DECISIONS_NOT_PREPARED"))).toBe(
      ERROR_MESSAGES.DECISIONS_NOT_PREPARED,
    );
    expect(describeError(new ApiError(0, NETWORK_ERROR))).toBe(ERROR_MESSAGES[NETWORK_ERROR]);
    expect(describeError(new Error("segredo interno"))).toBe(GENERIC_ERROR_MESSAGE);
    expect(describeError("texto")).toBe(GENERIC_ERROR_MESSAGE);
  });
});
