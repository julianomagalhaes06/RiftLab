import { parseRiotId } from "../../shared/router/routes";

/**
 * Servidores do jogo no seletor da busca, como nos sites de estatísticas. Só o BR está ativo:
 * o backend fala apenas com os hosts da Riot do BR (BR1 + Americas), e o modelo foi treinado com
 * partidas BR. Os demais aparecem desabilitados até ganharem suporte no servidor; a TAG padrão
 * de cada um entra junto com esse suporte.
 */
export interface Region {
  id: string;
  /** Sigla curta, mostrada no seletor. */
  label: string;
  name: string;
  /** TAG padrão do Riot ID nesse servidor, usada quando a pessoa digita só o nome. */
  defaultTag: string | null;
  enabled: boolean;
}

export const REGIONS: readonly Region[] = [
  { id: "BR", label: "BR", name: "Brasil", defaultTag: "BR1", enabled: true },
  { id: "LAN", label: "LAN", name: "América Latina Norte", defaultTag: null, enabled: false },
  { id: "LAS", label: "LAS", name: "América Latina Sul", defaultTag: null, enabled: false },
  { id: "NA", label: "NA", name: "América do Norte", defaultTag: null, enabled: false },
  { id: "EUW", label: "EUW", name: "Europa Oeste", defaultTag: null, enabled: false },
  { id: "EUNE", label: "EUNE", name: "Europa Nórdica e Leste", defaultTag: null, enabled: false },
  { id: "TR", label: "TR", name: "Turquia", defaultTag: null, enabled: false },
  { id: "RU", label: "RU", name: "Rússia", defaultTag: null, enabled: false },
  { id: "ME", label: "ME", name: "Oriente Médio", defaultTag: null, enabled: false },
  { id: "KR", label: "KR", name: "Coreia", defaultTag: null, enabled: false },
  { id: "JP", label: "JP", name: "Japão", defaultTag: null, enabled: false },
  { id: "OCE", label: "OCE", name: "Oceania", defaultTag: null, enabled: false },
  { id: "SEA", label: "SEA", name: "Sudeste Asiático", defaultTag: null, enabled: false },
  { id: "TW", label: "TW", name: "Taiwan", defaultTag: null, enabled: false },
  { id: "VN", label: "VN", name: "Vietnã", defaultTag: null, enabled: false },
];

export const DEFAULT_REGION: Region = REGIONS[0];

export function regionById(id: string): Region {
  return REGIONS.find((region) => region.id === id && region.enabled) ?? DEFAULT_REGION;
}

/**
 * Texto digitado → Riot ID. Com TAG ("Nome#TAG"), vale a TAG digitada. Sem TAG ("Nome" ou
 * "Nome#"), usa a TAG padrão do servidor escolhido, como o op.gg faz com o #BR1.
 */
export function resolveRiotId(query: string, region: Region = DEFAULT_REGION): { gameName: string; tagLine: string } | null {
  const text = query.trim();
  const withoutTag = text.endsWith("#") ? text.slice(0, -1).trim() : text;
  if (!withoutTag) return null;
  if (withoutTag.includes("#")) return parseRiotId(withoutTag);
  return region.defaultTag ? parseRiotId(`${withoutTag}#${region.defaultTag}`) : null;
}
