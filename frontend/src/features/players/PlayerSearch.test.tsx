import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouterProvider } from "../../shared/router/RouterProvider";
import { PlayerSearch } from "./PlayerSearch";

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("PlayerSearch", () => {
  it("mostra o aviso de Riot ID inválido e inicia a saída ao sair da busca (não fica preso ao navegar)", async () => {
    render(
      <RouterProvider>
        <PlayerSearch />
        <button type="button">Outra aba</button>
      </RouterProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    const hint = screen.getByRole("alert");
    expect(hint).toHaveTextContent(/Digite o nome do jogador/);
    expect(hint).not.toHaveClass("player-search__hint--leaving");

    const outside = screen.getByRole("button", { name: "Outra aba" });
    fireEvent.blur(screen.getByRole("search", { name: "Buscar jogador" }), { relatedTarget: outside });
    // Sai com uma animação curta (player-search-hint-out): ainda no documento logo após sair,
    // só com a classe que inicia a transição — não some na hora.
    expect(screen.getByRole("alert")).toHaveClass("player-search__hint--leaving");
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("digitar de novo também inicia a saída do aviso, sem precisar sair do campo", async () => {
    render(
      <RouterProvider>
        <PlayerSearch />
      </RouterProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    expect(screen.getByRole("alert")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Buscar jogador pelo Riot ID" }), {
      target: { value: "Ana" },
    });
    expect(screen.getByRole("alert")).toHaveClass("player-search__hint--leaving");
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("uma nova busca inválida cancela a saída em andamento, sem esperar o aviso antigo sumir", () => {
    render(
      <RouterProvider>
        <PlayerSearch />
      </RouterProvider>,
    );
    const input = screen.getByRole("combobox", { name: "Buscar jogador pelo Riot ID" });
    const submit = screen.getByRole("button", { name: "Buscar" });
    fireEvent.click(submit);
    fireEvent.change(input, { target: { value: "Ana" } }); // começa a sair
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(submit); // inválida de novo, antes da saída anterior terminar
    expect(screen.getByRole("alert")).not.toHaveClass("player-search__hint--leaving");
  });
});
