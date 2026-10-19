/** Quantidade com o substantivo concordando: "1 partida", "0 partidas", "1.250 partidas". */
export function count(value: number, singular: string, plural: string): string {
  return `${value.toLocaleString("pt-BR")} ${value === 1 ? singular : plural}`;
}
