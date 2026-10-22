import { afterEach, describe, expect, it, vi } from "vitest";
import type { PlayerProfile } from "../../shared/api/client";
import { clearRecentPlayers, matchRecentPlayers, readRecentPlayers, rememberPlayer, tagSuggestion } from "./recentPlayers";

function profile(gameName: string, tagLine = "BR1", overrides: Partial<PlayerProfile> = {}): PlayerProfile {
  return {
    id: "7a5c3f1e-0000-4000-8000-000000000001",
    gameName,
    tagLine,
    profileIconId: 4567,
    summonerLevel: 321,
    refreshedAt: "2026-10-19T15:00:00Z",
    dataDragonVersion: "16.20.1",
    solo: { queue: "RANKED_SOLO_5x5", state: "READY", tier: "GOLD", division: "II", leaguePoints: 41, observedAt: "2026-10-19T15:00:00Z" },
    flex: { queue: "RANKED_FLEX_SR", state: "READY", tier: "PLATINUM", division: "III", leaguePoints: 78, observedAt: "2026-10-19T15:00:00Z" },
    staleReason: null,
    ...overrides,
  } as PlayerProfile;
}

afterEach(() => vi.restoreAllMocks());

describe("buscas recentes de jogador (CC-1208)", () => {
  it("guarda o perfil aberto no topo, sem repetir o mesmo Riot ID, com o melhor elo", () => {
    rememberPlayer(profile("Fate Sealed"));
    rememberPlayer(profile("Outro Jogador", "XYZ"));
    rememberPlayer(profile("FATE SEALED", "br1"));
    const recent = readRecentPlayers();
    expect(recent.map((p) => `${p.gameName}#${p.tagLine}`)).toEqual(["FATE SEALED#br1", "Outro Jogador#XYZ"]);
    expect(recent[0].rank).toEqual({ tier: "PLATINUM", division: "III", leaguePoints: 78 });
    expect(JSON.stringify(recent)).not.toMatch(/puuid|7a5c3f1e/i); // só o que a tela mostra
  });

  it("guarda no máximo oito perfis", () => {
    for (let index = 0; index < 10; index += 1) rememberPlayer(profile(`Jogador ${index}`));
    expect(readRecentPlayers()).toHaveLength(8);
    expect(readRecentPlayers()[0].gameName).toBe("Jogador 9");
  });

  it("sugere por parte do Nome#TAG, sem diferenciar maiúsculas", () => {
    rememberPlayer(profile("Fate Sealed"));
    rememberPlayer(profile("Fateful", "AAA"));
    rememberPlayer(profile("Outro"));
    const recent = readRecentPlayers();
    expect(matchRecentPlayers("fat", recent).map((p) => p.gameName)).toEqual(["Fateful", "Fate Sealed"]);
    expect(matchRecentPlayers("#aaa", recent).map((p) => p.gameName)).toEqual(["Fateful"]);
    expect(matchRecentPlayers("", recent)).toHaveLength(3);
  });

  it("limpar apaga tudo; conteúdo corrompido ou armazenamento bloqueado não quebra a busca", () => {
    rememberPlayer(profile("Fate Sealed"));
    clearRecentPlayers();
    expect(readRecentPlayers()).toEqual([]);
    window.localStorage.setItem("riftlab:recent-players:v1", "{não é json");
    expect(readRecentPlayers()).toEqual([]);
    window.localStorage.setItem("riftlab:recent-players:v1", JSON.stringify([{ gameName: "a#b", tagLine: "X" }, 7]));
    expect(readRecentPlayers()).toEqual([]);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("bloqueado"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("bloqueado"); });
    expect(readRecentPlayers()).toEqual([]);
    expect(() => rememberPlayer(profile("Fate Sealed"))).not.toThrow();
  });

  it("sem # sugere a TAG BR1; com # ou nome inválido, nada", () => {
    expect(tagSuggestion(" Fate Sealed ")).toEqual({ gameName: "Fate Sealed", tagLine: "BR1" });
    expect(tagSuggestion("Fate#BR1")).toBeNull();
    expect(tagSuggestion("")).toBeNull();
    expect(tagSuggestion("x".repeat(17))).toBeNull();
  });
});
