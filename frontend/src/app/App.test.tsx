import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("mostra RiftLab e identifica o modo demonstração antes do health resolver", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    render(<App />);

    expect(screen.getByRole("heading", { name: "RiftLab" })).toBeInTheDocument();
    expect(screen.getByText(/modo demonstração/i)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Verificando disponibilidade");
  });

  it("mostra serviço disponível quando live e ready respondem 200", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse(200, { status: "UP" }))
        .mockResolvedValueOnce(jsonResponse(200, { status: "UP", database: "UP" })),
    );

    render(<App />);

    expect(await screen.findByText("Serviço disponível")).toBeInTheDocument();
  });

  it("mostra indisponibilidade quando a API nao responde", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network error")));

    render(<App />);

    expect(await screen.findByText("Serviço indisponível")).toBeInTheDocument();
    expect(screen.getByText(/API do RiftLab nao respondeu/i)).toBeInTheDocument();
  });
});
