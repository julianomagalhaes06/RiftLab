import { useEffect, useState, type ReactNode } from "react";
import { ItemNamesContext, type ItemNamesMap as Names } from "../hooks/useItemName";

const catalogs = new Map<string, Promise<Names>>();

function loadNames(version: string): Promise<Names> {
  const existing = catalogs.get(version);
  if (existing) return existing;
  const request = fetch(`https://ddragon.leagueoflegends.com/cdn/${version}/data/pt_BR/item.json`, {
    credentials: "omit",
    referrerPolicy: "no-referrer",
    signal: AbortSignal.timeout(10000),
  }).then(async (response) => {
    if (!response.ok) throw new Error("Catálogo em português indisponível");
    const body: unknown = await response.json();
    if (!body || typeof body !== "object" || !("version" in body) || body.version !== version || !("data" in body)
      || !body.data || typeof body.data !== "object" || Array.isArray(body.data)) {
      throw new Error("Catálogo em português inválido");
    }
    const names: Record<string, string> = {};
    for (const [itemId, item] of Object.entries(body.data)) {
      if (/^[1-9]\d*$/.test(itemId) && item && typeof item === "object" && "name" in item
        && typeof item.name === "string" && item.name.trim()) names[itemId] = item.name;
    }
    return names;
  }).catch((error: unknown) => {
    catalogs.delete(version);
    throw error;
  });
  catalogs.set(version, request);
  return request;
}

export function ItemNames({ version, children }: { version?: string | null; children: ReactNode }) {
  const [result, setResult] = useState<{ version: string; names: Names; failed?: boolean } | null>(null);
  useEffect(() => {
    if (!version || !/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(version)) return;
    let active = true;
    loadNames(version).then((names) => {
      if (active) setResult({ version, names });
    }).catch(() => {
      if (active) setResult({ version, names: {}, failed: true });
    });
    return () => { active = false; };
  }, [version]);
  return <ItemNamesContext value={result && result.version === version ? result.names : {}}>
    {result?.version === version && result?.failed ? <span className="muted" role="note">Tradução dos itens indisponível; exibindo os nomes originais do catálogo. </span> : null}
    {children}
  </ItemNamesContext>;
}
