import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json, problem } from "../../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const USER = { id: "11111111-1111-4111-8111-111111111111", username: "jogadora" };

function fill(username: string, password: string) {
  fireEvent.change(screen.getByLabelText("Usuário"), { target: { value: username } });
  fireEvent.change(screen.getByLabelText("Senha"), { target: { value: password } });
}

describe("Contas locais (Sprint 2, #10/#14)", () => {
  it("exige login quando o servidor pede, mostra o erro e entra com a conta", async () => {
    const mock = createMockApi();
    let session: { authRequired: boolean; user: typeof USER | null } = { authRequired: true, user: null };
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, session));
    mock.on("POST", /^\/api\/v1\/auth\/login$/, ({ body }) => {
      const { password } = body as { password: string };
      if (password !== "senha-correta-1") return problem(401, "INVALID_CREDENTIALS");
      session = { authRequired: true, user: USER };
      return json(200, session);
    });
    mock.on("POST", /^\/api\/v1\/auth\/logout$/, () => {
      session = { authRequired: true, user: null };
      return new Response(null, { status: 204 });
    });
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Entrar no RiftLab" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Áreas principais" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Preencha usuário e senha.");

    fill("jogadora", "senha-errada-1");
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByText("Usuário ou senha incorretos.")).toBeInTheDocument();
    expect(screen.getByLabelText("Senha")).toHaveAttribute("aria-invalid", "true");

    fill("jogadora", "senha-correta-1");
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByText("jogadora")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Entrar no RiftLab" })).not.toBeInTheDocument();
    // Toda chamada leva o cabeçalho anti-CSRF.
    const login = mock.callsTo("POST", /^\/api\/v1\/auth\/login$/);
    expect(login.at(-1)?.body).toEqual({ username: "jogadora", password: "senha-correta-1" });
    const init = mock.fetch.mock.calls.find(([url]) => String(url).endsWith("/api/v1/auth/login"))?.[1];
    expect((init?.headers as Record<string, string>)["X-RiftLab-Request"]).toBe("1");

    fireEvent.click(screen.getByRole("button", { name: "Sair" }));
    expect(await screen.findByRole("heading", { name: "Entrar no RiftLab" })).toBeInTheDocument();
    expect(mock.callsTo("POST", /^\/api\/v1\/auth\/logout$/)).toHaveLength(1);
  });

  it("cria conta pela mesma tela e explica nome em uso", async () => {
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, { authRequired: true, user: null }));
    let attempts = 0;
    mock.on("POST", /^\/api\/v1\/auth\/register$/, () => {
      attempts += 1;
      return attempts === 1 ? problem(409, "USERNAME_TAKEN") : json(201, { authRequired: true, user: USER });
    });
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Criar conta" }));
    expect(screen.getByRole("heading", { name: "Criar conta no RiftLab" })).toHaveFocus();
    expect(screen.getByText(/senha com pelo menos 10/)).toBeInTheDocument();
    fill("jogadora", "senha-correta-1");
    fireEvent.click(screen.getByRole("button", { name: "Criar conta" }));
    expect(await screen.findByText("Esse nome de usuário já está em uso. Escolha outro.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Criar conta" }));
    expect(await screen.findByRole("button", { name: "Sair" })).toBeInTheDocument();
  });

  it("volta à tela de login quando uma chamada responde AUTH_REQUIRED", async () => {
    const mock = createMockApi();
    let session: { authRequired: boolean; user: typeof USER | null } = { authRequired: true, user: USER };
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, session));
    mock.on("GET", /^\/api\/v1\/matches$/, () => {
      session = { authRequired: true, user: null };
      return problem(401, "AUTH_REQUIRED");
    });
    vi.stubGlobal("fetch", mock.fetch);
    window.history.replaceState(null, "", "/historico");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Entrar no RiftLab" })).toBeInTheDocument();
    await waitFor(() => expect(mock.callsTo("GET", /^\/api\/v1\/auth\/session$/).length).toBeGreaterThan(1));
  });

  it("sem exigência de login, a interface abre direto e sem menu de usuário", async () => {
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, { authRequired: false, user: null }));
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    await waitFor(() => expect(mock.callsTo("GET", /^\/api\/v1\/auth\/session$/)).toHaveLength(1));
    expect(screen.queryByRole("heading", { name: "Entrar no RiftLab" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sair" })).not.toBeInTheDocument();
  });
});
