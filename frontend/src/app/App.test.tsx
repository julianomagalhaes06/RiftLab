import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMockApi, json as jsonResponse } from "../test/mockApi";
import App from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

/** Além do health, o Painel e o Histórico consultam GET /matches. */
function stubApi(configure?: (api: ReturnType<typeof createMockApi>) => void) {
  const api = createMockApi();
  api.on("GET", /^\/api\/v1\/matches$/, () => jsonResponse(200, { items: [], nextCursor: null }));
  configure?.(api);
  vi.stubGlobal("fetch", api.fetch);
  return api;
}

describe("App: importar partida", () => {
  beforeEach(() => window.history.replaceState(null, "", "/importar"));

  it("mostra RiftLab, a demonstração e a faixa DEMO antes do health resolver", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    render(<App />);

    expect(screen.getByRole("heading", { name: "RiftLab" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Explorar demonstração" })).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("Partidas DEMO são sintéticas");
    expect(screen.getByRole("status")).toHaveTextContent("Verificando disponibilidade");
    // A lista crua de partidas saiu da entrada: elas ficam no Histórico.
    expect(screen.queryByRole("heading", { name: "Partidas importadas" })).not.toBeInTheDocument();
  });

  it("mostra serviço disponível quando live e ready respondem 200", async () => {
    stubApi();

    render(<App />);

    expect(await screen.findByText("Serviço disponível")).toBeInTheDocument();
  });

  it("mostra indisponibilidade quando a API nao responde", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network error")));

    render(<App />);

    expect(await screen.findByText("Serviço indisponível")).toBeInTheDocument();
    expect(screen.getByText(/API do RiftLab não respondeu/i)).toBeInTheDocument();
  });
});

describe("App: entrada", () => {
  it("abre o Painel em / sem a faixa DEMO quando não há dados de demonstração", async () => {
    stubApi((api) => {
      api.on("GET", /^\/api\/v1\/dashboard$/, () => jsonResponse(503, {}));
      api.on("GET", /^\/api\/v1\/ranked\/me$/, () => jsonResponse(503, {}));
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Entenda a partida. Evolua a decisão." })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Painel/, current: "page" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Importar partida" })).toHaveAttribute("href", "/importar");
    expect(screen.queryByText(/Partidas DEMO são sintéticas/)).not.toBeInTheDocument();
  });
});
