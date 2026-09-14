import { describe, expect, it, vi } from "vitest";
import { fetchHealthState } from "./health";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("fetchHealthState", () => {
  it("retorna available quando live e ready respondem 200", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { status: "UP" }))
      .mockResolvedValueOnce(jsonResponse(200, { status: "UP", database: "UP" }));

    const state = await fetchHealthState(fetchImpl);

    expect(state).toEqual({ kind: "available" });
  });

  it("retorna degraded com detail do problem+json quando ready falha", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { status: "UP" }))
      .mockResolvedValueOnce(
        jsonResponse(503, { code: "DATABASE_UNAVAILABLE", detail: "banco fora do ar" }),
      );

    const state = await fetchHealthState(fetchImpl);

    expect(state).toEqual({ kind: "degraded", detail: "banco fora do ar" });
  });

  it("retorna unavailable quando live não responde", async () => {
    const fetchImpl = vi.fn().mockRejectedValueOnce(new Error("network error"));

    const state = await fetchHealthState(fetchImpl);

    expect(state.kind).toBe("unavailable");
  });

  it("não chama /health/ready quando live falha", async () => {
    const fetchImpl = vi.fn().mockRejectedValueOnce(new Error("network error"));

    await fetchHealthState(fetchImpl);

    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
