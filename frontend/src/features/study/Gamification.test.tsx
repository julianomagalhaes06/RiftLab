import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DecisionSummary, LearningGamification } from "../../shared/api/client";
import { RouterProvider } from "../../shared/router/RouterProvider";
import { createMockApi, json } from "../../test/mockApi";
import { AchievementGrid, LevelChip, MatchStudyBar, MissionList, StreakBadge, XpBar } from "./Gamification";

afterEach(() => vi.unstubAllGlobals());

const GAME: LearningGamification = {
  xp: 310, level: 3, levelXp: 60, levelTarget: 200, studiedDecisions: 7,
  streak: { current: 2, best: 4, studiedToday: false },
  achievements: [
    { key: "first_study", title: "Primeira decisão", description: "Estude a primeira.", progress: 1, target: 1, unlocked: true },
    { key: "study_10", title: "Estudioso", description: "Estude 10.", progress: 7, target: 10, unlocked: false },
  ],
  missions: [
    { key: "study_3", title: "Estude 3 decisões", description: "Três nesta semana.", progress: 3, target: 3, completed: true },
    { key: "loss_1", title: "Estude uma derrota", description: "Uma derrota.", progress: 0, target: 1, completed: false },
  ],
  weekStart: "2026-11-09",
  match: { matchId: "BR1_1", studiedDecisionIds: ["11111111-1111-4111-8111-111111111111"], xp: 55 },
};

describe("gamificação do estudo", () => {
  it("mostra nível, XP, sequência com aviso, missões e conquistas com progresso", () => {
    render(<RouterProvider><XpBar game={GAME} /><StreakBadge game={GAME} /><MissionList missions={GAME.missions} /><AchievementGrid achievements={GAME.achievements} /></RouterProvider>);
    expect(screen.getByText("Nível 3")).toBeInTheDocument();
    expect(screen.getByText("60 / 200 XP")).toBeInTheDocument();
    expect(screen.getByText(/Estude hoje para não perder\. Recorde: 4/)).toBeInTheDocument();
    const missions = screen.getByRole("list", { name: "Missões da semana" });
    expect(within(missions).getByText("Concluída")).toBeInTheDocument();
    expect(within(missions).getByText("0/1")).toBeInTheDocument();
    const achievements = screen.getByRole("list", { name: "Conquistas" });
    expect(within(achievements).getByText("Conquistada")).toBeInTheDocument();
    expect(within(achievements).getByText("7/10")).toBeInTheDocument();
  });

  it("selo do topo descreve nível, XP e sequência e leva a Minha evolução", async () => {
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/learning-attempts\/gamification$/, () => json(200, GAME));
    vi.stubGlobal("fetch", mock.fetch);
    render(<RouterProvider><LevelChip /></RouterProvider>);
    const chip = await screen.findByRole("link", { name: /Nível 3, 60 de 200 XP para o próximo; sequência de 2 dias/ });
    expect(chip).toHaveAttribute("href", "/estudo/evolucao");
  });

  it("barra da partida conta só as decisões analisáveis já estudadas deste participante", async () => {
    const mock = createMockApi();
    mock.on("GET", /^\/api\/v1\/learning-attempts\/gamification$/, () => json(200, GAME));
    vi.stubGlobal("fetch", mock.fetch);
    const decisions = [
      { decisionId: "11111111-1111-4111-8111-111111111111", eligible: true },
      { decisionId: "22222222-2222-4222-8222-222222222222", eligible: true },
      { decisionId: "33333333-3333-4333-8333-333333333333", eligible: false },
    ] as unknown as DecisionSummary[];
    render(<RouterProvider><MatchStudyBar matchId="BR1_1" decisions={decisions} /></RouterProvider>);
    const bar = await screen.findByRole("region", { name: "Seu estudo desta partida" });
    expect(bar).toHaveTextContent("Partida estudada: 1 de 2 decisões analisáveis · +55 XP");
    expect(mock.callsTo("GET", /gamification$/)[0].search.get("matchId")).toBe("BR1_1");
  });
});
