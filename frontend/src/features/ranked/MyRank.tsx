import { useEffect, useState } from "react";
import { api, type RankedMe, type RankedQueue } from "../../shared/api/client";
import "./ranked.css";

const tierNames: Record<string, string> = {
  IRON: "Ferro", BRONZE: "Bronze", SILVER: "Prata", GOLD: "Ouro",
  PLATINUM: "Platina", EMERALD: "Esmeralda", DIAMOND: "Diamante",
  MASTER: "Mestre", GRANDMASTER: "Grão-mestre", CHALLENGER: "Desafiante",
};
const reasonNames: Record<string, string> = {
  ACCOUNT_OR_SECRET_MISSING: "Configure a conta local e o segredo de vínculo para consultar sua posição.",
  NO_OBSERVATION: "Ainda não há observação desta fila. Atualize pelo comando administrativo.",
  RIOT_KEY_MISSING: "A chave da Riot está ausente. A última posição observada continua visível.",
  OBSERVATION_OLD: "Esta observação tem mais de 24 horas.",
  RIOT_AUTH_FAILED: "A atualização falhou por autenticação na Riot. A posição anterior foi preservada.",
  RIOT_RATE_LIMITED: "A Riot limitou a atualização. A posição anterior foi preservada.",
  SCHEMA_UNSUPPORTED: "A resposta da Riot não pôde ser validada. A posição anterior foi preservada.",
  UPSTREAM_UNAVAILABLE: "A Riot não respondeu. A posição anterior foi preservada.",
};
const divisionNames: Record<string, string> = { I: "I", II: "II", III: "III", IV: "IV" };
const tierOrder = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER"];
const divisionOrder = ["IV", "III", "II", "I"];
function nextStep(tier: string, division: string | null): string {
  if (!division) return "o próximo elo";
  const index = divisionOrder.indexOf(division);
  if (index >= 0 && index < 3) return `${tierNames[tier]} ${divisionOrder[index + 1]}`;
  const nextTier = tierOrder[tierOrder.indexOf(tier) + 1];
  return nextTier === "MASTER" ? "Mestre" : nextTier ? `${tierNames[nextTier]} IV` : "o próximo elo";
}

function ChangeLine({ data }: { data: RankedMe["change"] }) {
  if (!data) return <p>Uma segunda observação da mesma temporada permitirá comparar a evolução.</p>;
  const since = new Date(data.since).toLocaleString("pt-BR");
  const until = new Date(data.until).toLocaleString("pt-BR");
  let text = "Variação não comparável entre estas observações.";
  if (data.kind === "CORRECTION") text = "Os contadores foram corrigidos; variação não comparável.";
  if (data.kind === "TRANSITION") text = "Houve mudança de elo ou divisão; PDL bruto não é comparável.";
  if (data.kind === "SAME_DIVISION") {
    const value = data.leaguePointsDelta ?? 0;
    text = `${value > 0 ? "+" : ""}${value} PDL desde a observação anterior, na mesma divisão.`;
  }
  const association = data.ownMatchAssociation === "ONE"
    ? "Uma partida própria concluída da fila foi encontrada nesse intervalo; a variação não é atribuída à partida."
    : "A variação cobre o intervalo de atualizações, sem atribuição a uma partida.";
  return <div className="my-rank__change"><p>{text}</p><p>{association}</p><small>De {since} até {until}.</small></div>;
}

export function MyRank({ compact = false }: { compact?: boolean }) {
  const [queue, setQueue] = useState<RankedQueue>("RANKED_SOLO_5x5");
  const [result, setResult] = useState<{ queue: RankedQueue; data: RankedMe } | null>(null);
  const [failedQueue, setFailedQueue] = useState<RankedQueue | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api.getRankedMe(controller.signal, queue).then((data) => {
      if (!controller.signal.aborted) {
        setResult({ queue, data });
        setFailedQueue(null);
      }
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted && !(cause instanceof DOMException && cause.name === "AbortError")) setFailedQueue(queue);
    });
    return () => controller.abort();
  }, [queue]);
  const data = result?.queue === queue ? result.data : null;
  const error = failedQueue === queue;
  const queueLabel = queue === "RANKED_SOLO_5x5" ? "Solo/Duo" : "Flex";
  const position = data?.position;
  const tier = position?.tier && tierNames[position.tier] ? position.tier : null;
  const apex = tier ? ["MASTER", "GRANDMASTER", "CHALLENGER"].includes(tier) : false;
  const title = tier ? `${tierNames[tier]}${position?.division ? ` ${divisionNames[position.division]}` : ""}` : "";
  return (
    <section className={`my-rank ${compact ? "my-rank--compact" : ""}`} aria-labelledby={`my-rank-heading-${compact ? "panel" : "page"}`} data-tier={tier ?? "NONE"}>
      <div className="my-rank__copy">
        <p className="my-rank__eyebrow">Fila {queueLabel} · BR1</p>
        <h3 id={`my-rank-heading-${compact ? "panel" : "page"}`}>Minha posição</h3>
        <label className="my-rank__queue">
          Fila ranqueada
          <select value={queue} onChange={(event) => setQueue(event.target.value as RankedQueue)}>
            <option value="RANKED_SOLO_5x5">Solo/Duo</option>
            <option value="RANKED_FLEX_SR">Flex</option>
          </select>
        </label>
        {!data && !error && <p role="status">Carregando posição observada…</p>}
        {error && <p role="alert">Não foi possível consultar a posição armazenada.</p>}
        {data?.status === "UNAVAILABLE" && <p role="status">{reasonNames[data.reason ?? ""] ?? "Posição indisponível para esta conta."}</p>}
        {position?.state === "UNRANKED" && <p role="status">Não classificado na {queueLabel} nesta observação válida.</p>}
        {position?.state === "READY" && <>
          <p className="sr-only">Elo {title}, {position.leaguePoints}{apex ? "" : " de 100"} PDL</p>
          <p className="my-rank__title" aria-hidden="true">{title} <strong>{position.leaguePoints} <span>PDL</span></strong></p>
          <p className="my-rank__step">{apex ? "Sem divisões · PDL sem teto" : `Faltam ${Math.max(0, 100 - (position.leaguePoints ?? 0))} PDL para ${nextStep(tier ?? "", position.division)}`}</p>
          {!apex && <div className="my-rank__track" role="img" aria-label={`${position.leaguePoints} de 100 pontos de liga para a próxima divisão`}>
            <span style={{ width: `${Math.min(position.leaguePoints ?? 0, 100)}%` }} />
          </div>}
          <p className="my-rank__record">{position.wins} vitórias · {position.losses} derrotas na fila</p>
        </>}
        {data?.status === "STALE" && <p className="my-rank__stale" role="status">Última posição observada. {reasonNames[data.reason ?? ""] ?? "Atualização indisponível."}</p>}
        {position?.origin === "DEMO_FICTITIOUS" && <p className="my-rank__stale" role="note">Elo fictício de demonstração: não foi consultado na Riot e não pertence a nenhuma conta.</p>}
        {position && <p className="my-rank__time">Temporada {position.season} · {position.origin === "DEMO_FICTITIOUS" ? "Demonstração (fictício)" : "Riot League-V4"} · Observado em {new Date(position.observedAt).toLocaleString("pt-BR")}</p>}
        {position?.state === "READY" && <ChangeLine data={data?.change ?? null} />}
      </div>
      <div className="my-rank__emblem" aria-hidden="true">{tier ? <img src={`/ranked-emblems/${tier.toLowerCase()}.webp`} alt="" width="180" height="180" /> : <span>RL</span>}</div>
    </section>
  );
}
