import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouterProvider } from "../../shared/router/RouterProvider";
import { PlayerSearch } from "./PlayerSearch";

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("PlayerSearch", () => {
  it("mostra o aviso de Riot ID inválido e some ao sair da busca (não fica preso ao navegar)", () => {
    render(
      <RouterProvider>
        <PlayerSearch />
        <button type="button">Outra aba</button>
      </RouterProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    const hint = screen.getByRole("alert");
    expect(hint).toHaveTextContent(/Digite o nome do jogador/);

    const outside = screen.getByRole("button", { name: "Outra aba" });
    fireEvent.blur(screen.getByRole("search", { name: "Buscar jogador" }), { relatedTarget: outside });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("digitar de novo também limpa o aviso, sem precisar sair do campo", () => {
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
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
