import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChampionBadge } from "./ChampionBadge";
import { championName, championPortraitUrl, participantLabel } from "./format";

const KAISA = { name: "Kai'Sa", dataDragonVersion: "16.17.1", imageFile: "Kaisa.png" };

describe("championPortraitUrl (CC-208): só o host oficial, versão fixada e arquivo validado", () => {
  it("monta o retrato quadrado da versão fixada", () => {
    expect(championPortraitUrl(KAISA)).toBe(
      "https://ddragon.leagueoflegends.com/cdn/16.17.1/img/champion/Kaisa.png",
    );
  });

  it.each([
    { ...KAISA, imageFile: "../Kaisa.png" },
    { ...KAISA, imageFile: "https://evil.example/Kaisa.png" },
    { ...KAISA, imageFile: "Kaisa.svg" },
    { ...KAISA, dataDragonVersion: "latest" },
    { ...KAISA, dataDragonVersion: "16.17.1/../x" },
  ])("recusa caminho, URL ou versão não fixada: %o", (champion) => {
    expect(championPortraitUrl(champion)).toBeNull();
  });

  it("sem campeão resolvido não há URL e o rótulo é numérico", () => {
    expect(championPortraitUrl(null)).toBeNull();
    expect(championName({ championId: 9001, champion: null })).toBe("Campeão #9001");
    expect(participantLabel({ participantId: 2, championId: 145, team: 100, champion: KAISA })).toBe(
      "Participante 2 — Kai'Sa",
    );
  });
});

describe("ChampionBadge", () => {
  it("mostra retrato decorativo (alt vazio) e o nome como texto acessível", () => {
    render(<ChampionBadge participant={{ championId: 145, champion: KAISA }} />);
    const img = screen.getByRole("presentation", { hidden: true });
    expect(img).toHaveAttribute("src", "https://ddragon.leagueoflegends.com/cdn/16.17.1/img/champion/Kaisa.png");
    expect(img).toHaveAttribute("alt", "");
    expect(img).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(screen.getByText("Kai'Sa")).toBeInTheDocument();
  });

  it("DEMO ou campeão ausente: rótulo numérico, sem imagem, caixa reservada", () => {
    render(<ChampionBadge participant={{ championId: 9001, champion: null }} />);
    expect(screen.getByText("Campeão #9001")).toBeInTheDocument();
    expect(screen.queryByRole("presentation", { hidden: true })).toBeNull();
    const box = screen.getByTestId("champion-portrait");
    expect(box).toHaveAttribute("data-state", "fallback");
    expect(box).toHaveStyle({ width: "32px", height: "32px" });
  });

  it("imagem quebrada vira apresentação sem imagem, mantendo nome e tamanho da caixa", () => {
    render(<ChampionBadge participant={{ championId: 145, champion: KAISA }} size={40} />);
    fireEvent.error(screen.getByRole("presentation", { hidden: true }));
    expect(screen.queryByRole("presentation", { hidden: true })).toBeNull();
    const box = screen.getByTestId("champion-portrait");
    expect(box).toHaveAttribute("data-state", "fallback");
    expect(box).toHaveStyle({ width: "40px", height: "40px" });
    expect(screen.getByText("Kai'Sa")).toBeInTheDocument();
  });
});
