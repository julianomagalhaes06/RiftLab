import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json, problem } from "../../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const USER = { id: "11111111-1111-4111-8111-111111111111", username: "jogadora" };
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

    fireEvent.click(screen.getByRole("button", { name: "Verificar ícone" }));
    expect(await screen.findByText(/ainda não é o pedido/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Verificar ícone" }));
    expect(await screen.findByRole("heading", { name: "Conta Riot vinculada" })).toBeInTheDocument();
    expect(screen.getByText("Jogadora#BR1")).toBeInTheDocument();

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
});
