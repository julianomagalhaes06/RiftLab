import { describe, expect, it } from "vitest";
import { SPLASHES, pickSplash, splashUrl } from "./splashes";

describe("splash arts do início", () => {
  it("cada skin é única e aponta para o Data Dragon", () => {
    const keys = SPLASHES.map((s) => `${s.key}_${s.num}`);
    expect(new Set(keys).size).toBe(SPLASHES.length);
    for (const splash of SPLASHES) {
      expect(splash.num).toBeGreaterThan(0); // nunca a arte padrão
      expect(splashUrl(splash)).toBe(`https://ddragon.leagueoflegends.com/cdn/img/champion/splash/${splash.key}_${splash.num}.jpg`);
      expect(splash.skin).toContain(splash.champion.split(" ")[0]);
    }
  });

  it("o sorteio cobre a lista inteira, das pontas ao meio", () => {
    expect(pickSplash(() => 0)).toBe(SPLASHES[0]);
    expect(pickSplash(() => 0.999999)).toBe(SPLASHES[SPLASHES.length - 1]);
    expect(pickSplash(() => 1)).toBe(SPLASHES[SPLASHES.length - 1]);
    expect(pickSplash(() => 0.5)).toBe(SPLASHES[Math.floor(SPLASHES.length / 2)]);
  });
});
