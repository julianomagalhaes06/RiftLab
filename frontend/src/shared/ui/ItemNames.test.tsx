import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ItemNames } from "./ItemNames";
import { useItemName } from "../hooks/useItemName";

function Inventory() {
  const name = useItemName();
  return <><span>{name({ itemId: 3031, name: "Infinity Edge" })}</span><span>{name({ itemId: 3340 })}</span></>;
}

afterEach(() => vi.unstubAllGlobals());

describe("nomes de itens em português", () => {
  it("usa o patch exato, traduz por ID e compartilha a consulta entre inventários", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      version: "16.19.1", data: { "3031": { name: "Gume do Infinito" }, "3340": { name: "Sentinela Invisível" } },
    })));
    vi.stubGlobal("fetch", fetch);
    render(<><ItemNames version="16.19.1"><Inventory /></ItemNames><ItemNames version="16.19.1"><Inventory /></ItemNames></>);
    expect(await screen.findAllByText("Gume do Infinito")).toHaveLength(2);
    expect(screen.getAllByText("Sentinela Invisível")).toHaveLength(2);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe("https://ddragon.leagueoflegends.com/cdn/16.19.1/data/pt_BR/item.json");
    expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: "omit", referrerPolicy: "no-referrer" });
  });

  it("não reaproveita nomes de outro patch ao navegar", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      version: "16.18.1", data: { "3031": { name: "Gume do Infinito" } },
    }))));
    const view = render(<ItemNames version="16.18.1"><Inventory /></ItemNames>);
    await screen.findByText("Gume do Infinito");
    view.rerender(<ItemNames version={null}><Inventory /></ItemNames>);
    expect(screen.getByText("Infinity Edge")).toBeInTheDocument();
    expect(screen.queryByText("Gume do Infinito")).not.toBeInTheDocument();
  });

  it.each([null, "latest", "../../16.19.1"])("sem versão válida (%s) não consulta rede nem inventa tradução", (version) => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    render(<ItemNames version={version}><Inventory /></ItemNames>);
    expect(screen.getByText("Infinity Edge")).toBeInTheDocument();
    expect(screen.getByText("Item #3340, nome indisponível")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["http", "network", "version", "schema"])("falha %s mantém nome original e informa o limite", async (failure) => {
    const fetch = vi.fn().mockImplementation(() => {
      if (failure === "network") return Promise.reject(new TypeError("offline"));
      return Promise.resolve(new Response(JSON.stringify(failure === "schema" ? {} : {
        version: failure === "version" ? "16.17.1" : "16.16.1", data: { "3031": { name: "Nome indevido" } },
      }), { status: failure === "http" ? 404 : 200 }));
    });
    vi.stubGlobal("fetch", fetch);
    render(<ItemNames version="16.16.1"><Inventory /></ItemNames>);
    await screen.findByText(/Tradução dos itens indisponível/);
    expect(screen.getByText("Infinity Edge")).toBeInTheDocument();
    expect(screen.queryByText("Nome indevido")).not.toBeInTheDocument();
  });

  it("não atualiza componentes desmontados quando a resposta chega", async () => {
    let resolve!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
    const view = render(<ItemNames version="16.15.1"><Inventory /></ItemNames>);
    view.unmount();
    resolve(new Response(JSON.stringify({ version: "16.15.1", data: {} })));
    await waitFor(() => expect(screen.queryByText("Infinity Edge")).not.toBeInTheDocument());
  });
});
