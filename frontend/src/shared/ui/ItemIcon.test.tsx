import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ItemVersionContext } from "../hooks/useItemName";
import { ItemIcon } from "./ItemIcon";

describe("ícones oficiais dos itens", () => {
  it("usa ID e versão fixada, sem repetir o nome acessível do item", () => {
    const { container } = render(<ItemVersionContext value="16.19.1"><ItemIcon itemId={3031} /></ItemVersionContext>);
    const image = container.querySelector("img")!;
    expect(image).toHaveAttribute("src", "https://ddragon.leagueoflegends.com/cdn/16.19.1/img/item/3031.png");
    expect(image).toHaveAttribute("alt", "");
    expect(image.parentElement).toHaveAttribute("aria-hidden", "true");
    expect(image).toHaveAttribute("width", "32");
    expect(image).toHaveAttribute("height", "32");
    expect(image).toHaveAttribute("loading", "lazy");
    expect(image).toHaveAttribute("referrerpolicy", "no-referrer");
  });

  it.each([null, "latest", "demo-datadragon-1", "16.19.1/../../evil"])("não monta URL para versão %s", (version) => {
    const { container } = render(<ItemVersionContext value={version}><ItemIcon itemId={3031} /></ItemVersionContext>);
    expect(container.querySelector("img")).toBeNull();
    expect(container.firstChild).toHaveAttribute("data-state", "fallback");
  });

  it.each([0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])("não monta URL para ID inválido %s", (itemId) => {
    const { container } = render(<ItemVersionContext value="16.19.1"><ItemIcon itemId={itemId} /></ItemVersionContext>);
    expect(container.querySelector("img")).toBeNull();
  });

  it("preserva a caixa após falha e tenta novamente quando muda o item ou patch", () => {
    const view = render(<ItemVersionContext value="16.19.1"><ItemIcon itemId={3031} /></ItemVersionContext>);
    fireEvent.error(view.container.querySelector("img")!);
    expect(view.container.querySelector("img")).toBeNull();
    expect(view.container.firstChild).toHaveClass("item-icon");
    expect(view.container.firstChild).toHaveAttribute("data-state", "fallback");
    view.rerender(<ItemVersionContext value="16.19.1"><ItemIcon itemId={3036} /></ItemVersionContext>);
    expect(view.container.querySelector("img")).toHaveAttribute("src", expect.stringContaining("3036.png"));
    fireEvent.error(view.container.querySelector("img")!);
    view.rerender(<ItemVersionContext value="16.18.1"><ItemIcon itemId={3036} /></ItemVersionContext>);
    expect(view.container.querySelector("img")).toHaveAttribute("src", expect.stringContaining("16.18.1/img/item/3036.png"));
  });
});
