import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../app/App";
import { createMockApi, json, problem } from "../../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const USER = { id: "11111111-1111-4111-8111-111111111111", email: "jogadora@example.test" };
const CODE_SENT = { status: "CODE_SENT" };
const PASSWORD = "Senha-Correta-1";

function fill(email: string, password: string) {
  fireEvent.change(screen.getByLabelText("E-mail"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Senha"), { target: { value: password } });
}

function typeCode(code: string) {
  fireEvent.change(screen.getByLabelText("Código de 6 dígitos"), { target: { value: code } });
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
    fill("jogadora@example.test", PASSWORD);
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
      if (password !== PASSWORD) return problem(401, "INVALID_CREDENTIALS");
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
    expect(await screen.findByRole("alert")).toHaveTextContent("Preencha e-mail e senha.");

    fill("jogadora@example.test", "Senha-Errada-1");
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByText("E-mail ou senha incorretos.")).toBeInTheDocument();
    expect(screen.getByLabelText("Senha")).toHaveAttribute("aria-invalid", "true");

    fill("jogadora@example.test", PASSWORD);
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("link", { name: "Minha conta (jogadora@example.test)" })).toHaveAttribute("href", "/conta");
    expect(window.location.pathname).toBe("/painel");
    // Toda chamada leva o cabeçalho anti-CSRF.
    const login = mock.callsTo("POST", /^\/api\/v1\/auth\/login$/);
    expect(login.at(-1)?.body).toEqual({ email: "jogadora@example.test", password: PASSWORD });
    const init = mock.fetch.mock.calls.find(([url]) => String(url).endsWith("/api/v1/auth/login"))?.[1];
    expect((init?.headers as Record<string, string>)["X-RiftLab-Request"]).toBe("1");

    fireEvent.click(screen.getByRole("button", { name: "Sair" }));
    // Sem sessão, o Painel vira o convite; a navegação continua.
    expect(await screen.findByText("Disponível com conta")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Áreas principais" })).toBeInTheDocument();
    expect(mock.callsTo("POST", /^\/api\/v1\/auth\/logout$/)).toHaveLength(1);
  });

  it("cria a conta, confirma o e-mail pelo código e só então abre a sessão", async () => {
    window.history.replaceState(null, "", "/entrar");
    const mock = createMockApi();
    let session: { authRequired: boolean; user: typeof USER | null } = { authRequired: true, user: null };
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, session));
    mock.on("POST", /^\/api\/v1\/auth\/register$/, () => json(202, CODE_SENT));
    mock.on("POST", /^\/api\/v1\/auth\/resend-code$/, () => json(202, CODE_SENT));
    mock.on("POST", /^\/api\/v1\/auth\/verify-email$/, ({ body }) => {
      if ((body as { code: string }).code !== "123456") return problem(422, "INVALID_CODE");
      session = { authRequired: true, user: USER };
      return json(200, session);
    });
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    fireEvent.click(await screen.findByRole("tab", { name: "Criar conta" }));
    expect(await screen.findByRole("heading", { name: "Criar conta no RiftLab" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/criar-conta");
    // Requisitos conferidos enquanto digita, com estado em texto (não só cor).
    expect(screen.getByText(/Pelo menos 8 caracteres/)).toHaveTextContent("(pendente)");
    expect(screen.getByText(/Uma letra maiúscula/)).toHaveTextContent("(pendente)");
    fill("jogadora@example.test", "Abcdef1!");
    expect(screen.getByText(/Pelo menos 8 caracteres/)).toHaveTextContent("(atendido)");
    expect(screen.getByText(/Uma letra maiúscula/)).toHaveTextContent("(atendido)");
    expect(screen.getByText(/Um símbolo/)).toHaveTextContent("(atendido)");
    fireEvent.click(screen.getByRole("button", { name: "Criar conta" }));

    const heading = await screen.findByRole("heading", { name: "Confirme seu e-mail" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByText("jogadora@example.test")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reenviar código em \d+ s/ })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Sair" })).not.toBeInTheDocument();
    expect(mock.callsTo("POST", /auth\/register$/)[0].body).toEqual({ email: "jogadora@example.test", password: "Abcdef1!" });

    const field = screen.getByLabelText("Código de 6 dígitos");
    expect(field).toHaveAttribute("autocomplete", "one-time-code");
    typeCode("12a34");
    expect(field).toHaveValue("1234"); // só dígitos
    fireEvent.click(screen.getByRole("button", { name: "Confirmar e-mail" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Digite os 6 dígitos");

    typeCode("999999");
    fireEvent.click(screen.getByRole("button", { name: "Confirmar e-mail" }));
    expect(await screen.findByText(/Código inválido ou vencido/)).toBeInTheDocument();

    typeCode("123456");
    fireEvent.click(screen.getByRole("button", { name: "Confirmar e-mail" }));
    expect(await screen.findByRole("heading", { name: "Conta criada" })).toHaveFocus();
    // A confirmação leva a senha do cadastro junto com o código.
    expect(mock.callsTo("POST", /verify-email$/).at(-1)?.body).toEqual({ email: "jogadora@example.test", code: "123456", password: "Abcdef1!" });
    fireEvent.click(screen.getByRole("button", { name: "Ir para o Painel" }));
    expect(await screen.findByRole("button", { name: "Sair" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/painel");
  });

  it("login de conta não confirmada vai para o código, reenviado, e depois entra", async () => {
    window.history.replaceState(null, "", "/entrar?next=%2Fconta");
    const mock = createMockApi();
    let session: { authRequired: boolean; user: typeof USER | null } = { authRequired: true, user: null };
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, session));
    mock.on("GET", /^\/api\/v1\/riot-link$/, () => json(200, { state: "NONE" }));
    mock.on("POST", /^\/api\/v1\/auth\/login$/, () => problem(403, "EMAIL_NOT_VERIFIED"));
    mock.on("POST", /^\/api\/v1\/auth\/resend-code$/, () => json(202, CODE_SENT));
    mock.on("POST", /^\/api\/v1\/auth\/verify-email$/, () => {
      session = { authRequired: true, user: USER };
      return json(200, session);
    });
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    await screen.findByRole("heading", { name: "Entrar no RiftLab" });
    fill("jogadora@example.test", PASSWORD);
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("heading", { name: "Confirme seu e-mail" })).toBeInTheDocument();
    await waitFor(() => expect(mock.callsTo("POST", /resend-code$/)).toHaveLength(1));
    expect(screen.getByRole("status")).toHaveTextContent("Confirme seu e-mail para entrar.");
    typeCode("123456");
    fireEvent.click(screen.getByRole("button", { name: "Confirmar e-mail" }));
    await waitFor(() => expect(window.location.pathname).toBe("/conta"));
    // Quem já tinha conta não vê a tela de boas-vindas do cadastro.
    expect(screen.queryByRole("heading", { name: "Conta criada" })).not.toBeInTheDocument();
  });

  it("esqueci minha senha: pede o código, troca a senha e volta para entrar", async () => {
    window.history.replaceState(null, "", "/entrar");
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/auth\/session$/, () => json(200, { authRequired: true, user: null }));
    mock.on("POST", /^\/api\/v1\/auth\/password\/forgot$/, () => json(202, CODE_SENT));
    let tries = 0;
    mock.on("POST", /^\/api\/v1\/auth\/password\/reset$/, () => {
      tries += 1;
      return tries === 1 ? problem(422, "INVALID_CODE") : new Response(null, { status: 204 });
    });
    vi.stubGlobal("fetch", mock.fetch);
    render(<App />);
    fireEvent.click(await screen.findByRole("link", { name: "Esqueci minha senha" }));
    expect(await screen.findByRole("heading", { name: "Redefinir a senha" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/esqueci-senha");
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("E-mail"), { target: { value: "jogadora@example.test" } });
    fireEvent.click(screen.getByRole("button", { name: "Enviar código" }));

    expect(await screen.findByRole("heading", { name: "Crie uma senha nova" })).toBeInTheDocument();
    expect(screen.getByText(/Se houver uma conta/)).toBeInTheDocument();
    typeCode("000000");
    fireEvent.change(screen.getByLabelText("Senha nova"), { target: { value: "Senha-Nova-2" } });
    expect(screen.getByText(/Um símbolo/)).toHaveTextContent("(atendido)");
    fireEvent.click(screen.getByRole("button", { name: "Salvar senha nova" }));
    expect(await screen.findByText(/Código inválido ou vencido/)).toBeInTheDocument();

    typeCode("123456");
    fireEvent.click(screen.getByRole("button", { name: "Salvar senha nova" }));
    expect(await screen.findByText("Senha alterada. Entre com a senha nova.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Entrar no RiftLab" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/entrar");
    expect(mock.callsTo("POST", /password\/reset$/).at(-1)?.body).toEqual({ email: "jogadora@example.test", code: "123456", newPassword: "Senha-Nova-2" });
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
