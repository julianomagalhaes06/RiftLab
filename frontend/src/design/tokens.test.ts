import { describe, expect, it } from "vitest";

/** CC-903: cores só em design/tokens.css; as demais folhas usam var(--…). */
const sheets = import.meta.glob("../**/*.css", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const LITERAL = /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(/;

describe("tokens visuais", () => {
  it("lê todas as folhas de estilo do app", () => {
    expect(Object.keys(sheets).length).toBeGreaterThan(5);
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
