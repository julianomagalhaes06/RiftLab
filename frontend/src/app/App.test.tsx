import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMockApi, json as jsonResponse } from "../test/mockApi";
import App from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

/** A entrada agora também lista as partidas importadas: além do health, há GET /matches. */
function stubApi(configure?: (api: ReturnType<typeof createMockApi>) => void) {
  const api = createMockApi();
  api.on("GET", /^\/api\/v1\/matches$/, () => jsonResponse(200, { items: [], nextCursor: null }));
  configure?.(api);
  vi.stubGlobal("fetch", api.fetch);
  return api;
}

describe("App", () => {
  it("mostra RiftLab e identifica o modo demonstração antes do health resolver", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    render(<App />);

    expect(screen.getByRole("heading", { name: "RiftLab" })).toBeInTheDocument();
    expect(screen.getByText(/modo demonstração/i)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Verificando disponibilidade");
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
