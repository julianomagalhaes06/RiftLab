/**
 * Splash arts do início: skins oficiais servidas pelo Data Dragon (CDN pública da Riot, a mesma
 * origem dos retratos e ícones do app). Uma por carregamento da página, sorteada. Lista curada à
 * mão; cada endereço foi conferido no Data Dragon 16.22.1 (os de skins recentes ainda não
 * existem lá e respondem 403). Nomes em pt-BR, do próprio Data Dragon.
 */
export interface Splash {
  /** Chave do campeão no Data Dragon (ex.: "MissFortune"). */
  key: string;
  num: number;
  champion: string;
  skin: string;
}

export const SPLASHES: readonly Splash[] = [
  { key: "Ahri", num: 27, champion: "Ahri", skin: "Ahri Florescer Espiritual" },
  { key: "Ahri", num: 66, champion: "Ahri", skin: "Ahri Arcana" },
  { key: "Ahri", num: 86, champion: "Ahri", skin: "Ahri Lenda Imortalizada" },
  { key: "Akali", num: 15, champion: "Akali", skin: "Akali True Damage" },
  { key: "Akali", num: 61, champion: "Akali", skin: "Akali Guardiã Estelar" },
  { key: "Akali", num: 71, champion: "Akali", skin: "Akali Congregação das Bruxas de Prestígio" },
  { key: "Aphelios", num: 9, champion: "Aphelios", skin: "Aphelios Fera Lunar" },
  { key: "Ashe", num: 11, champion: "Ashe", skin: "Ashe Rainha Cósmica" },
  { key: "Ashe", num: 76, champion: "Ashe", skin: "Ashe Florescer Espiritual" },
  { key: "Caitlyn", num: 51, champion: "Caitlyn", skin: "Caitlyn Comandante de Arcane de Prestígio" },
  { key: "Darius", num: 15, champion: "Darius", skin: "Deus-Rei Darius" },
  { key: "Diana", num: 65, champion: "Diana", skin: "Diana Cosmos Negro de Prestígio" },
  { key: "Ekko", num: 3, champion: "Ekko", skin: "PROJETO: Ekko" },
  { key: "Ekko", num: 45, champion: "Ekko", skin: "Ekko Guardião Estelar" },
  { key: "Ezreal", num: 18, champion: "Ezreal", skin: "Ezreal Guardião Estelar" },
  { key: "Ezreal", num: 54, champion: "Ezreal", skin: "Ezreal Escamas Celestiais de Prestígio" },
  { key: "Garen", num: 13, champion: "Garen", skin: "Deus-Rei Garen" },
  { key: "Gwen", num: 20, champion: "Gwen", skin: "Gwen Soul Fighter" },
  { key: "Irelia", num: 17, champion: "Irelia", skin: "PROJETO: Irelia de Prestígio" },
  { key: "Jhin", num: 5, champion: "Jhin", skin: "Jhin Cosmos Negro" },
  { key: "Jhin", num: 55, champion: "Jhin", skin: "Jhin Arcana" },
  { key: "Jinx", num: 13, champion: "Jinx", skin: "Jinx Odisseia" },
  { key: "Jinx", num: 60, champion: "Jinx", skin: "Jinx Fragmentada de Arcane" },
  { key: "Kaisa", num: 15, champion: "Kai'Sa", skin: "Kai'Sa K/DA de Prestígio" },
  { key: "Kaisa", num: 40, champion: "Kai'Sa", skin: "Kai'Sa Guardiã Estelar" },
  { key: "Katarina", num: 48, champion: "Katarina", skin: "Katarina Corte das Fadas de Prestígio" },
  { key: "Kayn", num: 9, champion: "Kayn", skin: "Kayn Emissário da Escuridão de Prestígio" },
  { key: "LeeSin", num: 11, champion: "Lee Sin", skin: "Lee Sin Punhos Divinos" },
  { key: "LeeSin", num: 31, champion: "Lee Sin", skin: "Lee Sin Dragão da Tormenta" },
  { key: "Leona", num: 10, champion: "Leona", skin: "Leona Eclipse Solar" },
  { key: "Lillia", num: 1, champion: "Lillia", skin: "Lillia Florescer Espiritual" },
  { key: "Lux", num: 7, champion: "Lux", skin: "Lux Elementalista" },
  { key: "Lux", num: 18, champion: "Lux", skin: "Lux Cósmica" },
  { key: "MissFortune", num: 16, champion: "Miss Fortune", skin: "Miss Fortune Vingadora Exocósmica" },
  { key: "Pyke", num: 53, champion: "Pyke", skin: "Pyke Soul Fighter" },
  { key: "Riven", num: 16, champion: "Riven", skin: "Riven Emissária da Luz" },
  { key: "Samira", num: 1, champion: "Samira", skin: "Samira PsyOps" },
  { key: "Senna", num: 27, champion: "Senna", skin: "Senna Eclipse Lunar de Prestígio" },
  { key: "Seraphine", num: 34, champion: "Seraphine", skin: "Seraphine Guardiã Estelar" },
  { key: "Sett", num: 9, champion: "Sett", skin: "Sett Dragão de Obsidiana de Prestígio" },
  { key: "Sylas", num: 14, champion: "Sylas", skin: "PROJETO: Sylas de Prestígio" },
  { key: "Thresh", num: 14, champion: "Thresh", skin: "Pulsefire Thresh de Prestígio" },
  { key: "Thresh", num: 27, champion: "Thresh", skin: "Thresh Liberto" },
  { key: "Vayne", num: 13, champion: "Vayne", skin: "Vayne Fogos de Artifício de Prestígio" },
  { key: "Vi", num: 48, champion: "Vi", skin: "Vi Lutadora de Arcane" },
  { key: "Viego", num: 21, champion: "Viego", skin: "Rei Viego" },
  { key: "Yasuo", num: 36, champion: "Yasuo", skin: "Yasuo Florescer Espiritual" },
  { key: "Yasuo", num: 54, champion: "Yasuo", skin: "Yasuo Dragão da Verdade" },
  { key: "Yone", num: 1, champion: "Yone", skin: "Yone Florescer Espiritual" },
  { key: "Zed", num: 13, champion: "Zed", skin: "Zed Dizimador de Galáxias" },
];

const SPLASH_BASE = "https://ddragon.leagueoflegends.com/cdn/img/champion/splash";

export function splashUrl(splash: Splash): string {
  return `${SPLASH_BASE}/${splash.key}_${splash.num}.jpg`;
}

/** Sorteia uma skin; `random` injetável para teste. */
export function pickSplash(random: () => number = Math.random): Splash {
  const index = Math.min(SPLASHES.length - 1, Math.floor(random() * SPLASHES.length));
  return SPLASHES[index];
}
