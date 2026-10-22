import { useEffect, useId, useState } from "react";
import { api, type RankedMe, type RankedQueue } from "../../shared/api/client";
import "./ranked.css";
import { preferredQueue } from "./preferredQueue";
import { RankCard, type RankCardChange } from "./RankCard";
import { isTier } from "./tiers";
import { count } from "../../shared/text/count";

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

/** Faixa de variação do card (CC-1203): ganho, perda ou o motivo de não comparar. */
function changeStrip(data: RankedMe["change"]): RankCardChange {
  if (!data) return { tone: "neutral", text: "Uma segunda observação permitirá comparar" };
  if (data.kind === "SAME_DIVISION") {
    const value = data.leaguePointsDelta ?? 0;
    if (value > 0) return { tone: "win", text: `+${value} PDL desde a observação anterior` };
    if (value < 0) return { tone: "loss", text: `${value} PDL desde a observação anterior` };
    return { tone: "neutral", text: "Sem variação de PDL desde a observação anterior" };
  }
  if (data.kind === "TRANSITION") return { tone: "neutral", text: "Mudou de elo ou divisão desde a observação anterior" };
  return { tone: "neutral", text: "Variação não comparável com a observação anterior" };
}

const observedAt = (iso: string) => new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });

export function MyRank({ compact = false }: { compact?: boolean }) {
  const [selection, setSelection] = useState<RankedQueue | null>(null);
  const [results, setResults] = useState<Partial<Record<RankedQueue, { data?: RankedMe; failed?: boolean }>>>({});
  const queueId = useId();
  useEffect(() => {
    const controller = new AbortController();
    for (const queue of ["RANKED_SOLO_5x5", "RANKED_FLEX_SR"] as const) {
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]);
      api.getRankedMe(signal, queue).then((data) => {
        if (data.position && data.position.queue !== queue) throw new Error("Fila da resposta divergente");
        if (!controller.signal.aborted) setResults((current) => ({ ...current, [queue]: { data } }));
      }).catch(() => {
        if (!controller.signal.aborted) setResults((current) => ({ ...current, [queue]: { failed: true } }));
      });
    }
    return () => controller.abort();
  }, []);
  const queue = selection ?? preferredQueue(results.RANKED_SOLO_5x5?.data, results.RANKED_FLEX_SR?.data);
  const data = results[queue]?.data ?? null;
  const error = results[queue]?.failed === true;
  const queueLabel = queue === "RANKED_SOLO_5x5" ? "Solo/Duo" : "Flex";
  const position = data?.position;
  const tier = position?.state === "READY" && isTier(position.tier) ? position.tier : null;
  const headingId = `my-rank-heading-${compact ? "panel" : "page"}`;
  return (
    <div className={`my-rank ${compact ? "my-rank--compact" : ""}`}>
      <RankCard
        size={compact ? "compact" : "large"}
        heading={<h3 id={headingId}>Minha posição</h3>}
        controls={
          <div className="rank-card__controls">
            <label className="sr-only" htmlFor={queueId}>Fila ranqueada</label>
            <select id={queueId} aria-describedby={`${queueId}-hint`} value={queue} onChange={(event) => setSelection(event.target.value as RankedQueue)}>
              <option value="RANKED_SOLO_5x5">Solo/Duo</option>
              <option value="RANKED_FLEX_SR">Flex</option>
            </select>
            <small id={`${queueId}-hint`}>{selection === null ? "Seleção automática · melhor classificação disponível" : "Seleção manual · válida nesta página"}</small>
          </div>
        }
        rank={tier && position ? { tier, division: position.division ?? null, leaguePoints: position.leaguePoints ?? 0 } : null}
        caption={position ? `Fila ${queueLabel} · observado em ${observedAt(position.observedAt)}` : undefined}
        change={tier ? changeStrip(data?.change ?? null) : null}
      >
        {!data && !error && <p className="rank-card__state" role="status">Carregando posição observada…</p>}
        {error && <p className="rank-card__state" role="alert">Não foi possível consultar a posição armazenada.</p>}
        {data?.status === "UNAVAILABLE" && <p className="rank-card__state" role="status">{reasonNames[data.reason ?? ""] ?? "Posição indisponível para esta conta."}</p>}
        {position?.state === "UNRANKED" && <p className="rank-card__state" role="status">Não classificado na {queueLabel} nesta observação válida.</p>}
        {data?.status === "STALE" && <p className="rank-card__note" role="status">Última posição observada. {reasonNames[data.reason ?? ""] ?? "Atualização indisponível."}</p>}
        {position?.origin === "DEMO_FICTITIOUS" && <p className="rank-card__note" role="note">Elo fictício de demonstração: não foi consultado na Riot e não pertence a nenhuma conta.</p>}
      </RankCard>
      {!compact && position ? (
        <div className="my-rank__details surface surface--pad">
          {tier && position.wins != null && position.losses != null && (
            <p className="my-rank__record">{count(position.wins, "vitória", "vitórias")} · {count(position.losses, "derrota", "derrotas")} na fila</p>
          )}
          <p className="my-rank__time">Temporada {position.season} · {position.origin === "DEMO_FICTITIOUS" ? "Demonstração (fictício)" : "Riot League-V4"} · Observado em {new Date(position.observedAt).toLocaleString("pt-BR")}</p>
          {tier && <ChangeLine data={data?.change ?? null} />}
        </div>
      ) : null}
    </div>
  );
}
