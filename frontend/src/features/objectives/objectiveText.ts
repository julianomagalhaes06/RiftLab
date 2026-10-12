import type { ObjectiveForm, ObjectivePriority } from "../../shared/api/client";

/** Rótulos e origem do objetivo (CC-504), compartilhados pelo seletor e pela comparação. */

export const OBJECTIVES: ReadonlyArray<{ value: ObjectivePriority; label: string; hint: string }> = [
  { value: "BALANCED", label: "Equilibrado", hint: "sem preferência por um tipo de atributo" },
  { value: "SURVIVAL", label: "Sobrevivência", hint: "armadura, resistência mágica e vida" },
  { value: "DAMAGE", label: "Dano", hint: "dano, crítico, velocidade de ataque e penetração" },
  { value: "UTILITY", label: "Utilidade", hint: "aceleração de habilidade, mana e mobilidade" },
];

export const TEXT_LIMIT = 500;

export function objectiveLabel(priority: ObjectivePriority | undefined): string {
  return OBJECTIVES.find((o) => o.value === priority)?.label ?? String(priority ?? "—");
}

export function objectiveOrigin(objective: ObjectiveForm): string {
  return objective.parserVersion
    ? `de texto confirmado (interpretador ${objective.parserVersion})`
    : "escolhido no formulário";
}
