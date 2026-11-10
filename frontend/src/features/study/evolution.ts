import type { LearningAttemptSummary } from "../../shared/api/client";

export interface WeekBucket {
  /** Segunda-feira da semana, AAAA-MM-DD (chave estável, independente de fuso de exibição). */
  weekStart: string;
  attempts: number;
  reflections: number;
}

function mondayKey(iso: string): string {
  const date = new Date(iso);
  const day = date.getDay(); // 0=domingo..6=sábado
  const diffToMonday = day === 0 ? -6 : 1 - day;
  const monday = new Date(date);
  monday.setHours(0, 0, 0, 0);
  monday.setDate(monday.getDate() + diffToMonday);
  return monday.toISOString().slice(0, 10);
}

/** Agrupa tentativas por semana (segunda a domingo), só as `weeks` mais recentes que têm
 * pelo menos uma tentativa, da mais antiga para a mais nova (ordem de leitura do gráfico). */
export function weeklyBuckets(attempts: readonly LearningAttemptSummary[], weeks: number): WeekBucket[] {
  const byWeek = new Map<string, WeekBucket>();
  for (const attempt of attempts) {
    const key = mondayKey(attempt.createdAt);
    const bucket = byWeek.get(key) ?? { weekStart: key, attempts: 0, reflections: 0 };
    bucket.attempts += 1;
    if (attempt.hasReflection) bucket.reflections += 1;
    byWeek.set(key, bucket);
  }
  return [...byWeek.values()].sort((a, b) => a.weekStart.localeCompare(b.weekStart)).slice(-weeks);
}

const MONTHS = [
  "jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez",
] as const;

/** "02–08 nov" (mês uma vez só) ou "30 out–05 nov" quando a semana cruza o mês. */
export function weekLabel(weekStart: string): string {
  const monday = new Date(`${weekStart}T00:00:00`);
  const sunday = new Date(monday);
  sunday.setDate(sunday.getDate() + 6);
  const day = (d: Date) => String(d.getDate()).padStart(2, "0");
  const month = (d: Date) => MONTHS[d.getMonth()];
  if (monday.getMonth() === sunday.getMonth()) {
    return `${day(monday)}–${day(sunday)} ${month(sunday)}`;
  }
  return `${day(monday)} ${month(monday)}–${day(sunday)} ${month(sunday)}`;
}
