import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json, problem } from "../../test/mockApi";
import { backstageFixture } from "./backstageFixture";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const ADMIN = { id: "u-admin", email: "admin@example.test", name: "Admin", isAdmin: true };
const USER = { ...ADMIN, id: "u-comum", email: "comum@example.test", isAdmin: false };

function session(user: typeof ADMIN) {
  return () => json(200, { authRequired: true, user, demoEnabled: false, socialProviders: [] });
}

describe("Bastidores", () => {
  it("mostra ao administrador as chamadas à Riot, a fila e o acervo", async () => {
    window.history.replaceState(null, "", "/bastidores");
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/auth\/session$/, session(ADMIN));
    api.on("GET", /^\/api\/v1\/admin\/backstage$/, () => json(200, backstageFixture()));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "O que o RiftLab está fazendo agora" })).toBeInTheDocument();
    expect(screen.getByText("Aceita")).toBeInTheDocument();
    const live = screen.getByRole("region", { name: /Chamadas à API da Riot, ao vivo/ });
    const rows = within(live).getAllByRole("row");
    expect(rows).toHaveLength(6); // cabeçalho + 5 chamadas
    expect(rows[1]).toHaveTextContent("Match-V5");
    expect(rows[1]).toHaveTextContent("linha do tempo");
    expect(rows[1]).toHaveTextContent("200 OK");
    expect(rows[4]).toHaveTextContent("404 não encontrado");
    expect(screen.getByText("523.811")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Bastidores/ })).toBeInTheDocument();
  });

  it("avisa quando a Riot recusou a chave", async () => {
    window.history.replaceState(null, "", "/bastidores");
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/auth\/session$/, session(ADMIN));
    api.on("GET", /^\/api\/v1\/admin\/backstage$/, () =>
      json(200, backstageFixture({ riotKey: { status: "REJECTED", checkedAt: new Date().toISOString() } })),
    );
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);
    expect(await screen.findByText("Recusada")).toBeInTheDocument();
    expect(screen.getByText(/expira em 24 h/)).toBeInTheDocument();
  });

  it("para outras contas a página não existe e o menu não mostra a área", async () => {
    window.history.replaceState(null, "", "/bastidores");
    const api = createMockApi();
    api.on("GET", /^\/api\/v1\/auth\/session$/, session(USER));
    api.on("GET", /^\/api\/v1\/admin\/backstage$/, () => problem(404, "RESOURCE_NOT_FOUND"));
    vi.stubGlobal("fetch", api.fetch);
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Página não encontrada" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Bastidores/ })).not.toBeInTheDocument();
  });
});
