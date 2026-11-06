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

describe("Contas locais: leitura pública e conta para guardar", () => {
  it("sem conta, abre o início com a busca e oferece entrar ou criar conta", async () => {
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, { authRequired: true, user: null }));
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);

    expect(await screen.findByRole("heading", { name: /Entenda cada compra/ })).toBeInTheDocument();
    expect(screen.getByRole("search", { name: "Buscar jogador" })).toHaveClass("player-search--hero");
    // Só a busca da vitrine: a do topo some no início.
    expect(screen.getAllByRole("search", { name: "Buscar jogador" })).toHaveLength(1);
    const nav = screen.getByRole("navigation", { name: "Áreas principais" });
    expect(nav).toHaveTextContent("Início");
    expect(nav).toHaveTextContent("Campeões");
    expect(nav).not.toHaveTextContent("Painel");
    expect(screen.getAllByRole("link", { name: "Entrar" })[0]).toHaveAttribute("href", "/entrar");
    expect(screen.getAllByRole("link", { name: "Criar conta" })[0]).toHaveAttribute("href", "/criar-conta");
    // Nenhuma chamada da conta própria sem sessão.
    expect(mock.callsTo("GET", /^\/api\/v1\/players\/me$/)).toHaveLength(0);
    expect(mock.callsTo("GET", /^\/api\/v1\/dashboard$/)).toHaveLength(0);
  });

  it("área da conta sem sessão mostra o convite e volta para ela depois de entrar", async () => {
    window.history.replaceState(null, "", "/historico");
    const mock = createMockApi();
    let session: { authRequired: boolean; user: typeof USER | null } = { authRequired: true, user: null };
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, session));
    mock.on("POST", /^\/api\/v1\/auth\/login$/, () => {
      session = { authRequired: true, user: USER };
      return json(200, session);
    });
    mock.on("GET", /^\/api\/v1\/matches$/, () => json(200, { items: [], nextCursor: null }));
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Histórico" })).toBeInTheDocument();
    expect(screen.getByText("Disponível com conta")).toBeInTheDocument();
    expect(mock.callsTo("GET", /^\/api\/v1\/matches$/)).toHaveLength(0);
    const enter = screen.getAllByRole("link", { name: "Entrar" }).find((link) => link.closest(".account-gate"));
    expect(enter).toHaveAttribute("href", "/entrar?next=%2Fhistorico");
    fireEvent.click(enter!);

    expect(await screen.findByRole("heading", { name: "Entrar no RiftLab" })).toBeInTheDocument();
    fill("jogadora", "senha-correta-1");
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("heading", { name: "Partidas para estudar" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/historico");
  });

  it("entra com a conta, mostra o erro de senha e sai sem perder a navegação", async () => {
    window.history.replaceState(null, "", "/entrar");
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
    expect(screen.getAllByRole("link", { name: "Continuar sem conta" })[0]).toHaveAttribute("href", "/");
    expect(screen.queryByText(/visitante/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Preencha usuário e senha.");

    fill("jogadora", "senha-errada-1");
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByText("Usuário ou senha incorretos.")).toBeInTheDocument();
    expect(screen.getByLabelText("Senha")).toHaveAttribute("aria-invalid", "true");

    fill("jogadora", "senha-correta-1");
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("link", { name: "Minha conta (jogadora)" })).toHaveAttribute("href", "/conta");
    expect(window.location.pathname).toBe("/painel");
    // Toda chamada leva o cabeçalho anti-CSRF.
    const login = mock.callsTo("POST", /^\/api\/v1\/auth\/login$/);
    expect(login.at(-1)?.body).toEqual({ username: "jogadora", password: "senha-correta-1" });
    const init = mock.fetch.mock.calls.find(([url]) => String(url).endsWith("/api/v1/auth/login"))?.[1];
    expect((init?.headers as Record<string, string>)["X-RiftLab-Request"]).toBe("1");

    fireEvent.click(screen.getByRole("button", { name: "Sair" }));
    // Sem sessão, o Painel vira o convite; a navegação continua.
    expect(await screen.findByText("Disponível com conta")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Áreas principais" })).toBeInTheDocument();
    expect(mock.callsTo("POST", /^\/api\/v1\/auth\/logout$/)).toHaveLength(1);
  });

  it("cria conta pela mesma tela e explica nome em uso", async () => {
    window.history.replaceState(null, "", "/entrar");
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, { authRequired: true, user: null }));
    let attempts = 0;
    mock.on("POST", /^\/api\/v1\/auth\/register$/, () => {
      attempts += 1;
      return attempts === 1 ? problem(409, "USERNAME_TAKEN") : json(201, { authRequired: true, user: USER });
    });
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    fireEvent.click(await screen.findByRole("tab", { name: "Criar conta" }));
    expect(await screen.findByRole("heading", { name: "Criar conta no RiftLab" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Criar conta" })).toHaveAttribute("aria-selected", "true");
    expect(window.location.pathname).toBe("/criar-conta");
    // Requisitos conferidos enquanto digita, com estado em texto (não só cor).
    expect(screen.getByText(/Senha com pelo menos 10 caracteres/)).toHaveTextContent("(pendente)");
    fill("jogadora", "senha-correta-1");
    fireEvent.click(screen.getByRole("button", { name: "Criar conta" }));
    expect(await screen.findByText("Esse nome de usuário já está em uso. Escolha outro.")).toBeInTheDocument();
    expect(screen.getByText(/Senha com pelo menos 10 caracteres/)).toHaveTextContent("(atendido)");
    fireEvent.click(screen.getByRole("button", { name: "Criar conta" }));
    // Depois do cadastro: confirmação com o próximo passo (vincular a conta Riot ou ir ao Painel).
    expect(await screen.findByRole("heading", { name: "Conta criada" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Vincular conta Riot" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ir para o Painel" }));
    expect(await screen.findByRole("button", { name: "Sair" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/painel");
  });

  it("sessão que termina no meio do uso troca a área da conta pelo convite", async () => {
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
    expect(await screen.findByText("Disponível com conta")).toBeInTheDocument();
    await waitFor(() => expect(mock.callsTo("GET", /^\/api\/v1\/auth\/session$/).length).toBeGreaterThan(1));
  });

  it("sem exigência de login, a interface abre direto e sem menu de usuário", async () => {
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, { authRequired: false, user: null }));
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    await waitFor(() => expect(mock.callsTo("GET", /^\/api\/v1\/auth\/session$/)).toHaveLength(1));
    expect(await screen.findByRole("heading", { name: /Entenda cada compra/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Entrar" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sair" })).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Áreas principais" })).toHaveTextContent("Painel");
  });
});

describe("Tela de entrada: abas e senha", () => {
  it("alterna as abas pelo teclado e mostra ou oculta a senha", async () => {
    window.history.replaceState(null, "", "/entrar");
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, { authRequired: true, user: null }));
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    const login = await screen.findByRole("tab", { name: "Entrar" });
    login.focus();
    fireEvent.keyDown(login, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Criar conta" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Criar conta" })).toHaveAttribute("aria-selected", "true");
    const password = screen.getByLabelText("Senha");
    expect(password).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByRole("button", { name: "Mostrar senha" }));
    expect(password).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Ocultar senha" })).toHaveAttribute("aria-pressed", "true");
  });
});
