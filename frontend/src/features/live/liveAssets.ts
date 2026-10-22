/** Feitiços, estilos de runa e filas da partida ao vivo (CC-1211). Ícones do Data Dragon por
 * caminho fixo; nomes em pt-BR como no cliente. Id desconhecido fica só com o número. */

const DDRAGON = "https://ddragon.leagueoflegends.com/cdn";

const SPELLS: Record<number, [key: string, name: string]> = {
  1: ["SummonerBoost", "Purificar"],
  3: ["SummonerExhaust", "Exaustão"],
  4: ["SummonerFlash", "Flash"],
  6: ["SummonerHaste", "Fantasma"],
  7: ["SummonerHeal", "Curar"],
  11: ["SummonerSmite", "Golpear"],
  12: ["SummonerTeleport", "Teleporte"],
  13: ["SummonerMana", "Clareza"],
  14: ["SummonerDot", "Incendiar"],
  21: ["SummonerBarrier", "Barreira"],
  32: ["SummonerSnowball", "Marcar"],
};

const STYLES: Record<number, [file: string, name: string]> = {
  8000: ["7201_Precision", "Precisão"],
  8100: ["7200_Domination", "Dominação"],
  8200: ["7202_Sorcery", "Feitiçaria"],
  8300: ["7203_Whimsy", "Inspiração"],
  8400: ["7204_Resolve", "Determinação"],
};

const QUEUES: Record<number, string> = {
  0: "Personalizada",
  400: "Normal (escolha alternada)",
  420: "Ranqueada Solo/Duo",
  430: "Normal (às cegas)",
  440: "Ranqueada Flex",
  450: "ARAM",
  490: "Partida rápida",
  700: "Clash",
  900: "ARURF",
  1700: "Arena",
  1900: "URF",
};

export type LiveIcon = { name: string; src: string | null };

export function spellIcon(id: number | null | undefined, version: string | null | undefined): LiveIcon | null {
  if (id == null) return null;
  const spell = SPELLS[id];
  if (!spell) return { name: `Feitiço ${id}`, src: null };
  return { name: spell[1], src: version ? `${DDRAGON}/${version}/img/spell/${spell[0]}.png` : null };
}

export function styleIcon(id: number | null | undefined): LiveIcon | null {
  if (id == null) return null;
  const style = STYLES[id];
  if (!style) return null;
  return { name: style[1], src: `${DDRAGON}/img/perk-images/Styles/${style[0]}.png` };
}

export function queueName(queueId: number | null | undefined, gameMode: string | null | undefined): string {
  if (queueId != null && QUEUES[queueId]) return QUEUES[queueId];
  if (gameMode === "ARAM") return "ARAM";
  if (gameMode === "CHERRY") return "Arena";
  return "Partida";
}

/** "12:05" a partir do início; antes do fim do carregamento, nulo. */
export function elapsed(startedAt: string | null | undefined, now: number): string | null {
  if (!startedAt) return null;
  const seconds = Math.max(0, Math.floor((now - new Date(startedAt).getTime()) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
