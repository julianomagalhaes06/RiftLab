import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json, problem } from "../../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const USER = { id: "11111111-1111-4111-8111-111111111111", email: "jogadora@example.test" };
const PENDING = { state: "PENDING", gameName: "Jogadora", tagLine: "BR1", iconId: 7,
  expiresAt: "2026-11-04T12:10:00Z", linkedAt: null };

describe("Minha conta: vínculo pelo ícone de perfil (Sprint 2, #12)", () => {
  it("pede o ícone, explica o ícone errado e vincula quando confere", async () => {
    window.history.replaceState(null, "", "/conta");
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, { authRequired: true, user: USER }));
    mock.on("GET", /^\/api\/v1\/catalog\/current$/, () => json(200, { gamePatch: "16.21", dataDragonVersion: "16.21.1" }));
    mock.on("GET", /^\/api\/v1\/riot-link$/, () => json(200, { state: "NONE" }));
    mock.on("POST", /^\/api\/v1\/riot-link\/challenge$/, () => json(200, PENDING));
    let verifications = 0;
    mock.on("POST", /^\/api\/v1\/riot-link\/verify$/, () => {
      verifications += 1;
      return verifications === 1 ? problem(409, "ICON_MISMATCH")
        : json(200, { state: "LINKED", gameName: "Jogadora", tagLine: "BR1", linkedAt: "2026-11-04T12:05:00Z" });
    });
    mock.on("DELETE", /^\/api\/v1\/riot-link$/, () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);

    const input = await screen.findByLabelText("Riot ID (Nome#TAG)");
    fireEvent.change(input, { target: { value: "sem-tag" } });
    fireEvent.click(screen.getByRole("button", { name: "Pedir ícone" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nome#TAG");
    expect(mock.callsTo("POST", /challenge$/)).toHaveLength(0);

    fireEvent.change(input, { target: { value: "Jogadora#BR1" } });
    fireEvent.click(screen.getByRole("button", { name: "Pedir ícone" }));
    expect(await screen.findByRole("img", { name: "Ícone de perfil número 7" }))
      .toHaveAttribute("src", "https://ddragon.leagueoflegends.com/cdn/16.21.1/img/profileicon/7.png");
    expect(mock.callsTo("POST", /challenge$/)[0].body).toEqual({ gameName: "Jogadora", tagLine: "BR1" });

    fireEvent.click(screen.getByRole("button", { name: "Verificar agora" }));
    expect(await screen.findByText(/ainda não é o pedido/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Verificar agora" }));
    expect(await screen.findByRole("heading", { name: "Conta Riot vinculada" })).toBeInTheDocument();
    expect(document.querySelector(".account-card__riot")).toHaveTextContent("Jogadora#BR1");

    fireEvent.click(screen.getByRole("button", { name: "Desvincular conta" }));
    expect(await screen.findByRole("heading", { name: "Vincular conta Riot" })).toBeInTheDocument();
  });

  it("sem contas locais, explica que a conta vem da configuração", async () => {
    window.history.replaceState(null, "", "/conta");
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, { authRequired: false, user: null }));
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    expect(await screen.findByText(/não usa contas locais/)).toBeInTheDocument();
    expect(mock.callsTo("GET", /^\/api\/v1\/riot-link$/)).toHaveLength(0);
  });

  describe("conferência automática do ícone", () => {
    function setupPending(verify: () => Response) {
      window.history.replaceState(null, "", "/conta");
      const mock = createMockApi();
      mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, { authRequired: true, user: USER }));
      mock.on("GET", /^\/api\/v1\/catalog\/current$/, () => json(200, { gamePatch: "16.21", dataDragonVersion: "16.21.1" }));
      mock.on("GET", /^\/api\/v1\/riot-link$/, () => json(200, PENDING));
      mock.on("POST", /^\/api\/v1\/riot-link\/verify$/, verify);
      vi.stubGlobal("fetch", mock.fetch);
      render(<App />);
      return mock;
    }
    const verifications = (mock: ReturnType<typeof createMockApi>) => mock.callsTo("POST", /riot-link\/verify$/).length;
    const setHidden = (hidden: boolean) => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
      document.dispatchEvent(new Event("visibilitychange"));
    };

    beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
    afterEach(() => {
      Reflect.deleteProperty(document, "hidden");
      vi.useRealTimers();
    });

    it("vincula sozinho quando a Riot atualiza, sem aviso de erro enquanto espera", async () => {
      let calls = 0;
      const mock = setupPending(() => {
        calls += 1;
        return calls === 1 ? problem(409, "ICON_MISMATCH") : json(200, { state: "LINKED", gameName: "Jogadora", tagLine: "BR1", linkedAt: "2026-11-04T12:05:00Z" });
      });
      expect(await screen.findByText(/Conferindo sozinho a cada 30 segundos/)).toBeInTheDocument();
      expect(verifications(mock)).toBe(0);

      await vi.advanceTimersByTimeAsync(30_000);
      expect(verifications(mock)).toBe(1);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument(); // ainda não apareceu: silêncio

      await vi.advanceTimersByTimeAsync(30_000);
      expect(await screen.findByRole("heading", { name: "Conta Riot vinculada" })).toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(60_000);
      expect(verifications(mock)).toBe(2); // vinculou: para de consultar
    });

    it("com a aba oculta não gasta cota, e confere ao voltar", async () => {
      const mock = setupPending(() => problem(409, "ICON_MISMATCH"));
      await screen.findByText(/Conferindo sozinho/);
      setHidden(true);
      await vi.advanceTimersByTimeAsync(90_000);
      expect(verifications(mock)).toBe(0);
      setHidden(false);
      await vi.advanceTimersByTimeAsync(0);
      expect(verifications(mock)).toBe(1);
    });

    it("prazo vencido encerra a conferência e explica", async () => {
      const mock = setupPending(() => problem(409, "CHALLENGE_EXPIRED"));
      await screen.findByText(/Conferindo sozinho/);
      await vi.advanceTimersByTimeAsync(30_000);
      expect(await screen.findByRole("alert")).toHaveTextContent("20 minutos");
      expect(screen.queryByText(/Conferindo sozinho/)).not.toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(90_000);
      expect(verifications(mock)).toBe(1);
    });

    it("falha passageira da Riot não interrompe", async () => {
      const mock = setupPending(() => problem(503, "UPSTREAM_UNAVAILABLE"));
      await screen.findByText(/Conferindo sozinho/);
      await vi.advanceTimersByTimeAsync(60_000);
      expect(verifications(mock)).toBe(2);
      expect(screen.getByText(/Conferindo sozinho/)).toBeInTheDocument();
    });
  });
});
