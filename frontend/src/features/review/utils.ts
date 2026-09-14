import type { Budget } from "./types";

export function describeBudget(budget: Budget): string {
  if (budget.source === "UNKNOWN") {
    return "Ouro desconhecido — sem orçamento confiável; comparação por meta de item.";
  }
  if (budget.source === "FRAME_ESTIMATE") {
    const idade = budget.ageSeconds ?? 0;
    const aproximado = budget.approxGold ?? 0;
    const alerta = budget.stale
      ? " Quadro com mais de 90s: considerado desatualizado para compra imediata."
      : "";
    return `Ouro estimado (quadro de ${idade}s atrás): ~${aproximado} — aproximado, não é saldo exato.${alerta}`;
  }
  return "Ouro informado manualmente — vale só para este cenário hipotético, nunca como dado real de treino.";
}
