import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json, matchSummary } from "../../test/mockApi";
import { syncMessage } from "./syncMessage";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const base = { listed: 0, newEnqueued: 0, alreadyKnown: 0, skippedPurged: 0 };

describe("CC-901: sincronização do histórico", () => {
  it("pede a sincronização ao abrir a aba e recarrega a lista quando há partidas novas", async () => {
    window.history.replaceState(null, "", "/historico");
    const api = createMockApi();
    let posts = 0;
    let listCalls = 0;
    api.on("POST", /^\/api\/v1\/history\/sync$/, () => {
      posts += 1;
      return json(202, { ...base, state: "QUEUED", requestedAt: "2026-10-19T12:00:00Z" });
    });
    api.on("GET", /^\/api\/v1\/history\/sync$/, () =>
      json(200, {
        ...base,
        state: "SUCCEEDED",
        listed: 3,
        newEnqueued: 2,
        alreadyKnown: 1,
        finishedAt: "2026-10-19T12:00:03Z",
        nextAllowedAt: "2099-01-01T00:00:00Z",
      }),
    );
    api.on("GET", /^\/api\/v1\/matches$/, () => {
      listCalls += 1;
      const items = listCalls > 1 ? [matchSummary({ matchId: "BR1_1" }), matchSummary({ matchId: "BR1_2" })] : [matchSummary({ matchId: "BR1_1" })];
      return json(200, { items, nextCursor: null });
    });
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);

    expect(await screen.findByText("Atualizando o histórico com a Riot…")).toBeInTheDocument();
    expect(await screen.findByText(/2 partidas novas encontradas/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(posts).toBe(1);
    expect(screen.getByRole("button", { name: "Atualizar" })).toBeDisabled();
    // A recarga não volta a "Carregando" e a partida nova aparece.
    expect(await screen.findByRole("link", { name: "Contexto da partida BR1_2" }, { timeout: 7000 })).toBeInTheDocument();
    expect(screen.queryByText("Carregando partidas…")).not.toBeInTheDocument();
  }, 15000);

  it("explica indisponibilidade, chave recusada, limite e intervalo mínimo", () => {
    expect(syncMessage({ ...base, state: "UNAVAILABLE", reason: "RIOT_KEY_MISSING" }, false)).toMatch(/sem chave da Riot/);
    expect(syncMessage({ ...base, state: "UNAVAILABLE", reason: "ACCOUNT_NOT_CONFIGURED" }, false)).toMatch(/conta própria não está configurada/);
    expect(syncMessage({ ...base, state: "UNAVAILABLE", reason: "ACCOUNT_NOT_LINKED" }, false)).toMatch(/Vincule sua conta Riot em Minha conta/);
    expect(syncMessage({ ...base, state: "FAILED", reason: "RIOT_AUTH_FAILED" }, false)).toMatch(/chave da Riot foi recusada/);
    expect(
      syncMessage({ ...base, state: "FAILED", reason: "RIOT_AUTH_FAILED", nextAllowedAt: "2026-10-19T13:00:00Z" }, false),
    ).toMatch(/Renove-a e reinicie o servidor .* Nova tentativa a partir das/);
    expect(
      syncMessage({ ...base, state: "FAILED", reason: "RIOT_RATE_LIMITED", nextAllowedAt: "2026-10-19T12:30:00Z" }, false),
    ).toMatch(/Limite de requisições da Riot atingido\. Nova tentativa a partir das/);
    expect(
      syncMessage(
        { ...base, state: "SUCCEEDED", reason: "COOLDOWN", newEnqueued: 2, finishedAt: "2026-10-19T12:00:00Z", nextAllowedAt: "2026-10-19T12:05:00Z" },
        false,
      ),
    ).toMatch(/^Na verificação das .*: 2 partidas novas encontradas\. Próxima verificação a partir das/);
    expect(
      syncMessage({ ...base, state: "SUCCEEDED", reason: "COOLDOWN", finishedAt: "2026-10-19T12:00:00Z", nextAllowedAt: "2026-10-19T12:05:00Z" }, false),
    ).toMatch(/Histórico em dia: nenhuma partida nova desde a verificação das .*Próxima verificação a partir das/);
    expect(syncMessage(null, true)).toMatch(/Não foi possível pedir a atualização/);
  });

  it("sem chave não mostra o botão e o botão volta quando o intervalo mínimo termina", async () => {
    window.history.replaceState(null, "", "/historico");
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [], nextCursor: null }));
    api.on("POST", /^\/api\/v1\/history\/sync$/, () => json(200, { ...base, state: "UNAVAILABLE", reason: "RIOT_KEY_MISSING" }));
    vi.stubGlobal("fetch", api.fetch);
    const { unmount } = render(<App />);
    expect(await screen.findByText(/sem chave da Riot/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Atualizar" })).not.toBeInTheDocument();
    unmount();

    const soon = new Date(Date.now() + 1500).toISOString();
    const api2 = createMockApi();
    let posts = 0;
    api2.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [], nextCursor: null }));
    api2.on("POST", /^\/api\/v1\/history\/sync$/, () => {
      posts += 1;
      return json(200, { ...base, state: "SUCCEEDED", reason: "COOLDOWN", finishedAt: new Date().toISOString(), nextAllowedAt: soon });
    });
    vi.stubGlobal("fetch", api2.fetch);
    render(<App />);
    const button = await screen.findByRole("button", { name: "Atualizar" });
    expect(button).toBeDisabled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Atualizar" })).toBeEnabled(), { timeout: 4000 });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Atualizar" }));
    });
    await waitFor(() => expect(posts).toBe(2));
  }, 15000);
});
