import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY } from "./theme";
import { ThemeToggle } from "./ThemeToggle";

afterEach(() => {
  delete document.documentElement.dataset.theme;
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("seletor de tema", () => {
  it("começa escuro, troca para o claro, guarda a escolha e volta", () => {
    render(<ThemeToggle />);
    const toggle = screen.getByRole("switch", { name: "Tema claro" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(document.documentElement.dataset.theme).toBeUndefined();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("light");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
  });

  it("abre no tema que o documento já tem", () => {
    document.documentElement.dataset.theme = "light";
    render(<ThemeToggle />);
    expect(screen.getByRole("switch", { name: "Tema claro" })).toHaveAttribute("aria-checked", "true");
  });

  it("com o armazenamento bloqueado, ainda troca o tema", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("bloqueado");
    });
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("switch", { name: "Tema claro" }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
