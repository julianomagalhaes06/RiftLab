import { describe, expect, it } from "vitest";

/** CC-903: cores só em design/tokens.css; as demais folhas usam var(--…).
 * CC-1201: as folhas são lidas do disco. No Vitest o `?raw` de CSS chega vazio, e o teste antigo
 * passava sem verificar nada. O `fs` vem por import dinâmico porque o app não carrega os tipos do
 * Node. */
type NodeFs = {
  readdirSync(path: string, options: { recursive: true; encoding: "utf8" }): string[];
  readFileSync(path: string, encoding: "utf8"): string;
};
const fs = (await import(/* @vite-ignore */ ["node", "fs"].join(":"))) as NodeFs;
const { cwd } = (await import(/* @vite-ignore */ ["node", "process"].join(":"))) as { cwd(): string };
const SRC = `${cwd()}/src/`; // o Vitest roda em frontend/
const sheets: Record<string, string> = Object.fromEntries(
  fs.readdirSync(SRC, { recursive: true, encoding: "utf8" })
    .filter((path) => path.endsWith(".css"))
    .map((path) => [`/${path}`, fs.readFileSync(SRC + path, "utf8")]),
);
const LITERAL = /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(/;

describe("tokens visuais", () => {
  it("lê todas as folhas de estilo do app, com conteúdo", () => {
    expect(Object.keys(sheets).length).toBeGreaterThan(5);
    expect(Object.values(sheets).every((text) => text.length > 0)).toBe(true);
  });

  it("nenhuma folha de estilo fora de tokens.css declara cor literal", () => {
    const offenders = Object.entries(sheets)
      .filter(([path]) => !path.endsWith("/design/tokens.css"))
      .flatMap(([path, text]) =>
        text
          .replace(/\/\*[\s\S]*?\*\//g, "")
          .split("\n")
          .flatMap((line, index) => (LITERAL.test(line) ? [`${path}:${index + 1}`] : [])),
      );
    expect(offenders).toEqual([]);
  });

  it("todo var(--x) usado tem definição em tokens.css ou é local ao componente", () => {
    const all = Object.values(sheets).join("\n");
    const defined = new Set([...all.matchAll(/--([a-z0-9-]+):/g)].map((m) => m[1]));
    const used = new Set([...all.matchAll(/var\(--([a-z0-9-]+)/g)].map((m) => m[1]));
    expect([...used].filter((name) => !defined.has(name))).toEqual([]);
  });
});

/** CC-1201: as combinações do padrão Arena mantêm o contraste AA (texto ≥ 4,5:1; borda de
 * controle ≥ 3:1). Lê os valores direto de tokens.css. */
const tokenSheet = Object.entries(sheets).find(([path]) => path.endsWith("/design/tokens.css"))?.[1] ?? "";
const hexIn = (block: string) => Object.fromEntries([...block.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-f]{6})\b/gi)].map((m) => [m[1], m[2]]));
const blockAt = (selector: string) => {
  const start = tokenSheet.indexOf(selector);
  return start < 0 ? "" : tokenSheet.slice(start, tokenSheet.indexOf("}", start));
};
// Só tema escuro: os valores vêm do bloco :root.
const darkHex = hexIn(blockAt(":root {"));
const hex = darkHex;

function luminance(value: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(value.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(fg: string, bg: string, palette: Record<string, string> = hex): number {
  const [a, b] = [luminance(palette[fg]), luminance(palette[bg])].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}

type Pair = [fg: string, bg: string, minimum: number];

const TIERS = ["iron", "bronze", "silver", "gold", "platinum", "emerald", "diamond", "master", "grandmaster", "challenger", "none"];

describe("contraste do padrão Arena", () => {
  it.each<Pair>([
    ...["ntext", "ntext-muted", "ntext-subtle", "accent", "win", "loss"].flatMap((fg) =>
      ["n0", "n1", "n2"].map((bg): Pair => [fg, bg, 4.5])),
    ["on-accent", "accent-strong", 4.5],
    ["on-accent", "accent-fill-hover", 4.5],
    ["accent", "accent-surface", 4.5],
    ["win", "win-surface", 4.5],
    ["loss", "loss-surface", 4.5],
    ["tier-next-ink", "tier-next-top", 4.5],
    ["nline-strong", "n0", 3],
    ["nline-strong", "n1", 3],
    ["accent", "n1", 4.5],
    ["gold-text", "n1", 4.5],
    ["gold-text", "n2", 4.5],
    ["color-degraded", "color-degraded-bg", 4.5],
    ["color-loading", "color-loading-bg", 4.5],
    ["color-warning", "color-warning-bg", 4.5],
    ["team-blue", "n1", 3],
    ["team-red", "n1", 3],
    ...TIERS.flatMap((tier): Pair[] => [
      [`tier-${tier}-text`, "n1", 4.5],
      [`tier-${tier}-text`, `tier-${tier}-deep`, 4.5],
      [`tier-${tier}-text`, `tier-${tier}-dark`, 3],
      ["mark-ink", `tier-${tier}-base`, 4.5],
    ]),
  ])("%s sobre %s ≥ %s:1", (fg, bg, minimum) => {
    expect(hex[fg], fg).toBeDefined();
    expect(hex[bg], bg).toBeDefined();
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(minimum);
  });
});
