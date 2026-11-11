import { useEffect, useState } from "react";

/** Feitiços e runas do Data Dragon em português (summoner.json e runesReforged.json), com cache
 * por versão, como os nomes de item (ItemNames). Falhou ou sem versão: mapas vazios, e a
 * interface mostra a casa vazia com o rótulo genérico. */
export interface GameAsset {
  name: string;
  url: string;
}

export interface GameAssets {
  spells: Record<number, GameAsset>;
  runes: Record<number, GameAsset>;
}

const EMPTY: GameAssets = { spells: {}, runes: {} };
const CDN = "https://ddragon.leagueoflegends.com/cdn";
const cache = new Map<string, Promise<GameAssets>>();

function get(url: string): Promise<unknown> {
  return fetch(url, { credentials: "omit", referrerPolicy: "no-referrer", signal: AbortSignal.timeout(10000) })
    .then((response) => {
      if (!response.ok) throw new Error("Catálogo indisponível");
      return response.json() as Promise<unknown>;
    });
}

const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const safePath = (value: unknown): value is string => typeof value === "string" && /^[\w./-]+\.png$/.test(value) && !value.includes("..");

function parseSpells(body: unknown, version: string): Record<number, GameAsset> {
  const spells: Record<number, GameAsset> = {};
  if (!isRecord(body) || !isRecord(body.data)) return spells;
  for (const spell of Object.values(body.data)) {
    if (!isRecord(spell) || typeof spell.name !== "string" || typeof spell.key !== "string" || !/^\d+$/.test(spell.key)) continue;
    const image = isRecord(spell.image) ? spell.image.full : null;
    if (!safePath(image)) continue;
    spells[Number(spell.key)] = { name: spell.name, url: `${CDN}/${version}/img/spell/${image}` };
  }
  return spells;
}

function parseRunes(body: unknown): Record<number, GameAsset> {
  const runes: Record<number, GameAsset> = {};
  if (!Array.isArray(body)) return runes;
  const add = (entry: unknown) => {
    if (isRecord(entry) && typeof entry.id === "number" && typeof entry.name === "string" && safePath(entry.icon)) {
      runes[entry.id] = { name: entry.name, url: `${CDN}/img/${entry.icon}` };
    }
  };
  for (const style of body) {
    add(style);
    const slots = isRecord(style) && Array.isArray(style.slots) ? style.slots : [];
    for (const slot of slots) for (const rune of isRecord(slot) && Array.isArray(slot.runes) ? slot.runes : []) add(rune);
  }
  return runes;
}

function load(version: string): Promise<GameAssets> {
  const existing = cache.get(version);
  if (existing) return existing;
  const request = Promise.all([
    get(`${CDN}/${version}/data/pt_BR/summoner.json`).then((body) => parseSpells(body, version)),
    get(`${CDN}/${version}/data/pt_BR/runesReforged.json`).then(parseRunes),
  ]).then(([spells, runes]) => ({ spells, runes })).catch((error: unknown) => {
    cache.delete(version);
    throw error;
  });
  cache.set(version, request);
  return request;
}

export function useGameAssets(version: string | null | undefined): GameAssets {
  const [state, setState] = useState<{ version: string; assets: GameAssets } | null>(null);
  useEffect(() => {
    if (!version || !/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(version)) return;
    let active = true;
    load(version).then((assets) => { if (active) setState({ version, assets }); }).catch(() => undefined);
    return () => { active = false; };
  }, [version]);
  return state && state.version === version ? state.assets : EMPTY;
}
