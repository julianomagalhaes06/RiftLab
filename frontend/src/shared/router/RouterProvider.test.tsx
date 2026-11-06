import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useRouter } from "./context";
import { openedInApp } from "./intent";
import { Link } from "./Link";
import { RouterProvider } from "./RouterProvider";

function Probe() {
  const { route, href, navigate } = useRouter();
  return (
    <div>
      <p data-testid="route">{route.name}</p>
      <p data-testid="href">{href}</p>
      <button type="button" onClick={() => navigate("/matches/DEMO-0008?participant=2")}>
        ir
      </button>
      <button type="button" onClick={() => navigate("/matches/DEMO-0008", { replace: true })}>
        trocar
      </button>
      <button type="button" onClick={() => navigate("https://evil.example/x")}>
        fora
      </button>
      <Link to="/prototipo">protótipo</Link>
      <Link to="/prototipo" target="_blank">
        nova aba
      </Link>
    </div>
  );
}

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("RouterProvider (History API)", () => {
  it("lê a URL inicial (deep link) e navega por pushState", () => {
    window.history.replaceState(null, "", "/matches/DEMO-0008?participant=1");
    render(
      <RouterProvider>
        <Probe />
      </RouterProvider>,
    );

    expect(screen.getByTestId("route")).toHaveTextContent("match");
    expect(screen.getByTestId("href")).toHaveTextContent("/matches/DEMO-0008?participant=1");

    fireEvent.click(screen.getByRole("button", { name: "ir" }));
    expect(window.location.pathname + window.location.search).toBe("/matches/DEMO-0008?participant=2");
    expect(screen.getByTestId("href")).toHaveTextContent("participant=2");
  });

  it("marca as entradas criadas pelo app; um endereço aberto de fora começa sem a marca (CC-1218)", () => {
    window.history.replaceState(null, "", "/jogador/Alvo-BR1");
    render(
      <RouterProvider>
        <Probe />
      </RouterProvider>,
    );
    expect(openedInApp()).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "ir" }));
    expect(openedInApp()).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "trocar" }));
    expect(openedInApp()).toBe(true);
    fireEvent.click(screen.getByRole("link", { name: "protótipo" }));
    expect(openedInApp()).toBe(true);
  });

  it("voltar/avançar (popstate) atualizam a rota", async () => {
    render(
      <RouterProvider>
        <Probe />
      </RouterProvider>,
    );
    fireEvent.click(screen.getByRole("link", { name: "protótipo" }));
    expect(screen.getByTestId("route")).toHaveTextContent("prototype");

    window.history.back();
    await waitFor(() => expect(screen.getByTestId("route")).toHaveTextContent("home"));

    window.history.forward();
    await waitFor(() => expect(screen.getByTestId("route")).toHaveTextContent("prototype"));
  });

  it("navigate com replace não empilha entrada no histórico", () => {
    render(
      <RouterProvider>
        <Probe />
      </RouterProvider>,
    );
    const before = window.history.length;

    fireEvent.click(screen.getByRole("button", { name: "trocar" }));

    expect(window.history.length).toBe(before);
    expect(screen.getByTestId("route")).toHaveTextContent("match");
  });

  it("nunca navega para outra origem", () => {
    render(
      <RouterProvider>
        <Probe />
      </RouterProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "fora" }));

    expect(window.location.hostname).not.toBe("evil.example");
    expect(screen.getByTestId("route")).toHaveTextContent("home");
  });

  it("Link tem href real, navega em clique simples e respeita modificadores/target", () => {
    // Observa, no document (depois do React), se o roteador cancelou o clique; e cancela
    // aqui para o jsdom não tentar navegar de verdade.
    const intercepted: boolean[] = [];
    const observe = (event: Event) => {
      intercepted.push(event.defaultPrevented);
      event.preventDefault();
    };
    document.addEventListener("click", observe);
    try {
      render(
        <RouterProvider>
          <Probe />
        </RouterProvider>,
      );
      const link = screen.getByRole("link", { name: "protótipo" });
      expect(link).toHaveAttribute("href", "/prototipo");

      fireEvent.click(link, { ctrlKey: true }); // nova aba: o roteador não intercepta
      fireEvent.click(screen.getByRole("link", { name: "nova aba" }));
      expect(intercepted).toEqual([false, false]);
      expect(screen.getByTestId("route")).toHaveTextContent("home");

      fireEvent.click(link);
      expect(intercepted[2]).toBe(true);
      expect(screen.getByTestId("route")).toHaveTextContent("prototype");
    } finally {
      document.removeEventListener("click", observe);
    }
  });
});
